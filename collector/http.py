import logging
import random
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)


class Http:
    """requests.Session with retries and a polite delay between requests."""

    def __init__(self, user_agent: str, delay_s: float = 1.0, timeout_s: float = 30):
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self._last = 0.0
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Language": "cs,en;q=0.8"})
        retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"), respect_retry_after_header=True)
        self.s.mount("https://", HTTPAdapter(max_retries=retry))
        self.s.mount("http://", HTTPAdapter(max_retries=retry))

    def _wait(self):
        pause = self.delay_s * random.uniform(0.8, 1.4) - (time.monotonic() - self._last)
        if pause > 0:
            time.sleep(pause)
        self._last = time.monotonic()

    def get(self, url, **kw):
        self._wait()
        r = self.s.get(url, timeout=self.timeout_s, **kw)
        r.raise_for_status()
        return r

    def post_json(self, url, payload, **kw):
        self._wait()
        r = self.s.post(url, json=payload, timeout=self.timeout_s, **kw)
        r.raise_for_status()
        return r.json()
