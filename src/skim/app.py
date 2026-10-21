"""aiohttp application: /api/messages, /health, /debug/*."""
from __future__ import annotations

import logging

from aiohttp import web
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter, jwt_authorization_decorator, start_agent_process

from . import debug_api
from .bot import Sender, build_agent, current_capture, ingest, setup_activity_logger
from .config import Settings
from .storage.base import Storage

log = logging.getLogger("skim")


def make_storage(settings: Settings) -> Storage:
    if settings.storage == "memory":
        from .storage.memory import MemoryStorage
        return MemoryStorage()
    from .storage.postgres import PostgresStorage
    assert settings.database_url
    return PostgresStorage(settings.database_url)


def create_app(settings: Settings, storage: Storage | None = None, sender: Sender | None = None) -> web.Application:
    setup_activity_logger()
    storage = storage or make_storage(settings)
    connections = MsalConnectionManager(**settings.sdk_config)
    adapter = CloudAdapter(connection_manager=connections)
    agent = build_agent(settings, connections, sender)

    @jwt_authorization_decorator  # JWT is checked on this route only; /health and /debug have their own rules
    async def messages(request: web.Request) -> web.Response:
        try:
            activity = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(activity, dict):
            return web.json_response({"error": "expected a JSON object"}, status=400)
        # A storage failure propagates as 500 on purpose: Teams will retry, so nothing is lost.
        capture = await ingest(storage, settings, activity)
        token = current_capture.set(capture)
        try:
            response = await start_agent_process(request, agent, adapter)
            return response or web.Response(status=202)
        except Exception:
            # FR-15: unknown/malformed activities must not produce 5xx; the raw row is already stored.
            log.exception("agent processing failed raw_id=%s", capture.raw_id)
            return web.Response(status=202)
        finally:
            current_capture.reset(token)

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "db": await storage.ping()})

    app = web.Application()
    app["agent_configuration"] = connections.get_default_connection_configuration()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    if settings.debug_token:
        debug_api.register(app, storage, settings.debug_token)

    async def on_startup(_: web.Application) -> None:
        await storage.start()

    async def on_cleanup(_: web.Application) -> None:
        await storage.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app
