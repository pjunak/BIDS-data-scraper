import http.client
import http.server
import importlib.util
import threading
import unittest
from pathlib import Path
from urllib.parse import urlencode

from helpers import ROOT

_spec = importlib.util.spec_from_file_location("auth", Path(ROOT) / "web" / "auth.py")
auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auth)

USER_PW, ADMIN_PW = "uzivatel-heslo", "spravce-heslo"
ROLES = {"user": auth.hash_password(USER_PW), "admin": auth.hash_password(ADMIN_PW)}


class PasswordTest(unittest.TestCase):
    def test_hash_roundtrip_and_no_dollar(self):
        h = ROLES["user"]
        self.assertTrue(auth.verify_password(USER_PW, h))
        self.assertFalse(auth.verify_password("jine", h))
        self.assertNotIn("$", h, "the hash must not need quoting in a Compose .env")

    def test_password_alone_picks_the_role(self):
        self.assertEqual(auth.login(USER_PW, ROLES), "user")
        self.assertEqual(auth.login(ADMIN_PW, ROLES), "admin")
        self.assertIsNone(auth.login("spatne", ROLES))

    def test_roles_from_environment(self):
        env = {"BIDS_PASSWORD_HASH_USER": ROLES["user"], "BIDS_PASSWORD_HASH_ADMIN": "", "PATH": "x"}
        self.assertEqual(auth.load_roles(env), {"user": ROLES["user"]})
        with self.assertRaises(SystemExit):
            auth.load_roles({})
        with self.assertRaisesRegex(SystemExit, "BIDS_PASSWORD_HASH_USER"):
            auth.load_roles({"BIDS_PASSWORD_HASH_USER": "$2a$14$bcrypt"})


class SessionTest(unittest.TestCase):
    def test_valid_session(self):
        s = auth.make_session("user", ROLES, now=1000)
        self.assertEqual(auth.check_session(s, ROLES, now=1001), "user")

    def test_expired_tampered_and_foreign_sessions_fail(self):
        s = auth.make_session("user", ROLES, now=1000)
        self.assertIsNone(auth.check_session(s, ROLES, now=1000 + auth.SESSION_S + 1))
        role, expires, sig = s.split(".")
        self.assertIsNone(auth.check_session(f"admin.{expires}.{sig}", ROLES, now=1001))
        self.assertIsNone(auth.check_session(f"user.{int(expires) + 999}.{sig}", ROLES, now=1001))
        for junk in ("", "x", "a.b.c.d", "user.abc.sig"):
            self.assertIsNone(auth.check_session(junk, ROLES, now=1001))

    def test_changing_a_password_signs_that_role_out(self):
        s = auth.make_session("user", ROLES, now=1000)
        changed = dict(ROLES, user=auth.hash_password("nove-heslo-123"))
        self.assertIsNone(auth.check_session(s, changed, now=1001))
        a = auth.make_session("admin", ROLES, now=1000)
        self.assertEqual(auth.check_session(a, changed, now=1001), "admin")

    def test_safe_next_keeps_redirects_on_site(self):
        self.assertEqual(auth.safe_next("/db/collector?sql=select%201"), "/db/collector?sql=select%201")
        for bad in ("https://evil.example", "//evil.example", "/\\evil", "/x\r\nSet-Cookie: a=b", ""):
            self.assertEqual(auth.safe_next(bad), "/")


class ServiceTest(unittest.TestCase):
    """The real HTTP handler, as nginx talks to it."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), auth.make_handler(ROLES))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def request(self, method, path, body=None, cookie=None):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        headers = {"Content-Type": "application/x-www-form-urlencoded"} if body else {}
        if cookie:
            headers["Cookie"] = cookie
        con.request(method, path, body=body, headers=headers)
        r = con.getresponse()
        r.read()
        con.close()
        return r

    def test_login_check_logout(self):
        self.assertEqual(self.request("GET", "/auth/check").status, 401)

        bad = self.request("POST", "/auth/login", urlencode({"password": "spatne", "next": "/logs/"}))
        self.assertEqual((bad.status, bad.getheader("Location")), (303, "/login?error=1&next=/logs/"))
        self.assertIsNone(bad.getheader("Set-Cookie"))

        ok = self.request("POST", "/auth/login", urlencode({"password": USER_PW, "next": "/db/collector"}))
        self.assertEqual((ok.status, ok.getheader("Location")), (303, "/db/collector"))
        set_cookie = ok.getheader("Set-Cookie")
        for attr in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
            self.assertIn(attr, set_cookie)
        cookie = set_cookie.split(";")[0]

        check = self.request("GET", "/auth/check", cookie=cookie)
        self.assertEqual((check.status, check.getheader("X-Bids-Role")), (204, "user"))

        out = self.request("GET", "/auth/logout", cookie=cookie)
        self.assertEqual((out.status, out.getheader("Location")), (303, "/login"))
        self.assertIn("Max-Age=0", out.getheader("Set-Cookie"))

    def test_open_redirect_is_refused(self):
        r = self.request("POST", "/auth/login", urlencode({"password": USER_PW, "next": "//evil.example/"}))
        self.assertEqual(r.getheader("Location"), "/")

    def test_oversized_form_is_rejected(self):
        self.assertEqual(self.request("POST", "/auth/login", "password=" + "x" * 5000).status, 413)


if __name__ == "__main__":
    unittest.main()
