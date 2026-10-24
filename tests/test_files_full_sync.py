import pytest

from fake_graph import FakeGraph
from files_helpers import CH, DRIVE, FOLDER, TEAM, item, make_store, sync, tree
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.common import Budget, JobContext
from skim.jobs.files_sync import full_sync_channel


async def test_nested_folders_get_relative_paths_and_created_events():
    store = await make_store()
    status, ctx = await sync(tree(FakeGraph()), store)
    assert status == "ok"
    paths = {d["item_id"]: d["path"] for d in await store.list_documents(limit=100)}
    assert paths == {"L": "Lectures", "a": "syllabus.pdf", "w3": "Lectures/week3.pdf", "S": "Lectures/Slides",
                     "w4": "Lectures/week4.pdf", "s1": "Lectures/Slides/deep.pdf"}
    d = await store.get_document(DRIVE, "w3")
    assert (d["is_folder"], d["mime_type"], d["size_bytes"], d["created_by_name"], d["channel_id"], d["team_id"]) == \
        (False, "application/pdf", 10, "Prof", CH, TEAM)
    assert d["web_url_norm"] == "https://x.sharepoint.com/sites/t/shared documents/general/week3.pdf"
    assert (await store.get_document(DRIVE, "L"))["is_folder"] is True and (await store.get_document(DRIVE, "L"))["mime_type"] is None
    assert ctx.stats["created"] == 6 and ctx.stats["items_seen"] == 6
    for i in store.documents:
        assert [e["event"] for e in await store.list_document_events(i)] == ["created"]
    assert (await store.get_sync_state(f"folder:{CH}"))["delta_link"].endswith("token=START")
    assert await store.get_sync_state(f"fullsync:{CH}") is None
    assert store.channels[CH]["last_synced_at"] is not None


async def test_rerun_is_a_noop():
    store = await make_store()
    await sync(tree(FakeGraph()), store)
    snapshot = ({i: {k: v for k, v in d.items() if k != "last_seen_at"} for i, d in store.documents.items()},
                len(store.document_events), len(store.graph_raw))
    status, ctx = await sync(tree(FakeGraph()), store)
    after = ({i: {k: v for k, v in d.items() if k != "last_seen_at"} for i, d in store.documents.items()},
             len(store.document_events), len(store.graph_raw))
    assert status == "ok" and after == snapshot
    assert ctx.stats["unchanged"] == 6 and not any(ctx.stats[k] for k in ("created", "modified", "renamed", "moved", "deleted", "restored"))


async def test_missing_items_are_soft_deleted_then_restored():
    store = await make_store()
    await sync(tree(FakeGraph()), store)
    fake = FakeGraph()
    tree(fake)
    fake.add(f"/drives/{DRIVE}/items/{FOLDER}/children", {"value": [item("L", "Lectures", FOLDER, True)]})  # syllabus gone
    _, ctx = await sync(fake, store)
    assert ctx.stats["deleted"] == 1
    a = await store.get_document(DRIVE, "a")
    assert a["deleted_at"] is not None
    assert [e["event"] for e in await store.list_document_events(a["id"])] == ["created", "deleted"]
    assert "a" not in {d["item_id"] for d in await store.list_documents()}
    _, ctx = await sync(tree(FakeGraph()), store)  # back again
    assert ctx.stats["restored"] == 1 and (await store.get_document(DRIVE, "a"))["deleted_at"] is None
    assert [e["event"] for e in await store.list_document_events(a["id"])] == ["created", "deleted", "restored"]


async def test_changes_between_runs_produce_modified_renamed_moved():
    store = await make_store()
    await sync(tree(FakeGraph()), store)
    fake = tree(FakeGraph())
    fake.add(f"/drives/{DRIVE}/items/{FOLDER}/children", {"value": [
        item("L", "Lectures", FOLDER, True),
        item("a", "syllabus-v2.pdf", FOLDER, etag="e1")]})              # renamed
    fake.add(f"/drives/{DRIVE}/items/L/children", {"value": [
        item("w3", "week3.pdf", "L", etag="e2"),                         # modified
        item("S", "Slides", "L", True)]})
    fake.add(f"/drives/{DRIVE}/items/S/children", {"value": [item("s1", "deep.pdf", "S"), item("w4", "week4.pdf", "S")]})  # w4 moved into Slides
    fake.routes.pop("/page2")
    _, ctx = await sync(fake, store)
    by = {d["item_id"]: [e["event"] for e in store.document_events if e["document_id"] == d["id"]] for d in store.documents.values()}
    assert by["a"] == ["created", "renamed"] and by["w3"] == ["created", "modified"] and by["w4"] == ["created", "moved"]
    assert (await store.get_document(DRIVE, "w4"))["path"] == "Lectures/Slides/week4.pdf"
    assert (await store.get_document(DRIVE, "a"))["path"] == "syllabus-v2.pdf"
    assert by["L"] == ["created"] and by["S"] == ["created"]  # folders: no "modified" noise


async def test_partial_run_resumes_without_duplicates():
    store = await make_store()
    # budget already expired => saves the cursor immediately; then finish with a real budget
    status, _ = await sync(tree(FakeGraph()), store, budget=-1)
    assert status == "partial" and (await store.get_sync_state(f"fullsync:{CH}"))["cursor"]["queue"] == [[FOLDER, ""]]
    assert await store.get_sync_state(f"folder:{CH}") is None  # delta link only after completion
    status, _ = await sync(tree(FakeGraph()), store)
    assert status == "ok" and len(store.documents) == 6
    assert sum(1 for e in store.document_events if e["event"] == "created") == 6


async def test_partial_after_some_folders_resumes_where_it_stopped():
    store = await make_store()
    fake = tree(FakeGraph())
    ticks = iter([False, True])  # first loop iteration allowed, second stops
    async with fake.serve() as base:
        g = GraphClient(StaticTokenProvider(), base_url=base)
        b = Budget(60)
        b.expired = lambda: next(ticks, True)
        ctx = JobContext(store, g, b)
        assert await full_sync_channel(ctx, store.channels[CH]) == "partial"
        await g.close()
    cur = (await store.get_sync_state(f"fullsync:{CH}"))["cursor"]
    assert [q[0] for q in cur["queue"]] == ["L"]  # root listed, Lectures pending
    await sync(tree(FakeGraph()), store)
    assert len(store.documents) == 6 and sum(1 for e in store.document_events if e["event"] == "created") == 6


async def test_no_file_bytes_requested():
    store = await make_store()
    fake = tree(FakeGraph())
    await sync(fake, store)
    assert not any(p.endswith("/content") for p, _, _ in fake.requests)


async def test_download_urls_are_stripped_from_raw():
    store = await make_store()
    fake = tree(FakeGraph())
    leaky = item("a", "syllabus.pdf", FOLDER)
    leaky["@microsoft.graph.downloadUrl"] = "https://x/download?tempauth=SECRET"
    fake.add(f"/drives/{DRIVE}/items/{FOLDER}/children", {"value": [item("L", "Lectures", FOLDER, True), leaky]})
    await sync(fake, store)
    assert "SECRET" not in str(store.graph_raw) and "downloadUrl" not in str(store.graph_raw)
