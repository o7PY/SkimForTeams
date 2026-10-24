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


def _flag(request: web.Request, name: str) -> bool:
    return request.query.get(name, "").lower() in ("1", "true", "yes")


def redact_state(row: dict) -> dict:
    """sync_state for humans: Graph delta/next links carry tokens, so only their presence is shown."""
    cur = row.get("cursor")
    summary = None
    if isinstance(cur, dict):
        summary = {k: ("<url>" if isinstance(v, str) and v.startswith("http") else len(v) if isinstance(v, list) else v)
                   for k, v in cur.items() if k != "delta_link"}
    return {"key": row["key"], "last_status": row.get("last_status"), "last_error": row.get("last_error"),
            "updated_at": row.get("updated_at"), "has_delta_link": bool(row.get("delta_link")), "cursor": summary}


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

    async def channels(request: web.Request):
        return _json(await storage.list_channels(request.query.get("team_id") or None))

    async def documents(request: web.Request):
        return _json(await storage.list_documents(
            request.query.get("team_id") or None, request.query.get("channel_id") or None,
            _flag(request, "include_deleted"), _limit(request, 100)))

    async def document(request: web.Request):
        try:
            doc = await storage.get_document_by_id(int(request.match_info["id"]))
        except ValueError:
            doc = None
        if doc is None:
            return web.json_response({"error": "not found"}, status=404)
        return _json({"document": doc, "events": await storage.list_document_events(doc["id"]),
                      "attachments": await storage.list_document_links(doc["id"])})

    async def sync_status(request: web.Request):
        teams = await storage.list_teams()
        return _json({
            "job_runs": await storage.latest_job_runs(),
            "sync_state": [redact_state(r) for r in await storage.list_sync_state()],
            "channels_with_errors": [{"id": c["id"], "team_id": c["team_id"], "display_name": c["display_name"],
                                      "sync_error": c["sync_error"]}
                                     for c in await storage.list_channels() if c.get("sync_error")],
            "teams": [{k: t.get(k) for k in ("id", "name", "needs_initial_sync", "last_discovered_at",
                                              "last_files_sync_at", "last_backfill_at", "removed_at")} for t in teams]})

    app.router.add_get("/debug/channels", channels)
    app.router.add_get("/debug/documents", documents)
    app.router.add_get("/debug/documents/{id}", document)
    app.router.add_get("/debug/sync-status", sync_status)
    app.router.add_get("/debug/raw", raw)
    app.router.add_get("/debug/messages", messages)
    app.router.add_get("/debug/messages/{conversation_id}/{message_id}", message)
    app.router.add_get("/debug/teams", teams)
