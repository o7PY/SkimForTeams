"""T7: ai-process job, run-all integration, kill switch, reprocess, debug endpoints."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer
from ai_fakes import ProviderErrorLike, ScriptedProvider, extraction, item
from ai_harness import CFG, CONV, ist
from test_jobs import AUTH, CHAN, ENV, GID, TEAM, fake_tenant

from skim.ai.providers.base import ProviderError
from skim.ai.services import AIServices
from skim.app import create_app
from skim.config import Settings
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.storage.memory import MemoryStorage

DEBUG = {"Authorization": "Bearer dbg"}
QUIZ = extraction("event", [item("create", "quiz", "Quiz 1", {"kind": "absolute", "date": "2026-10-23", "text": "23 Oct"})])


async def make(script, ai_on=True, cfg=CFG, extra_providers=()):
    store = MemoryStorage()
    await store.upsert_team({"id": TEAM, "aad_group_id": GID})
    await store.mark_team_installed(TEAM, "code", "prof-aad", ist("2026-10-01 10:00"))
    await store.upsert_conversation({"id": CHAN, "conversation_type": "channel", "team_id": TEAM, "channel_id": CHAN})
    await store.backfill_message({"conversation_id": CHAN, "message_id": "q1", "reply_to_id": None, "from_aad_id": "prof-aad",
                                  "from_name": "Prof", "text": "Quiz 1 on 23 Oct", "html": None, "importance": None,
                                  "mentions": None, "subject": None, "created_at": ist("2026-10-20 11:00"),
                                  "last_edited_at": None, "deleted_at": None}, [], [])
    fake = fake_tenant()
    ctx = fake.serve()
    base = await ctx.__aenter__()
    graph = GraphClient(StaticTokenProvider(), base_url=base, sleep=lambda s: asyncio.sleep(0))
    provider = ScriptedProvider(script)
    ai = AIServices.build(cfg, store, providers=[provider, *extra_providers]) if ai_on else None
    settings = Settings.from_env({**ENV, "JOBS_TOKEN": "jobs-secret", "DEBUG_TOKEN": "dbg"})
    client = TestClient(TestServer(create_app(settings, store, graph=graph, ai=ai)))
    await client.start_server()

    async def close():
        await client.close(); await graph.close(); await ctx.__aexit__(None, None, None)
    return client, store, provider, close


async def test_ai_process_creates_events_and_reports_number_only_stats():
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ})
    try:
        body = await (await client.post("/jobs/ai-process", headers=AUTH)).json()
        s = body["stats"]
        assert body["status"] == "ok" and s["ai_messages"] == 1 and s["ai_events_created"] == 1 and s["ai_calls"] == 1
        assert all(isinstance(v, int) for v in s.values())
        assert "Quiz" not in str(body) and len(await store.list_events(team_id=TEAM)) == 1
        again = await (await client.post("/jobs/ai-process", headers=AUTH)).json()
        assert not again["stats"].get("ai_messages") and len(provider.calls) == 1  # processed once
    finally:
        await close()


async def test_run_all_runs_ai_after_the_phase2_steps_and_a_failing_provider_does_not_fail_it():
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": ProviderErrorLike(ProviderError("server", 503))})
    try:
        body = await (await client.post("/jobs/run-all", headers=AUTH)).json()
        assert body["status"] == "ok" and body["stats"]["created"] == 1  # Phase 2 documents still synced
        assert body["stats"]["ai_failed"] == 1 and body["stats"]["ai_all_failed"] == 1
        (a,) = await store.list_message_analyses(CHAN, "q1")
        assert a["status"] == "failed" and a["error_class"] == "server"
    finally:
        await close()


async def test_kill_switch_run_all_is_exactly_phase_2():
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ}, ai_on=False)
    try:
        body = await (await client.post("/jobs/run-all", headers=AUTH)).json()
        assert body["status"] == "ok" and not any(k.startswith("ai_") for k in body["stats"])
        assert not provider.calls and await store.list_events() == [] and store.analyses == {}
        explicit = await (await client.post("/jobs/ai-process", headers=AUTH)).json()
        assert explicit["status"] == "ok" and explicit["stats"]["ai_disabled"] == 1 and not any(k.startswith("ai_") and k != "ai_disabled" for k in explicit["stats"])
        st = await (await client.get("/debug/ai-status", headers=DEBUG)).json()
        assert st["enabled"] is False and st["models"] is None
    finally:
        await close()


async def test_time_budget_makes_ai_process_partial_and_leaves_the_rest_pending():
    from skim.jobs.ai_process import ai_process
    from skim.jobs.common import Budget, JobContext
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ})
    try:
        services = AIServices.build(CFG, store, providers=[provider])
        ctx = JobContext(store, None, Budget(0.0), ai=services)  # budget already spent
        assert await ai_process(ctx, None) == "partial" and not provider.calls
        assert (await store.ai_status(CFG.prompt_version, 3))["messages_pending"] == 1
    finally:
        await close()


async def test_reprocess_keeps_old_analyses_and_leaves_events_alone():
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ})
    try:
        await client.post("/jobs/ai-process", headers=AUTH)
        r = await client.post("/jobs/ai-reprocess?prompt_version=p3-v2", headers=AUTH)
        body = await r.json()
        assert r.status == 200 and body["stats"]["ai_messages"] == 1
        assert sorted(a["prompt_version"] for a in await store.list_message_analyses(CHAN, "q1")) == ["p3-test", "p3-v2"]
        events = await store.list_events(team_id=TEAM)
        assert len(events) == 1 and [c["change_type"] for c in (await store.get_event(events[0]["id"]))["changes"]] == ["created"]
        assert (await client.post("/jobs/ai-reprocess", headers=AUTH)).status == 400
        assert (await client.post(f"/jobs/ai-reprocess?prompt_version={CFG.prompt_version}", headers=AUTH)).status == 400
        assert (await client.post("/jobs/ai-reprocess?prompt_version=../x", headers=AUTH)).status == 400
    finally:
        await close()


async def test_debug_endpoints_events_message_ai_status_and_auth():
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ})
    try:
        await client.post("/jobs/ai-process", headers=AUTH)
        for path in ("/debug/events", "/debug/ai-status", f"/debug/message-ai/{CHAN}/q1"):
            assert (await client.get(path)).status == 403
        evs = await (await client.get(f"/debug/events?team_id={TEAM}&type=quiz&status=scheduled&needs_review=false", headers=DEBUG)).json()
        assert len(evs) == 1 and evs[0]["title"] == "Quiz 1"
        assert await (await client.get("/debug/events?type=exam", headers=DEBUG)).json() == []
        one = await (await client.get(f"/debug/events/{evs[0]['id']}", headers=DEBUG)).json()
        assert [c["change_type"] for c in one["changes"]] == ["created"] and one["messages"][0]["message_id"] == "q1"
        assert (await client.get("/debug/events/999", headers=DEBUG)).status == 404
        assert (await client.get("/debug/events/abc", headers=DEBUG)).status == 404
        ai = await (await client.get(f"/debug/message-ai/{CHAN}/q1", headers=DEBUG)).json()
        assert ai[0]["category"] == "event" and ai[0]["author_role"] == "staff" and ai[0]["provider"] == "groq"
        assert (await client.get(f"/debug/message-ai/{CHAN}/nope", headers=DEBUG)).status == 404
        st = await (await client.get("/debug/ai-status", headers=DEBUG)).json()
        assert st["enabled"] and st["analyses_by_status"] == {"ok": 1} and st["messages_pending"] == 0 and st["events"] == 1
        assert "gk" not in str(st) and st["models"] == {"groq": "m", "gemini": CFG.gemini_model}
    finally:
        await close()


async def test_a_pipeline_bug_on_one_message_is_counted_and_stops_after_max_attempts(monkeypatch):
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ})
    try:
        async def boom(*a, **k):
            raise RuntimeError("bug with SECRET text")
        from skim.ai.pipeline import Pipeline
        monkeypatch.setattr(Pipeline, "_plan", boom)
        for _ in range(4):
            body = await (await client.post("/jobs/ai-process", headers=AUTH)).json()
        (a,) = await store.list_message_analyses(CHAN, "q1")
        assert a["status"] == "failed" and a["error_class"] == "pipeline_error" and a["attempts"] == 3
        assert "SECRET" not in str(body)
    finally:
        await close()
