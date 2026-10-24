import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from fake_graph import FakeGraph
from skim.app import create_app
from skim.config import Settings
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.runner import JobRunner
from skim.storage.memory import MemoryStorage

TEAM, GID = "19:team@thread.tacv2", "gid-1"
CHAN = "19:general@thread.tacv2"
DRIVE, FOLDER = "b!drive", "F0"
ENV = {"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true", "STORAGE": "memory", "PAIRING_SECRET": "s"}
AUTH = {"Authorization": "Bearer jobs-secret"}


def fake_tenant() -> FakeGraph:
    f = FakeGraph()
    f.add(f"/teams/{GID}/channels", {"value": [
        {"id": CHAN, "displayName": "General", "membershipType": "standard"},
        {"id": "19:priv@thread.tacv2", "displayName": "Hidden", "membershipType": "private"}]})
    f.add(f"/teams/{GID}/channels/{CHAN}/filesFolder", {"id": FOLDER, "name": "General",
                                                         "parentReference": {"driveId": DRIVE, "id": "root"}, "folder": {}})
    f.add(f"/drives/{DRIVE}/items/{FOLDER}/delta", {"value": [], "@odata.deltaLink": "{base}" + f"/drives/{DRIVE}/items/{FOLDER}/delta?token=T1"})
    f.add(f"/drives/{DRIVE}/items/{FOLDER}/delta?token=T1", {"value": [], "@odata.deltaLink": "{base}" + f"/drives/{DRIVE}/items/{FOLDER}/delta?token=T1"})
    f.add(f"/drives/{DRIVE}/items/{FOLDER}/children", {"value": [
        {"id": "a", "name": "syllabus.pdf", "size": 5, "eTag": "e", "file": {"mimeType": "application/pdf"},
         "webUrl": "https://x.sharepoint.com/General/syllabus.pdf", "parentReference": {"driveId": DRIVE, "id": FOLDER}}]})
    return f


@pytest.fixture
async def env():
    store = MemoryStorage()
    await store.upsert_team({"id": TEAM, "aad_group_id": GID})
    await store.mark_team_installed(TEAM, "code", None, __import__("datetime").datetime.now().astimezone())
    fake = fake_tenant()
    async with fake.serve() as base:
        graph = GraphClient(StaticTokenProvider(), base_url=base, sleep=lambda s: asyncio.sleep(0))
        settings = Settings.from_env({**ENV, "JOBS_TOKEN": "jobs-secret"})
        client = TestClient(TestServer(create_app(settings, store, graph=graph)))
        await client.start_server()
        yield client, store, fake, graph
        await client.close()
        await graph.close()


async def test_auth_401_wrong_or_missing_token(env):
    client, *_ = env
    assert (await client.post("/jobs/run-all")).status == 401
    assert (await client.post("/jobs/run-all", headers={"Authorization": "Bearer nope"})).status == 401


async def test_404_when_jobs_token_unset():
    store = MemoryStorage()
    client = TestClient(TestServer(create_app(Settings.from_env(ENV), store)))
    await client.start_server()
    try:
        assert (await client.post("/jobs/run-all", headers=AUTH)).status == 404
        assert (await client.get("/health")).status == 200
    finally:
        await client.close()


async def test_404_when_no_graph_credentials():
    client = TestClient(TestServer(create_app(Settings.from_env({**ENV, "JOBS_TOKEN": "jobs-secret"}), MemoryStorage())))
    await client.start_server()
    try:
        assert (await client.post("/jobs/run-all", headers=AUTH)).status == 404
    finally:
        await client.close()


async def test_run_all_syncs_files_and_second_run_changes_nothing(env):
    client, store, fake, _ = env
    r = await client.post("/jobs/run-all", headers=AUTH)
    body = await r.json()
    assert r.status == 200 and body["status"] == "ok" and body["next"] is False and body["job_run_id"]
    assert body["stats"]["created"] == 1 and body["stats"]["channels"] == 1 and body["stats"]["skipped_non_standard"] == 1
    assert [d["path"] for d in await store.list_documents()] == ["syllabus.pdf"]
    assert list(store.channels) == [CHAN]  # the private channel left no trace
    snapshot = ([dict(d) for d in store.documents.values()], len(store.document_events), len(store.graph_raw))
    r2 = await (await client.post("/jobs/run-all", headers=AUTH)).json()
    assert r2["status"] == "ok" and not any(r2["stats"].get(k) for k in ("created", "modified", "renamed", "moved", "deleted", "restored"))
    assert r2["stats"].get("delta_syncs") == 1 and r2["job_run_id"] != body["job_run_id"]
    assert len(store.document_events) == snapshot[1] and len(store.graph_raw) == snapshot[2]
    runs = {r["job"]: r for r in await store.latest_job_runs()}
    assert runs["run-all"]["status"] == "ok" and runs["run-all"]["finished_at"] is not None
    assert (await store.list_sync_teams())[0]["last_files_sync_at"] is not None


async def test_individual_endpoints_and_team_filter(env):
    client, store, *_ = env
    assert (await (await client.post("/jobs/discover", headers=AUTH)).json())["stats"]["channels"] == 1
    r = await (await client.post("/jobs/sync-files?team_id=nobody", headers=AUTH)).json()
    assert r["status"] == "ok" and not store.documents  # filter matched no team
    r = await (await client.post(f"/jobs/sync-files?team_id={GID}", headers=AUTH)).json()
    assert r["stats"]["created"] == 1


async def test_busy_when_another_run_holds_the_lock(env):
    client, store, *_ = env
    async with store.job_lock("jobs") as got:
        assert got
        r = await client.post("/jobs/run-all", headers=AUTH)
        body = await r.json()
    assert r.status == 200 and body["status"] == "busy" and body["next"] is False
    assert (await store.latest_job_runs())[0]["status"] == "busy"
    assert not store.documents and (await (await client.post("/jobs/run-all", headers=AUTH)).json())["status"] == "ok"


async def test_two_simultaneous_calls_one_runs_one_is_busy(env):
    client, *_ = env
    a, b = await asyncio.gather(client.post("/jobs/run-all", headers=AUTH), client.post("/jobs/run-all", headers=AUTH))
    statuses = sorted([(await a.json())["status"], (await b.json())["status"]])
    assert statuses in (["busy", "ok"], ["ok", "ok"])  # "ok","ok" only if the first finished before the second began


async def test_tiny_budget_returns_partial_and_repeated_calls_finish_without_duplicates(env):
    client, store, fake, graph = env
    runner = JobRunner(store, graph, budget_seconds=-1)  # already expired
    first = await runner.run("run-all")
    assert first["status"] == "partial" and first["next"] is True
    assert len(store.documents) == 0
    # resume with a real budget, twice: same end state, one 'created' event
    ok = await JobRunner(store, graph, 60).run("run-all")
    again = await JobRunner(store, graph, 60).run("run-all")
    assert ok["status"] == "ok" and again["status"] == "ok"
    assert len(store.documents) == 1 and sum(1 for e in store.document_events if e["event"] == "created") == 1


async def test_a_failing_channel_is_recorded_and_the_run_continues(env):
    client, store, fake, graph = env
    fake.add(f"/teams/{GID}/channels", {"value": [
        {"id": CHAN, "displayName": "General", "membershipType": "standard"},
        {"id": "19:bad@thread.tacv2", "displayName": "Bad", "membershipType": "standard"}]})
    fake.add(f"/teams/{GID}/channels/19:bad@thread.tacv2/filesFolder", {"error": {"code": "accessDenied"}}, status=403)
    body = await (await client.post("/jobs/run-all", headers=AUTH)).json()
    assert body["status"] == "ok" and body["stats"]["channel_errors"] == 1 and body["stats"]["created"] == 1
    assert "accessDenied" in store.channels["19:bad@thread.tacv2"]["sync_error"] and store.channels[CHAN]["sync_error"] is None


async def test_files_error_in_one_channel_does_not_abort_others(env):
    client, store, fake, graph = env
    await client.post("/jobs/run-all", headers=AUTH)
    fake.add(f"/drives/{DRIVE}/items/{FOLDER}/delta?token=T1", {"error": {"code": "accessDenied"}}, status=403)
    body = await (await client.post("/jobs/run-all", headers=AUTH)).json()
    assert body["status"] == "ok" and body["stats"]["channel_errors"] == 1
    assert "files sync failed: GraphAuthError 403 accessDenied" == store.channels[CHAN]["sync_error"]


async def test_unexpected_exception_is_an_error_run_without_leaking_text(env):
    client, store, fake, graph = env

    class Boom(Exception): ...

    async def boom(*a, **k):
        raise Boom("https://secret.example/?token=ABC")

    store.list_sync_teams = boom
    body = await (await client.post("/jobs/run-all", headers=AUTH)).json()
    assert body["status"] == "error" and body["error"] == "Boom" and "ABC" not in str(body)
    assert (await store.latest_job_runs())[0]["status"] == "error"


async def test_removed_team_is_not_synced(env):
    client, store, *_ = env
    from datetime import datetime
    await store.mark_team_removed(TEAM, datetime.now().astimezone())
    body = await (await client.post("/jobs/run-all", headers=AUTH)).json()
    assert body["status"] == "ok" and not store.documents and not store.channels


async def test_install_sets_needs_initial_sync_again_on_reinstall(env):
    _, store, *_ = env
    from datetime import datetime
    now = datetime.now().astimezone()
    await store.update_team(TEAM, needs_initial_sync=False)
    await store.mark_team_installed(TEAM, "code", None, now)  # retry/upgrade of an active install: untouched
    assert store.teams[TEAM]["needs_initial_sync"] is False
    await store.mark_team_removed(TEAM, now)
    await store.mark_team_installed(TEAM, "code2", None, now)  # a real re-install
    assert store.teams[TEAM]["needs_initial_sync"] is True


async def test_run_all_end_to_end_on_postgres(pg_store):
    """Same flow through the real Postgres storage: documents, events, sync_state, job_runs, advisory lock."""
    from datetime import datetime
    store = pg_store
    await store.upsert_team({"id": TEAM, "aad_group_id": GID})
    await store.mark_team_installed(TEAM, "code", None, datetime.now().astimezone())
    fake = fake_tenant()
    async with fake.serve() as base:
        graph = GraphClient(StaticTokenProvider(), base_url=base)
        runner = JobRunner(store, graph, 60)
        first = await runner.run("run-all")
        second = await runner.run("run-all")
        async with store.job_lock("jobs") as got:
            assert got
            busy = await runner.run("run-all")
        await graph.close()
    assert first["status"] == "ok" and first["stats"]["created"] == 1 and second["stats"].get("delta_syncs") == 1
    assert not any(second["stats"].get(k) for k in ("created", "modified", "deleted"))
    assert busy["status"] == "busy"
    [doc] = await store.list_documents()
    assert doc["path"] == "syllabus.pdf" and [e["event"] for e in await store.list_document_events(doc["id"])] == ["created"]
    assert [c["id"] for c in await store.list_channels()] == [CHAN]
    runs = {r["job"]: r for r in await store.latest_job_runs()}
    assert runs["run-all"]["status"] == "busy"
    assert (await store.get_sync_state(f"folder:{CHAN}"))["delta_link"]
    assert (await store.list_sync_teams())[0]["last_files_sync_at"] is not None
