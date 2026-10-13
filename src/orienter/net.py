import time

import httpx


class Throttle:
    """Keep at least `interval` seconds between calls (free APIs have per-minute limits)."""

    def __init__(self, interval: float):
        self.interval = interval
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + self.interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def client(user_agent: str, timeout: float = 30) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True)
