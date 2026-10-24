"""T10: Phase 2 debug endpoints, structured job logs, and the smoke script against a local app."""
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from fake_graph import FakeGraph
from skim.app import create_app
from skim.config import Settings
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.storage.memory import MemoryStorage
from test_jobs import AUTH, CHAN, DRIVE, ENV, GID, TEAM, fake_tenant

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import smoke_remote  # noqa: E402

DBG = {"Authorization": "Bearer dbg"}


@pytest.fixture
async def app_env():
    store = MemoryStorage()
    await store.upsert_team({"id": TEAM, "aad_group_id": GID, "name": "Class"})
    await store.mark_team_installed(TEAM, "code", None, datetime.now().astimezone())
    fake = fake_tenant()
    async with fake.serve() as base:
        graph = GraphClient(StaticTokenProvider(), base_url=base, sleep=lambda s: asyncio.sleep(0))
        settings = Settings.from_env({**ENV, "JOBS_TOKEN": "jobs-secret", "DEBUG_TOKEN": "dbg"})
        client = TestClient(TestServer(create_app(settings, store, graph=graph)))
        await client.start_server()
        yield client, store, fake
        await client.close()
        await graph.close()


async def get(client, path):
    r = await client.get(path, headers=DBG)
    return r.status, await r.json()


async def test_debug_endpoints_require_the_debug_token(app_env):
    client, *_ = app_env
    for p in ("/debug/channels", "/debug/documents", "/debug/documents/1", "/debug/sync-status"):
        assert (await client.get(p)).status == 403
        assert (await client.get(p, headers={"Authorization": "Bearer nope"})).status == 403


async def test_channels_documents_and_document_detail(app_env):
    client, store, fake = app_env
    await client.post("/jobs/run-all", headers=AUTH)
    st, chans = await get(client, f"/debug/channels?team_id={TEAM}")
    assert st == 200 and [c["id"] for c in chans] == [CHAN]  # the private channel has no row
    st, docs = await get(client, f"/debug/documents?channel_id={CHAN}")
    assert [d["name"] for d in docs] == ["syllabus.pdf"]
    st, detail = await get(client, f"/debug/documents/{docs[0]['id']}")
    assert st == 200 and detail["document"]["path"] == "syllabus.pdf" and [e["event"] for e in detail["events"]] == ["created"]
    assert [a["message_id"] for a in detail["attachments"]] == ["m1"]  # linked attachment shows up
    assert (await get(client, "/debug/documents/99999"))[0] == 404 and (await get(client, "/debug/documents/abc"))[0] == 404


async def test_include_deleted_and_limit(app_env):
    client, store, fake = app_env
    await client.post("/jobs/run-all", headers=AUTH)
    doc = await store.get_document(DRIVE, "a")
    await store.upsert_document({**doc, "deleted_at": datetime.now().astimezone()})
    assert (await get(client, "/debug/documents"))[1] == []
    assert len((await get(client, "/debug/documents?include_deleted=true"))[1]) == 1
    assert (await get(client, "/debug/documents?include_deleted=true&limit=0"))[0] == 200


async def test_sync_status_shows_runs_state_and_errors_without_leaking_links(app_env):
    client, store, fake = app_env
    await client.post("/jobs/run-all", headers=AUTH)
    await store.update_channel(CHAN, sync_error="files sync failed: GraphAuthError 403 accessDenied")
    st, body = await get(client, "/debug/sync-status")
    assert st == 200
    assert {r["job"] for r in body["job_runs"]} == {"run-all"} and body["job_runs"][0]["status"] == "ok"
    assert [c["sync_error"] for c in body["channels_with_errors"]] == ["files sync failed: GraphAuthError 403 accessDenied"]
    assert any(s["key"].startswith("folder:") and s["has_delta_link"] for s in body["sync_state"])
    assert "token=" not in json.dumps(body["sync_state"]) and '"delta_link"' not in json.dumps(body["sync_state"])
    assert "http" not in json.dumps(body["sync_state"])
    assert body["teams"][0]["needs_initial_sync"] is False


def test_redact_state_hides_urls_and_summarises_cursors():
    from skim.debug_api import redact_state
    r = redact_state({"key": "backfill:c", "delta_link": None, "last_status": "partial", "last_error": None,
                      "updated_at": 1, "cursor": {"next": "https://graph.microsoft.com/x?$skiptoken=SECRET",
                                                  "pending": ["a", "b"], "count": 7}})
    assert r["cursor"] == {"next": "<url>", "pending": 2, "count": 7} and "SECRET" not in str(r)


async def test_job_run_is_logged_as_one_json_line(app_env, caplog):
    client, *_ = app_env
    with caplog.at_level(logging.INFO, logger="skim.jobs"):
        await client.post("/jobs/run-all", headers=AUTH)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith('{"event": "job_run"')]
    assert len(lines) == 1 and lines[0]["job"] == "run-all" and lines[0]["status"] == "ok"
    assert set(lines[0]) == {"event", "job", "status", "duration_s", "stats"}
    text = json.dumps(lines) + " ".join(r.getMessage() for r in caplog.records)
    assert "hi" not in json.dumps(lines[0]["stats"])  # message text is not in job logs


async def test_graph_errors_are_logged_with_endpoint_status_and_code_only(app_env, caplog):
    client, store, fake = app_env
    fake.add(f"/teams/{GID}/channels/{CHAN}/messages", {"error": {"code": "Forbidden", "message": "SECRET BODY TEXT"}}, status=403)
    with caplog.at_level(logging.WARNING, logger="skim.graph"):
        await client.post("/jobs/run-all", headers=AUTH)
    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "skim.graph" and r.getMessage().startswith("{")]
    assert {"event": "graph_error", "endpoint": f"/teams/{GID}/channels/{CHAN}/messages", "status": 403, "code": "Forbidden"} in lines
    assert "SECRET BODY TEXT" not in " ".join(r.getMessage() for r in caplog.records)


async def test_smoke_script_passes_against_a_local_app(app_env):
    client, *_ = app_env
    base = str(client.make_url("")).rstrip("/")
    results = await smoke_remote.run(base, "dbg", "jobs-secret", jobs=True)
    failed = [r for r in results if not r[1]]
    assert not failed, failed
    names = [n for n, _, _ in results]
    assert "GET /debug/sync-status" in names and "POST /jobs/run-all #2" in names and "second run-all creates nothing" in names


async def test_smoke_script_fails_when_the_service_is_unhealthy(app_env):
    client, *_ = app_env
    results = await smoke_remote.run(str(client.make_url("")).rstrip("/"), "WRONG", None)
    assert any(not ok for _, ok, _ in results)
