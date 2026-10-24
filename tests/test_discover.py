import pytest

from fake_graph import FakeGraph
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.common import Budget, JobContext
from skim.jobs.discover import discover_team
from skim.storage.memory import MemoryStorage

TEAM = "19:team@thread.tacv2"
GID = "gid-1"


def chan(i, name, kind="standard"):
    return {"id": f"19:{i}@thread.tacv2", "displayName": name, "membershipType": kind}


def folder(i):
    return {"id": f"folder-{i}", "name": "x", "parentReference": {"driveId": "b!drive", "id": "root"}, "folder": {"childCount": 0}}


async def run(fake, store):
    await store.upsert_team({"id": TEAM, "aad_group_id": GID})
    async with fake.serve() as base:
        g = GraphClient(StaticTokenProvider(), base_url=base)
        ctx = JobContext(store, g, Budget(60))
        await discover_team(ctx, (await store.list_sync_teams())[0])
        await g.close()
    return ctx


async def test_standard_channels_get_folder_rows_and_private_shared_are_only_counted():
    fake = FakeGraph().add(f"/teams/{GID}/channels", {"value": [
        chan("a", "General"), chan("b", "SECRET-PRIVATE-NAME", "private"), chan("c", "Shared-Name", "shared"), chan("d", "Labs")]})
    fake.add(f"/teams/{GID}/channels/19:a@thread.tacv2/filesFolder", folder("a"))
    fake.add(f"/teams/{GID}/channels/19:d@thread.tacv2/filesFolder", folder("d"))
    store = MemoryStorage()
    ctx = await run(fake, store)
    chans = {c["id"]: c for c in await store.list_channels(TEAM)}
    assert set(chans) == {"19:a@thread.tacv2", "19:d@thread.tacv2"}
    assert chans["19:a@thread.tacv2"]["files_drive_id"] == "b!drive" and chans["19:a@thread.tacv2"]["files_folder_item_id"] == "folder-a"
    assert ctx.stats["skipped_non_standard"] == 2 and ctx.stats["channels"] == 2
    # no Graph call touched a private/shared channel, and nothing about them was stored
    assert not any("19:b@" in p or "19:c@" in p for p, _, _ in fake.requests)
    assert "SECRET-PRIVATE-NAME" not in str(store.graph_raw) and "Shared-Name" not in str(store.graph_raw)
    assert (await store.list_sync_teams())[0]["last_discovered_at"] is not None


async def test_one_failing_channel_is_recorded_and_others_continue():
    fake = FakeGraph().add(f"/teams/{GID}/channels", {"value": [chan("a", "A"), chan("d", "D")]})
    fake.add(f"/teams/{GID}/channels/19:a@thread.tacv2/filesFolder", {"error": {"code": "accessDenied"}}, status=403)
    fake.add(f"/teams/{GID}/channels/19:d@thread.tacv2/filesFolder", folder("d"))
    store = MemoryStorage()
    ctx = await run(fake, store)
    chans = {c["id"]: c for c in await store.list_channels(TEAM)}
    assert "accessDenied" in chans["19:a@thread.tacv2"]["sync_error"] and chans["19:a@thread.tacv2"]["files_drive_id"] is None
    assert chans["19:d@thread.tacv2"]["sync_error"] is None and chans["19:d@thread.tacv2"]["files_drive_id"] == "b!drive"
    assert ctx.stats["channel_errors"] == 1


async def test_error_is_cleared_when_the_channel_works_again():
    fake = FakeGraph().add(f"/teams/{GID}/channels", {"value": [chan("a", "A")]})
    fake.add(f"/teams/{GID}/channels/19:a@thread.tacv2/filesFolder", [(404, {"error": {"code": "itemNotFound"}}), (200, folder("a"))])
    store = MemoryStorage()
    await run(fake, store)
    assert (await store.list_channels())[0]["sync_error"]
    async with fake.serve() as base:
        g = GraphClient(StaticTokenProvider(), base_url=base)
        await discover_team(JobContext(store, g, Budget(60)), (await store.list_sync_teams())[0])
        await g.close()
    assert (await store.list_channels())[0]["sync_error"] is None


async def test_team_level_failure_does_not_raise():
    fake = FakeGraph().add(f"/teams/{GID}/channels", {"error": {"code": "Forbidden"}}, status=403)
    ctx = await run(fake, MemoryStorage())
    assert ctx.stats["team_errors"] == 1


async def test_rerun_is_idempotent_for_raw_rows():
    fake = FakeGraph().add(f"/teams/{GID}/channels", {"value": [chan("a", "A")]})
    fake.add(f"/teams/{GID}/channels/19:a@thread.tacv2/filesFolder", folder("a"))
    store = MemoryStorage()
    await run(fake, store)
    n = len(store.graph_raw)
    async with fake.serve() as base:
        g = GraphClient(StaticTokenProvider(), base_url=base)
        await discover_team(JobContext(store, g, Budget(60)), (await store.list_sync_teams())[0])
        await g.close()
    assert len(store.graph_raw) == n and len(store.channels) == 1
