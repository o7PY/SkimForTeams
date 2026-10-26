"""/api/v1 (PHASE4.md section 6.2). Every route sits behind the bearer-token middleware; every query is scoped to the
token's user. Registered only when API_ENABLED."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable

from aiohttp import web

from ..storage.base import Storage
from . import routes_items, routes_reminders, user_settings
from .auth import AuthError, TokenVerifier

log = logging.getLogger("skim.api")

USER = web.RequestKey("user", dict)
SEEN_TTL = 300.0  # re-check the user (still in Supabase auth? profile changed?) at most every 5 minutes per instance


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(status: int, message: str, **extra) -> web.Response:
    return web.json_response({"error": message, **extra}, status=status)


def _claims_to_user(claims: dict) -> dict:
    meta = claims.get("user_metadata") if isinstance(claims.get("user_metadata"), dict) else {}
    name = meta.get("full_name") or meta.get("name")
    email = claims.get("email")
    return {"id": str(claims["sub"]).lower(), "email": email if isinstance(email, str) else None,
            "name": name if isinstance(name, str) else None}


def make_auth_middleware(storage: Storage, verifier: TokenVerifier, seen: dict[str, float], clock: Callable[[], float]):
    @web.middleware
    async def auth(request: web.Request, handler):
        header = request.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return web.json_response({"error": "unauthorized"}, status=401, headers={"WWW-Authenticate": "Bearer"})
        try:
            claims = await verifier.verify(token.strip())
        except AuthError as e:
            log.info("api auth rejected: %s", e.reason)  # the reason names the failure, never the token
            if e.status == 503:
                return _err(503, "auth keys unavailable, try again")
            return web.json_response({"error": "unauthorized"}, status=401, headers={"WWW-Authenticate": "Bearer"})
        user = _claims_to_user(claims)
        now = clock()
        if now - seen.get(user["id"], -SEEN_TTL) >= SEEN_TTL:
            if not await storage.auth_user_exists(user["id"]):  # a deleted account's token must not bring it back
                seen.pop(user["id"], None)
                return web.json_response({"error": "unauthorized"}, status=401, headers={"WWW-Authenticate": "Bearer"})
            await storage.upsert_app_user(user["id"], user["email"], user["name"], _now())
            seen[user["id"]] = now
        request[USER] = user
        return await handler(request)
    return auth


async def _json(request: web.Request):
    try:
        return await request.json()
    except Exception:
        return None


def register(app: web.Application, storage: Storage, verifier: TokenVerifier, clock: Callable[[], float] = time.monotonic,
             now: Callable[[], datetime] = _now, staff_aad_ids: frozenset[str] = frozenset(),
             id_secret: str = "") -> web.Application:
    if not id_secret:
        raise ValueError("id_secret is required (reminder ids are derived from it)")
    seen: dict[str, float] = {}
    sub = web.Application(middlewares=[make_auth_middleware(storage, verifier, seen, clock)])

    def profile(u: dict) -> dict:
        return {"id": u["id"], "email": u["email"], "name": u["name"], "created_at": u["created_at"].isoformat(),
                "settings": user_settings.effective(u["settings"])}

    async def me(request: web.Request) -> web.Response:
        u = await storage.get_app_user(request[USER]["id"])
        if u is None:  # deleted by another instance while this token was cached
            return _err(401, "unauthorized")
        return web.json_response(profile(u))

    async def patch_settings(request: web.Request) -> web.Response:
        body = await _json(request)
        clean, problems = user_settings.validate_patch(body)
        if problems:
            return _err(400, "invalid settings", details=problems)
        u = await storage.get_app_user(request[USER]["id"])
        if u is None:
            return _err(401, "unauthorized")
        stored = user_settings.deep_merge(u["settings"] or {}, clean)
        await storage.set_user_settings(u["id"], stored)
        return web.json_response({"settings": user_settings.effective(stored)})

    async def delete_me(request: web.Request) -> web.Response:
        uid = request[USER]["id"]
        await storage.delete_app_user(uid)       # every row for the user (children cascade)
        await storage.delete_auth_user(uid)      # then the Supabase auth user (P4-D-134); a failure is a 500 and can be retried
        seen.pop(uid, None)
        log.info("account deleted")
        return web.Response(status=204)

    sub.router.add_get("/me", me)
    sub.router.add_patch("/me/settings", patch_settings)
    sub.router.add_delete("/me", delete_me)
    routes_items.add_routes(sub, storage, USER, now, frozenset(staff_aad_ids), id_secret)
    routes_reminders.add_routes(sub, storage, USER, now, id_secret)
    app.add_subapp("/api/v1", sub)
    return sub
