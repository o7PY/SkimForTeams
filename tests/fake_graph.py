"""A local aiohttp server that mimics Graph for tests. No test talks to real Graph.

    fake = FakeGraph()
    fake.add("/teams/t1/channels", fixture("channels"))                       # 200 JSON, always
    fake.add("/x", [(429, {}, {"Retry-After": "2"}), (200, {"value": []})])   # scripted sequence; the last entry repeats
    async with fake.serve() as base_url: ...

"{base}" inside a body is replaced by the server's base URL (for @odata.nextLink). Requests are recorded in
`fake.requests` as (path, query_dict, authorization_header).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

Response = tuple[int, Any, dict]


class FakeGraph:
    def __init__(self) -> None:
        self.routes: dict[str, list[Response]] = {}
        self.requests: list[tuple[str, dict, str | None]] = []
        self._served: dict[str, int] = {}
        self.base = ""

    def add(self, path: str, body: Any, status: int = 200, headers: dict | None = None) -> "FakeGraph":
        """Single response, or a list of (status, body[, headers]) to be served in order (last one repeats)."""
        if isinstance(body, list) and body and isinstance(body[0], tuple):
            seq = [(s, b, (h[0] if h else {})) for s, b, *h in body]
        else:
            seq = [(status, body, headers or {})]
        self.routes[path] = seq
        self._served[path] = 0
        return self

    def count(self, path: str) -> int:
        return sum(1 for p, _, _ in self.requests if p == path)

    async def _handle(self, request: web.Request) -> web.Response:
        # Key on path + the delta/paging token when present, so ?token=A and ?token=B can be different routes.
        self.requests.append((request.path, dict(request.query), request.headers.get("Authorization")))
        keyed = request.path + (f"?token={request.query['token']}" if "token" in request.query else "")
        key = keyed if keyed in self.routes else request.path
        if key not in self.routes:
            return web.json_response({"error": {"code": "itemNotFound", "message": "no such route in FakeGraph"}}, status=404)
        seq = self.routes[key]
        i = min(self._served[key], len(seq) - 1)
        self._served[key] += 1
        status, body, headers = seq[i]
        text = json.dumps(body).replace("{base}", self.base)
        return web.Response(status=status, text=text, headers=headers, content_type="application/json")

    @asynccontextmanager
    async def serve(self):
        app = web.Application()
        app.router.add_get("/{tail:.*}", self._handle)
        server = TestServer(app)
        await server.start_server()
        self.base = f"http://{server.host}:{server.port}"
        try:
            yield self.base
        finally:
            await server.close()
