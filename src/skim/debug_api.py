"""Token-protected JSON debug endpoints (FR-11). Only registered when DEBUG_TOKEN is set."""
from __future__ import annotations

import hmac
import json

from aiohttp import web

from .storage.base import Storage


def _json(data) -> web.Response:
    return web.json_response(data, dumps=lambda o: json.dumps(o, default=str, ensure_ascii=False))


def _limit(request: web.Request, default: int) -> int:
    try:
        return max(1, min(int(request.query.get("limit", default)), 500))
    except ValueError:
        return default


def register(app: web.Application, storage: Storage, token: str) -> None:
    @web.middleware
    async def auth(request: web.Request, handler):
        if request.path.startswith("/debug"):
            given = request.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if not hmac.compare_digest(given.encode(), expected.encode()):
                return web.json_response({"error": "forbidden"}, status=403)
        return await handler(request)

    app.middlewares.append(auth)

    async def raw(request: web.Request):
        return _json(await storage.list_raw(_limit(request, 20), request.query.get("type") or None))

    async def messages(request: web.Request):
        return _json(await storage.list_messages(
            _limit(request, 50), request.query.get("team_id") or None, request.query.get("conversation_id") or None))

    async def message(request: web.Request):
        found = await storage.get_message(request.match_info["conversation_id"], request.match_info["message_id"])
        return _json(found) if found else web.json_response({"error": "not found"}, status=404)

    async def teams(request: web.Request):
        return _json(await storage.list_teams())

    app.router.add_get("/debug/raw", raw)
    app.router.add_get("/debug/messages", messages)
    app.router.add_get("/debug/messages/{conversation_id}/{message_id}", message)
    app.router.add_get("/debug/teams", teams)
