"""Thin Graph client: bearer auth, retries/backoff, paging, typed errors (FR-201, P2-D-118).

Rules enforced here so no job can break them:
  * the Authorization header is only ever sent to the configured Graph host (nextLinks are checked);
  * file bytes are never requested (paths ending in /content and downloadUrl hosts are refused, P2-D-106);
  * logs carry endpoint path, status and Graph error code only, never tokens, query strings or bodies.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp

from .auth import GraphAuthError, TokenProvider

log = logging.getLogger("skim.graph")

__all__ = ["GraphAuthError", "GraphClient", "GraphError", "GraphGone", "GraphNotFound", "GraphServerError", "GraphThrottled"]


class GraphError(Exception):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None, endpoint: str | None = None):
        super().__init__(message)
        self.status, self.code, self.endpoint = status, code, endpoint


class GraphNotFound(GraphError): ...
class GraphGone(GraphError): ...        # 410: delta token expired, resync needed
class GraphServerError(GraphError): ...  # 5xx after retries


class GraphThrottled(GraphError):
    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any):
        super().__init__(message, **kw)
        self.retry_after = retry_after


def _error_info(body: Any) -> tuple[str | None, str]:
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return err.get("code"), (err.get("message") or "")[:200]
    return None, ""


class GraphClient:
    def __init__(self, tokens: TokenProvider, *, base_url: str = "https://graph.microsoft.com/v1.0",
                 max_concurrency: int = 4, max_retries: int = 4, backoff_base: float = 0.5, max_wait: float = 60.0,
                 session: aiohttp.ClientSession | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 jitter: Callable[[], float] = random.random):
        self.tokens = tokens
        self.base_url = base_url.rstrip("/")
        self._host = urlsplit(self.base_url).netloc
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self.max_retries, self.backoff_base, self.max_wait = max_retries, backoff_base, max_wait
        self._session, self._own_session = session, session is None
        self._sleep, self._jitter = sleep, jitter
        self.stats = {"requests": 0, "retries": 0, "throttled": 0}

    async def close(self) -> None:
        if self._session and self._own_session:
            await self._session.close()

    # ------------------------------------------------------------------ requests
    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            if urlsplit(path_or_url).netloc != self._host:
                raise GraphError("refusing to send credentials to a host other than the Graph host")
            url = path_or_url
        else:
            url = self.base_url + "/" + path_or_url.lstrip("/")
        parts = urlsplit(url)
        if parts.path.rstrip("/").endswith("/content") or "downloadurl" in url.lower():
            raise ValueError("file content is never requested (P2-D-106)")
        return url

    @staticmethod
    def endpoint(url: str) -> str:
        """Path only (no query string, no host): safe to log and to store in graph_raw.endpoint."""
        return urlsplit(url).path

    def _backoff(self, attempt: int) -> float:
        return min(self.max_wait, self.backoff_base * (2 ** attempt)) * (0.5 + self._jitter() / 2)

    async def get(self, path_or_url: str, params: dict | None = None) -> dict:
        url = self._url(path_or_url)
        endpoint = self.endpoint(url)
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        attempt = 0
        while True:
            token = await self.tokens.get_token()
            retry_after: float | None = None
            async with self._sem:
                self.stats["requests"] += 1
                try:
                    async with self._session.get(url, params=params, headers={"Authorization": f"Bearer {token}"}) as r:
                        status = r.status
                        try:
                            body = await r.json(content_type=None)
                        except Exception:
                            body = None
                        ra_header = r.headers.get("Retry-After")
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    status, body, ra_header = 0, None, None
                    log.warning(json.dumps({"event": "graph_network_error", "endpoint": endpoint, "error": type(e).__name__}))
            if 200 <= status < 300:
                return body if isinstance(body, dict) else {}
            code, message = _error_info(body)
            if status != 0:
                log.warning(json.dumps({"event": "graph_error", "endpoint": endpoint, "status": status, "code": code}))
            if status in (401, 403):
                raise GraphAuthError(f"{status} {code or ''} {message}".strip(), status=status, code=code, endpoint=endpoint)
            if status == 404:
                raise GraphNotFound(f"404 {code or ''}".strip(), status=status, code=code, endpoint=endpoint)
            if status == 410:
                raise GraphGone(f"410 {code or ''}".strip(), status=status, code=code, endpoint=endpoint)
            if status in (429, 503) or status == 0 or status >= 500:
                try:
                    retry_after = float(ra_header) if ra_header is not None else None
                except ValueError:
                    retry_after = None
                if status == 429:
                    self.stats["throttled"] += 1
                if retry_after is not None and retry_after > self.max_wait:
                    raise GraphThrottled(f"{status} Retry-After {retry_after:.0f}s exceeds the wait limit", status=status,
                                         code=code, endpoint=endpoint, retry_after=retry_after)
                if attempt >= self.max_retries:
                    if status == 429:
                        raise GraphThrottled("429 retries exhausted", status=status, code=code, endpoint=endpoint, retry_after=retry_after)
                    raise GraphServerError(f"{status} {code or 'unavailable'} retries exhausted", status=status, code=code, endpoint=endpoint)
                wait = retry_after if retry_after is not None else self._backoff(attempt)
                attempt += 1
                self.stats["retries"] += 1
                await self._sleep(wait)
                continue
            raise GraphError(f"{status} {code or ''} {message}".strip(), status=status, code=code, endpoint=endpoint)

    # ------------------------------------------------------------------ paging
    async def pages(self, path: str, params: dict | None = None) -> AsyncIterator[dict]:
        """Yield each response page, following @odata.nextLink (verbatim) until it is absent.
        The final page of a delta query carries @odata.deltaLink."""
        body = await self.get(path, params)
        while True:
            yield body
            nxt = body.get("@odata.nextLink")
            if not nxt:
                return
            body = await self.get(nxt)

    async def items(self, path: str, params: dict | None = None) -> AsyncIterator[dict]:
        async for page in self.pages(path, params):
            for it in page.get("value", []):
                yield it
