"""aiohttp application: /api/messages, /health, /debug/*."""
from __future__ import annotations

import warnings

import logging

from aiohttp import web
from aiohttp.web_exceptions import NotAppKeyWarning
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter, jwt_authorization_decorator, start_agent_process

from . import debug_api, jobs_api
from .api import routes as api_routes
from .api.auth import TokenVerifier
from .bot import Sender, build_agent, current_capture, ingest, setup_activity_logger
from .config import Settings
from .schema import schema_status
from .graph.auth import MsalTokenProvider
from .graph.client import GraphClient
from .ai.services import AIServices
from .jobs.runner import JobRunner
from .normalize import is_non_standard_channel
from .storage.base import Storage

# The SDK's JWT middleware stores request["claims_identity"] with a string key (its code, not ours): same warning, filtered by module.
warnings.filterwarnings("ignore", category=NotAppKeyWarning, module=r"microsoft_agents\.hosting\.aiohttp\..*")

log = logging.getLogger("skim")


def make_storage(settings: Settings) -> Storage:
    if settings.storage == "memory":
        from .storage.memory import MemoryStorage
        return MemoryStorage()
    from .storage.postgres import PostgresStorage
    assert settings.database_url
    return PostgresStorage(settings.database_url)


def create_app(settings: Settings, storage: Storage | None = None, sender: Sender | None = None,
               graph: GraphClient | None = None, ai: "AIServices | None | str" = "auto",
               verifier: TokenVerifier | None = None) -> web.Application:
    setup_activity_logger()
    storage = storage or make_storage(settings)
    connections = MsalConnectionManager(**settings.sdk_config)
    adapter = CloudAdapter(connection_manager=connections)
    agent = build_agent(settings, storage, connections, sender)
    if ai == "auto":  # Phase 3 (P3-D-018): off, with one log line, unless AI_ENABLED and all keys/models are set
        ai = AIServices.from_env(storage)

    @jwt_authorization_decorator  # JWT is checked on this route only; /health and /debug have their own rules
    async def messages(request: web.Request) -> web.Response:
        try:
            activity = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(activity, dict):
            return web.json_response({"error": "expected a JSON object"}, status=400)
        if is_non_standard_channel(activity):
            # P2-D-116: privacy wins over raw-first. Nothing is stored, nothing is logged but this fixed line.
            log.info("skipped non-standard channel")
            return web.Response(status=202)
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
        db = await storage.ping()
        status = (await schema_status(storage))[0] if db else "unknown"
        return web.json_response({"ok": True, "db": db, "db_schema": status})

    app = web.Application()
    # The Microsoft Agents SDK looks this up by the plain STRING key ("agent_configuration"); a web.AppKey would not be found and
    # would silently disable its JWT check. aiohttp warns about string keys, so only that known assignment is silenced.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotAppKeyWarning)
        app["agent_configuration"] = connections.get_default_connection_configuration()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    if settings.debug_token:
        debug_api.register(app, storage, settings.debug_token, ai)

    if settings.api_enabled:  # Phase 4: student app API (P4-D-104); absent (plain 404) otherwise
        assert settings.supabase_url
        api_routes.register(app, storage, verifier or TokenVerifier(settings.supabase_url, settings.supabase_jwt_secret),
                            staff_aad_ids=settings.staff_aad_ids, id_secret=settings.pairing_secret)
    else:
        log.info("Phase 4 API disabled: API_ENABLED is not set")

    own_graph = False
    if settings.jobs_token and graph is None and settings.graph_credentials:
        graph = GraphClient(MsalTokenProvider(*settings.graph_credentials), base_url=settings.graph_base_url,
                            max_concurrency=settings.graph_max_concurrency)
        own_graph = True
    if settings.jobs_token and graph is not None:
        jobs_api.register(app, JobRunner(storage, graph, settings.job_time_budget_seconds,
                                  backfill_since_days=settings.backfill_since_days,
                                  backfill_max_messages=settings.backfill_max_messages_per_channel, ai=ai,
                                  staff_aad_ids=settings.staff_aad_ids, feed_lookback_days=settings.feed_lookback_days), settings.jobs_token)
    else:
        log.info("Phase 2 jobs disabled: %s", "JOBS_TOKEN is not set" if not settings.jobs_token
                 else "no Graph credentials")

    async def on_startup(_: web.Application) -> None:
        await storage.start()
        status, missing = await schema_status(storage)
        if status == "behind":
            log.error("DATABASE SCHEMA IS BEHIND THE CODE - missing migrations: %s. Run scripts/migrate.py. "
                      "/health reports db_schema=behind; jobs will fail until then.", ", ".join(missing))
        else:
            log.info("database schema: %s", status)

    async def on_cleanup(_: web.Application) -> None:
        if own_graph and graph is not None:
            await graph.close()
        if ai is not None:
            await ai.close()
        await storage.close()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app
