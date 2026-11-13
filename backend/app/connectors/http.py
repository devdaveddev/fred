"""One small HTTP client for every connector: retries on 429/5xx with backoff, readable errors, test transport."""
from __future__ import annotations

import time

import httpx

transport: httpx.BaseTransport | None = None  # tests swap in httpx.MockTransport
sleep = time.sleep


class SourceError(RuntimeError):
    """A problem the user can act on (bad credentials, missing permission). Shown as-is."""


class Api:
    def __init__(self, name: str, base: str = "", headers: dict | None = None, auth=None, hints: dict | None = None):
        self.name = name
        self.hints = {401: f"{name} rejected the credentials. Reconnect it.",
                      403: f"{name} refused access: the connection is missing a permission."} | (hints or {})
        self.http = httpx.Client(base_url=base, headers=headers or {}, auth=auth, timeout=40, transport=transport)

    def request(self, method: str, url: str, **kw) -> httpx.Response:
        for attempt in range(5):
            try:
                r = self.http.request(method, url, **kw)
            except httpx.TransportError as e:
                if attempt == 4:
                    raise SourceError(f"Could not reach {self.name}: {type(e).__name__}") from e
                sleep(min(2 ** attempt, 20))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                wait = r.headers.get("retry-after")
                sleep(min(float(wait) if wait and wait.replace(".", "").isdigit() else 2 ** attempt, 30))
                continue
            if r.status_code in self.hints:
                raise SourceError(self.hints[r.status_code])
            if r.status_code >= 400:
                raise SourceError(f"{self.name} error {r.status_code}: {r.text[:200]}")
            return r
        raise SourceError(f"{self.name} kept rate-limiting the sync; try again in a few minutes.")

    def get(self, url: str, **kw) -> dict:
        return self.request("GET", url, **kw).json()

    def post(self, url: str, **kw) -> dict:
        r = self.request("POST", url, **kw)
        return r.json() if r.content else {}

    def patch(self, url: str, **kw) -> dict:
        r = self.request("PATCH", url, **kw)
        return r.json() if r.content else {}
