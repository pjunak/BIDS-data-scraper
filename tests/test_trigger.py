import http.client
import json
import threading
import unittest

import helpers  # noqa: F401  (sets sys.path)
from collector import trigger


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class TriggerTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.trig = trigger.Trigger(cooldown_s=3600, clock=self.clock)

    def test_request_wakes_the_daemon_once(self):
        self.assertEqual(self.trig.state(), {"running": False, "next_allowed": None})
        status, state = self.trig.request("user")
        self.assertEqual(status, 202)
        self.assertTrue(state["running"])
        self.assertTrue(self.trig.wait(0))
        self.assertEqual(self.trig.request("user")[0], 409, "already queued")

    def test_cooldown_after_a_manual_run(self):
        self.trig.request("user")
        self.trig.run(lambda: None)
        self.clock.t += 600
        status, state = self.trig.request("user")
        self.assertEqual(status, 429)
        self.assertFalse(state["running"])
        self.assertEqual(state["next_allowed"], "1970-01-12T14:46:40+00:00")
        self.clock.t += 3000
        self.assertEqual(self.trig.request("user")[0], 202)

    def test_no_manual_run_while_the_nightly_scrape_runs(self):
        seen = []

        def nightly():
            seen.append(self.trig.request("user"))

        self.trig.run(nightly)
        self.assertEqual(seen[0][0], 409)
        self.assertFalse(self.trig.wait(0), "the rejected request must not queue a run")

    def test_run_clears_the_request_and_survives_a_failing_job(self):
        self.trig.request("user")

        def boom():
            raise RuntimeError("portal down")

        with self.assertRaises(RuntimeError):
            self.trig.run(boom)
        self.assertFalse(self.trig.state()["running"])
        self.assertFalse(self.trig.wait(0))


class TriggerHttpTest(unittest.TestCase):
    """The endpoint nginx proxies /api/run to."""

    def setUp(self):
        self.trig = trigger.Trigger(cooldown_s=3600)
        self.server = trigger.serve(self.trig, 0)

    def tearDown(self):
        self.server.shutdown()

    def call(self, method, path="/api/run"):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        con.request(method, path, headers={"X-Bids-Role": "user"})
        r = con.getresponse()
        body = r.read()
        con.close()
        return r.status, (json.loads(body) if r.getheader("Content-Type") == "application/json" else None)

    def test_get_state_post_request_and_conflict(self):
        self.assertEqual(self.call("GET"), (200, {"running": False, "next_allowed": None}))
        status, state = self.call("POST")
        self.assertEqual((status, state["running"]), (202, True))
        self.assertEqual(self.call("POST")[0], 409)
        self.assertEqual(self.call("GET", "/other")[0], 404)

    def test_daemon_loop_picks_up_the_request(self):
        ran = threading.Event()

        def loop():
            if self.trig.wait(5):
                self.trig.run(ran.set)

        t = threading.Thread(target=loop)
        t.start()
        self.call("POST")
        t.join(5)
        self.assertTrue(ran.is_set())
        self.assertFalse(self.call("GET")[1]["running"])


if __name__ == "__main__":
    unittest.main()
