"""Phase 2 storage contract. Runs against MemoryStorage always, and PostgresStorage when TEST_DATABASE_URL is set
(a disposable database: the schema is dropped). The same assertions must hold for both."""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
DRIVE, FOLDER = "b!drive", "folder1"


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        from skim.storage.memory import MemoryStorage
        yield MemoryStorage()
        return
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    import asyncpg
    from migrate import MIGRATIONS
    from skim.storage.postgres import PostgresStorage

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    await conn.execute("drop schema public cascade; create schema public;")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await conn.execute(path.read_text())
    await conn.execute((MIGRATIONS / "003_phase2.sql").read_text())  # applies cleanly a second time
    await conn.close()
    s = PostgresStorage(dsn)
    await s.start()
    yield s
    await s.close()


async def seed(s):
    await s.upsert_team({"id": "19:team@thread.tacv2", "aad_group_id": "gid-1", "name": "T"})
    await s.upsert_channel({"id": "19:chan@thread.tacv2", "team_id": "19:team@thread.tacv2", "display_name": "General",
                            "membership_type": "standard"})


def doc(item, **kw):
    return {"drive_id": DRIVE, "item_id": item, "team_id": "19:team@thread.tacv2", "channel_id": "19:chan@thread.tacv2",
            "name": item + ".pdf", "parent_item_id": FOLDER, "path": item + ".pdf", **kw}


async def test_graph_raw_dedupes_on_key(store):
    row = {"kind": "driveItem", "endpoint": "/drives/x/items/y", "item_key": "x:y", "dedupe_key": "k1", "payload": {"a": 1}}
    a, ins1 = await store.insert_graph_raw(row)
    b, ins2 = await store.insert_graph_raw(row)
    assert (ins1, ins2) == (True, False) and a == b


async def test_teams_for_sync_and_flags(store):
    await seed(store)
    await store.upsert_team({"id": "19:noaad@thread.tacv2"})
    await store.upsert_team({"id": "19:gone@thread.tacv2", "aad_group_id": "gid-2"})
    await store.mark_team_removed("19:gone@thread.tacv2", T0)
    assert [t["id"] for t in await store.list_sync_teams()] == ["19:team@thread.tacv2"]
    t = (await store.list_sync_teams())[0]
    assert t["needs_initial_sync"] is True
    await store.update_team(t["id"], needs_initial_sync=False, last_discovered_at=T0)
    t = (await store.list_sync_teams())[0]
    assert t["needs_initial_sync"] is False and t["last_discovered_at"] == T0
    with pytest.raises(ValueError):
        await store.update_team(t["id"], name="x")


async def test_channel_upsert_keeps_existing_values_and_error(store):
    await seed(store)
    cid = "19:chan@thread.tacv2"
    await store.upsert_channel({"id": cid, "team_id": "19:team@thread.tacv2", "files_drive_id": DRIVE,
                                "files_folder_item_id": FOLDER})
    await store.update_channel(cid, sync_error="boom")
    await store.upsert_channel({"id": cid, "team_id": "19:team@thread.tacv2"})  # nothing new: nothing lost
    [c] = await store.list_channels("19:team@thread.tacv2")
    assert (c["display_name"], c["files_drive_id"], c["files_folder_item_id"], c["sync_error"]) == \
        ("General", DRIVE, FOLDER, "boom")
    assert await store.list_channels("other") == []


async def test_document_upsert_events_and_soft_delete(store):
    await seed(store)
    i = await store.upsert_document(doc("a"))
    j = await store.upsert_document(doc("b", parent_item_id="sub", path="Lectures/b.pdf"))
    first = (await store.get_document(DRIVE, "a"))["first_seen_at"]
    assert await store.upsert_document(doc("a", name="renamed.pdf")) == i  # same row
    d = await store.get_document(DRIVE, "a")
    assert d["name"] == "renamed.pdf" and d["first_seen_at"] == first and d["is_folder"] is False
    assert (await store.get_document_by_id(j))["path"] == "Lectures/b.pdf"
    assert [x["item_id"] for x in await store.list_child_documents(DRIVE, "sub")] == ["b"]

    await store.add_document_event({"document_id": i, "event": "created", "at": T0, "new_name": "a.pdf"})
    await store.add_document_event({"document_id": i, "event": "renamed", "at": T0 + timedelta(minutes=1),
                                    "old_name": "a.pdf", "new_name": "renamed.pdf"})
    assert [e["event"] for e in await store.list_document_events(i)] == ["created", "renamed"]

    # b was last seen before the cutoff, a after it
    cutoff = datetime.now(timezone.utc) + timedelta(seconds=1)
    await asyncio.sleep(1.2)
    await store.upsert_document(doc("a"))
    gone = await store.soft_delete_unseen("19:chan@thread.tacv2", cutoff, T0)
    assert [g["item_id"] for g in gone] == ["b"]
    assert [x["item_id"] for x in await store.list_documents(channel_id="19:chan@thread.tacv2")] == ["a"]
    assert len(await store.list_documents(include_deleted=True)) == 2
    assert await store.soft_delete_unseen("19:chan@thread.tacv2", cutoff, T0) == []  # idempotent
    await store.upsert_document(doc("b", deleted_at=None))  # restored by a later sighting
    assert (await store.get_document(DRIVE, "b"))["deleted_at"] is None


async def test_sync_state_roundtrip_replace_and_delete(store):
    assert await store.get_sync_state("folder:c") is None
    await store.put_sync_state("folder:c", delta_link="https://x/delta?token=1", last_status="ok")
    await store.put_sync_state("backfill:c", cursor={"next": "https://x/next", "n": 5}, last_status="partial")
    s = await store.get_sync_state("backfill:c")
    assert s["cursor"] == {"next": "https://x/next", "n": 5} and s["delta_link"] is None
    await store.put_sync_state("folder:c", delta_link="https://x/delta?token=2")
    s = await store.get_sync_state("folder:c")
    assert s["delta_link"].endswith("token=2") and s["last_status"] is None  # replaced, not merged
    assert [r["key"] for r in await store.list_sync_state()] == ["backfill:c", "folder:c"]
    await store.delete_sync_state("folder:c")
    assert await store.get_sync_state("folder:c") is None


async def test_job_runs_latest_per_job(store):
    a = await store.start_job_run("discover")
    await store.finish_job_run(a, "ok", {"channels": 1})
    b = await store.start_job_run("discover")
    await store.finish_job_run(b, "partial", {"channels": 2}, "budget")
    c = await store.start_job_run("backfill")
    latest = {r["job"]: r for r in await store.latest_job_runs()}
    assert latest["discover"]["id"] == b and latest["discover"]["stats"] == {"channels": 2}
    assert latest["discover"]["status"] == "partial" and latest["backfill"]["status"] is None and latest["backfill"]["id"] == c


async def test_job_lock_is_exclusive_and_released(store):
    async with store.job_lock("sync-files") as first:
        assert first is True
        async with store.job_lock("sync-files") as second:
            assert second is False
        async with store.job_lock("backfill") as other:  # different job: independent
            assert other is True
    async with store.job_lock("sync-files") as again:
        assert again is True


async def test_job_lock_released_on_exception(store):
    with pytest.raises(RuntimeError):
        async with store.job_lock("discover") as got:
            assert got
            raise RuntimeError("boom")
    async with store.job_lock("discover") as got:
        assert got is True


async def test_attachment_links(store):
    await seed(store)
    from skim.storage.memory import MemoryStorage
    if isinstance(store, MemoryStorage):
        store.attachments.append({"id": 1, "conversation_id": "c", "message_id": "m", "content_type": "reference",
                                  "name": "a.pdf", "content_url": "https://x/a.pdf", "payload": {}})
    else:
        await store.pool.execute(
            "insert into attachments (conversation_id, message_id, content_type, name, content_url, payload) "
            "values ('c','m','reference','a.pdf','https://x/a.pdf','{}')")
    i = await store.upsert_document(doc("a"))
    att_id = 1 if isinstance(store, MemoryStorage) else await store.pool.fetchval("select id from attachments")
    await store.add_attachment_link(att_id, i, "web_url")
    await store.add_attachment_link(att_id, i, "web_url")  # idempotent
    links = await store.list_document_links(i)
    assert len(links) == 1 and links[0]["match_method"] == "web_url" and links[0]["name"] == "a.pdf"
