"""Manual "run now" requests from the dashboard (button Spustit sběr).

The daemon serves GET/POST /api/run on its own small HTTP port, reachable only from nginx inside the
stack (behind the login). A request only wakes the daemon's main loop, which runs the scrape itself,
so manual and nightly runs never overlap. At most one manual run per cooldown, to spare the portals.
"""
import http.server
import json
import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class Trigger:
    def __init__(self, cooldown_s: float = 3600, clock=time.time):
        self.cooldown_s = cooldown_s
        self.clock = clock
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._busy = False
        self._last_request = None

    def state(self) -> dict:
        nxt = None
        if self._last_request is not None and self.clock() - self._last_request < self.cooldown_s:
            nxt = datetime.fromtimestamp(self._last_request + self.cooldown_s, timezone.utc).isoformat(timespec="seconds")
        return {"running": self._busy or self._event.is_set(), "next_allowed": nxt}

    def request(self, who: str) -> tuple[int, dict]:
        """202 accepted, 409 a run is already running or queued, 429 within the cooldown."""
        with self._lock:
            state = self.state()
            if state["running"]:
                return 409, state
            if state["next_allowed"]:
                return 429, state
            self._last_request = self.clock()
            self._event.set()
            log.info("manual run requested (%s)", who or "?")
            return 202, self.state()

    def wait(self, timeout: float) -> bool:
        """Sleep up to timeout; True when a manual run was requested."""
        return self._event.wait(timeout)

    def run(self, job):
        """Run a scrape (manual or nightly) and show it as running meanwhile."""
        with self._lock:
            self._busy = True
            self._event.clear()
        try:
            job()
        finally:
            with self._lock:
                self._busy = False


def make_handler(trigger: Trigger):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _json(self, status: int, body: dict):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.split("?")[0] != "/api/run":
                return self.send_error(404)
            self._json(200, trigger.state())

        def do_POST(self):
            if self.path.split("?")[0] != "/api/run":
                return self.send_error(404)
            self._json(*trigger.request(self.headers.get("X-Bids-Role", "")))

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(trigger: Trigger, port: int) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), make_handler(trigger))
    threading.Thread(target=server.serve_forever, daemon=True, name="trigger").start()
    log.info("manual runs accepted on :%s", port)
    return server
