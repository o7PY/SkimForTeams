"""POST/DELETE /api/v1/devices and GET /api/v1/sync (PHASE4.md section 6.2). A device is an Expo push token the app registered so the
server can send change alerts (P4-D-121); /sync is the cheap check the background task makes before refetching anything."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Awaitable, Callable

from aiohttp import web

from ..push import valid_token
from ..storage.base import Storage
from . import cards
from .routes_items import body, err

MAX_DEVICES = 10   # active tokens per user; stops anyone using registration to fill the table


def add_routes(sub: web.Application, storage: Storage, user_key: web.RequestKey, now: Callable[[], datetime],
               reminders_etag: Callable[[str], Awaitable[str]]) -> None:
    async def register(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        b = await body(request)
        token, platform = (b or {}).get("expo_push_token"), (b or {}).get("platform")
        if not b or set(b) - {"expo_push_token", "platform"} or not valid_token(token) or platform not in ("android", "ios"):
            return err(400, "expo_push_token (an Expo push token) and platform (android or ios) are required")
        mine = await storage.list_devices(uid, active_only=True)
        if token not in {d["expo_push_token"] for d in mine} and len(mine) >= MAX_DEVICES:
            return err(429, "too many devices registered; sign out of the others first")
        if not await storage.upsert_device(uid, token, platform, now()):
            return err(409, "that device is registered to another account; sign out there first")
        return web.json_response({"ok": True})

    async def unregister(request: web.Request) -> web.Response:
        if not await storage.delete_device(request[user_key]["id"], request.match_info["token"]):
            return err(404, "not found")
        return web.Response(status=204)

    async def sync(request: web.Request) -> web.Response:
        uid = request[user_key]["id"]
        raw, changed = request.query.get("since"), True
        if raw:
            try:
                since = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return err(400, "since must be an ISO time with an offset")
            if since.tzinfo is None or since > now() + timedelta(minutes=5):
                return err(400, "since must be an ISO time with an offset, not in the future")
            changed = await storage.deck_changed_since(uid, since)
        return web.json_response({"deck_changed": changed, "reminders_etag": await reminders_etag(uid),
                                  "server_time": cards.iso(now())})

    sub.router.add_post("/devices", register)
    sub.router.add_delete("/devices/{token}", unregister)
    sub.router.add_get("/sync", sync)
