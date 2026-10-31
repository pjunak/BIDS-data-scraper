"""Password-only login for bids.junak.eu (standard library only).

nginx asks GET /auth/check before every request (auth_request): 204 for a valid session cookie,
401 otherwise, which nginx turns into a redirect to /login. The password alone decides the role:
every BIDS_PASSWORD_HASH_<ROLE> environment variable holds one password hash, so a later admin
role is just another variable. Sessions are signed with the role's password hash, so changing a
password signs that role out everywhere.

    python web/auth.py hash     # prompt for a password (or read it from stdin), print its hash
    python web/auth.py serve    # run the service on :8081
"""
import base64
import binascii
import getpass
import hashlib
import hmac
import http.server
import logging
import os
import secrets
import sys
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs

log = logging.getLogger("auth")

PREFIX = "BIDS_PASSWORD_HASH_"
COOKIE = "bids_session"
SESSION_S = 30 * 86400
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
MAX_FORM = 4096


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    """'scrypt:n:r:p:salt:hash' – no '$', so it needs no quoting in a Compose .env file."""
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt:{SCRYPT['n']}:{SCRYPT['r']}:{SCRYPT['p']}:{_b64(salt)}:{_b64(dk)}"


def _parse_hash(stored: str):
    parts = stored.split(":")
    if len(parts) != 6 or parts[0] != "scrypt" or not all(p.isdigit() for p in parts[1:4]):
        raise ValueError("not a scrypt hash from `auth.py hash`")
    return int(parts[1]), int(parts[2]), int(parts[3]), _unb64(parts[4]), _unb64(parts[5])


def verify_password(password: str, stored: str) -> bool:
    n, r, p, salt, dk = _parse_hash(stored)
    return hmac.compare_digest(hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p), dk)


def load_roles(environ) -> dict[str, str]:
    """{role: password hash} from BIDS_PASSWORD_HASH_<ROLE>; refuses to start without a valid one."""
    roles = {k[len(PREFIX):].lower(): v.strip() for k, v in environ.items() if k.startswith(PREFIX) and v.strip()}
    if not roles:
        raise SystemExit(f"auth: set at least one {PREFIX}<ROLE> (create it with `auth.py hash`)")
    for role, stored in roles.items():
        try:
            _parse_hash(stored)
        except (ValueError, binascii.Error) as e:
            raise SystemExit(f"auth: {PREFIX}{role.upper()}: {e}")
    return roles


def login(password: str, roles: dict[str, str]) -> str | None:
    for role, stored in roles.items():
        if verify_password(password, stored):
            return role
    return None


def _sign(role: str, expires: int, stored: str) -> str:
    return _b64(hmac.new(stored.encode(), f"{role}.{expires}".encode(), "sha256").digest())


def make_session(role: str, roles: dict[str, str], now: float) -> str:
    expires = int(now) + SESSION_S
    return f"{role}.{expires}.{_sign(role, expires, roles[role])}"


def check_session(value: str, roles: dict[str, str], now: float) -> str | None:
    """The role of a valid, unexpired session cookie, else None."""
    parts = value.split(".")
    if len(parts) != 3 or parts[0] not in roles or not parts[1].isdigit() or int(parts[1]) < now:
        return None
    role, expires = parts[0], int(parts[1])
    return role if hmac.compare_digest(parts[2], _sign(role, expires, roles[role])) else None


def safe_next(target: str) -> str:
    """Only same-site paths, so the login form cannot be used to redirect elsewhere."""
    if not target.startswith("/") or target.startswith("//") or any(c in target for c in "\\\r\n"):
        return "/"
    return target


def make_handler(roles: dict[str, str]):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _cookie(self) -> str:
            try:
                morsel = SimpleCookie(self.headers.get("Cookie", "")).get(COOKIE)
            except CookieError:
                return ""
            return morsel.value if morsel else ""

        def _redirect(self, location: str, cookie: str | None = None):
            self.send_response(303)
            self.send_header("Location", location)
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/auth/check":
                role = check_session(self._cookie(), roles, time.time())
                self.send_response(204 if role else 401)
                if role:
                    self.send_header("X-Bids-Role", role)
                self.end_headers()
            elif path == "/auth/logout":
                self._redirect("/login", f"{COOKIE}=; Max-Age=0; Path=/; HttpOnly; Secure; SameSite=Lax")
            elif path == "/auth/health":
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path.split("?")[0] != "/auth/login":
                return self.send_error(404)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_FORM:
                self.rfile.read(min(length, 1024 * 1024))  # drain (nginx caps bodies at 1 MB) so the reply arrives
                return self.send_error(413)
            form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
            target = safe_next(form.get("next", ["/"])[0])
            role = login(form.get("password", [""])[0], roles)
            if not role:
                log.warning("failed login from %s", self.headers.get("X-Real-IP", self.client_address[0]))
                return self._redirect(f"/login?error=1&next={target}")
            cookie = f"{COOKIE}={make_session(role, roles, time.time())}; Max-Age={SESSION_S}; Path=/; HttpOnly; Secure; SameSite=Lax"
            log.info("login as %s", role)
            self._redirect(target, cookie)

        def log_message(self, fmt, *args):
            pass  # nginx sees every request; only logins are worth logging here

    return Handler


def serve(port: int = 8081):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    roles = load_roles(os.environ)
    log.info("auth listening on :%s, roles: %s", port, ", ".join(sorted(roles)))
    http.server.ThreadingHTTPServer(("0.0.0.0", port), make_handler(roles)).serve_forever()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "hash":
        if sys.stdin.isatty():
            password = getpass.getpass("Heslo: ")
            if password != getpass.getpass("Znovu: "):
                raise SystemExit("Hesla se neshodují.")
        else:
            password = sys.stdin.readline().rstrip("\n")
        if len(password) < 8:
            raise SystemExit("Heslo musí mít aspoň 8 znaků.")
        print(hash_password(password))
    elif cmd == "serve":
        serve()
    else:
        raise SystemExit(f"usage: {argv[0]} hash|serve")


if __name__ == "__main__":
    main(sys.argv)
