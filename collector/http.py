"""HTTP client shared by all sources: retries on server errors and a polite pause between requests."""
import random
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class Http:
    """A requests session that waits about `delay_s` between requests and retries 429/5xx with backoff."""

    def __init__(self, user_agent: str, delay_s: float = 1.0, timeout_s: float = 30):
        self.delay_s = delay_s
        self.timeout_s = timeout_s
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Language": "cs,en;q=0.8"})
        retry = Retry(total=5, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "POST"), respect_retry_after_header=True)
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))

    def _pause(self):
        """Keep a randomised gap between requests, so the collector never hammers a portal."""
        wait = self.delay_s * random.uniform(0.8, 1.4) - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def get(self, url: str, **kwargs) -> requests.Response:
        """GET that raises on an HTTP error status. Pass stream=True for large files."""
        self._pause()
        response = self.session.get(url, timeout=self.timeout_s, **kwargs)
        response.raise_for_status()
        return response

    def post_json(self, url: str, payload: dict, **kwargs):
        """POST a JSON body and return the decoded JSON answer."""
        self._pause()
        response = self.session.post(url, json=payload, timeout=self.timeout_s, **kwargs)
        response.raise_for_status()
        return response.json()
