from urllib.parse import urlsplit

import pytest

from fake_graph import FakeGraph
from files_helpers import CH, DRIVE, FOLDER, TEAM, item, make_store, sync, tree
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.common import Budget, JobContext
from skim.jobs.files_sync import delta_sync_channel

D = f"/drives/{DRIVE}/items/{FOLDER}/delta"
ROOT = f"/drives/{DRIVE}/root/delta"


async def synced():
    store = await make_store()
    await sync(tree(FakeGraph()), store)
    return store


async def delta(fake, store, budget=60, key=f"folder:{CH}"):
    """Run one delta pass. The stored links point at a previous fake server's port, so re-aim them."""
    async with fake.serve() as base:
        for k in list(store.sync_state):
            st = store.sync_state[k]
            for f in ("delta_link",):
                if st[f]:
                    st[f] = base + urlsplit(st[f]).path + ("?" + urlsplit(st[f]).query if urlsplit(st[f]).query else "")
            if st["cursor"] and st["cursor"].get("next"):
                u = urlsplit(st["cursor"]["next"])
                st["cursor"]["next"] = base + u.path + ("?" + u.query if u.query else "")
        g = GraphClient(StaticTokenProvider(), base_url=base)
        ctx = JobContext(store, g, Budget(budget))
        status = await delta_sync_channel(ctx, store.channels[CH])
        await g.close()
    return status, ctx


def feed(fake, *pages, path=D, token="START"):
    """pages: lists of items. Served as START -> P2 -> ... -> final page with a deltaLink."""
    names = [token] + [f"P{i + 2}" for i in range(len(pages) - 1)]
    for i, items in enumerate(pages):
        body = {"value": items}
        if i < len(pages) - 1:
            body["@odata.nextLink"] = "{base}" + path + f"?token={names[i + 1]}"
        else:
            body["@odata.deltaLink"] = "{base}" + path + "?token=NEXT"
        fake.add(f"{path}?token={names[i]}", body)
    return fake


def events(store, item_id):
    d = next(d for d in store.documents.values() if d["item_id"] == item_id)
    return [e["event"] for e in store.document_events if e["document_id"] == d["id"]]


async def test_needs_full_without_state():
    store = await make_store()
    status, _ = await delta(FakeGraph(), store)
    assert status == "needs_full"


async def test_create_modify_rename_and_new_link_saved():
    store = await synced()
    fake = feed(FakeGraph(), [
        item(FOLDER, "General", "root", True),                                   # the channel folder itself: ignored
        item("new", "notes.pdf", FOLDER),                                        # created
        item("w3", "week3.pdf", "L", etag="e2"),                                 # modified
        item("a", "syllabus-final.pdf", FOLDER)])                                # renamed
    status, ctx = await delta(fake, store)
    assert status == "ok"
    assert events(store, "new") == ["created"] and events(store, "w3") == ["created", "modified"]
    assert events(store, "a") == ["created", "renamed"] and (await store.get_document(DRIVE, "a"))["path"] == "syllabus-final.pdf"
    assert await store.get_document(DRIVE, FOLDER) is None
    assert (await store.get_sync_state(f"folder:{CH}"))["delta_link"].endswith("token=NEXT")


async def test_replaying_a_delta_changes_nothing():
    store = await synced()
    page = [item("new", "notes.pdf", FOLDER), item("w3", "week3.pdf", "L", etag="e2")]
    await delta(feed(FakeGraph(), page), store)
    n = len(store.document_events)
    _, ctx = await delta(feed(FakeGraph(), page, token="NEXT"), store)  # same content again, from the new link
    assert len(store.document_events) == n and ctx.stats["unchanged"] == 2


async def test_folder_rename_rewrites_descendant_paths():
    store = await synced()
    fake = feed(FakeGraph(), [item("L", "Course Notes", FOLDER, True)])
    await delta(fake, store)
    paths = {d["item_id"]: d["path"] for d in store.documents.values()}
    assert paths["L"] == "Course Notes" and paths["w3"] == "Course Notes/week3.pdf" and paths["s1"] == "Course Notes/Slides/deep.pdf"
    assert events(store, "L") == ["created", "renamed"] and events(store, "w3") == ["created"]  # no descendant events


async def test_folder_move_rewrites_descendants_and_logs_moved():
    store = await synced()
    fake = feed(FakeGraph(), [item("S", "Slides", FOLDER, True)])  # Slides moved from Lectures to the top
    await delta(fake, store)
    assert events(store, "L") == ["created"] and events(store, "S") == ["created", "moved"]
    assert (await store.get_document(DRIVE, "s1"))["path"] == "Slides/deep.pdf"


async def test_child_before_parent_in_same_page_resolves_through_the_page():
    store = await synced()
    fake = feed(FakeGraph(), [item("c", "x.pdf", "N"), item("N", "NewDir", FOLDER, True)])
    await delta(fake, store)
    assert (await store.get_document(DRIVE, "c"))["path"] == "NewDir/x.pdf"


async def test_unknown_parent_is_fetched_by_id_and_cached():
    store = await synced()
    fake = feed(FakeGraph(), [item("c1", "x.pdf", "P"), item("c2", "y.pdf", "P")])
    fake.add(f"/drives/{DRIVE}/items/P", {"id": "P", "name": "Older", "parentReference": {"driveId": DRIVE, "id": FOLDER}})
    await delta(fake, store)
    assert (await store.get_document(DRIVE, "c2"))["path"] == "Older/y.pdf"
    assert fake.count(f"/drives/{DRIVE}/items/P") == 1


async def test_deleted_item_that_is_gone_is_soft_deleted_and_can_be_restored():
    store = await synced()
    fake = feed(FakeGraph(), [{"id": "a", "deleted": {"state": "deleted"}, "parentReference": {"driveId": DRIVE, "id": FOLDER}}])
    fake.add(f"/drives/{DRIVE}/items/a", {"error": {"code": "itemNotFound"}}, status=404)
    await delta(fake, store)
    assert (await store.get_document(DRIVE, "a"))["deleted_at"] is not None and events(store, "a") == ["created", "deleted"]
    await delta(feed(FakeGraph(), [item("a", "syllabus.pdf", FOLDER)], token="NEXT"), store)
    assert (await store.get_document(DRIVE, "a"))["deleted_at"] is None and events(store, "a") == ["created", "deleted", "restored"]


async def test_deleted_folder_cascades_to_children():
    store = await synced()
    fake = feed(FakeGraph(), [{"id": "L", "deleted": {}, "parentReference": {"driveId": DRIVE, "id": FOLDER}}])
    fake.add(f"/drives/{DRIVE}/items/L", {"error": {"code": "itemNotFound"}}, status=404)
    await delta(fake, store)
    live = {d["item_id"] for d in await store.list_documents()}
    assert live == {"a"} and events(store, "s1") == ["created", "deleted"]


async def test_moved_out_is_not_deleted_and_channel_is_nulled():
    store = await synced()
    fake = feed(FakeGraph(), [{"id": "a", "deleted": {}, "parentReference": {"driveId": DRIVE, "id": FOLDER}}])
    fake.add(f"/drives/{DRIVE}/items/a", {"id": "a"})  # still exists elsewhere
    await delta(fake, store)
    a = await store.get_document(DRIVE, "a")
    assert a["deleted_at"] is None and a["channel_id"] is None and a["path"] is None and events(store, "a") == ["created", "moved"]
    assert "a" not in {d["item_id"] for d in await store.list_documents(channel_id=CH)}


async def test_moved_out_as_live_item_with_foreign_parent():
    store = await synced()
    fake = feed(FakeGraph(), [item("a", "syllabus.pdf", "ELSEWHERE")])
    fake.add(f"/drives/{DRIVE}/items/ELSEWHERE", {"id": "ELSEWHERE", "name": "Other", "parentReference": {"driveId": DRIVE, "id": "ROOTX"}})
    fake.add(f"/drives/{DRIVE}/items/ROOTX", {"id": "ROOTX", "name": "root"})  # no parent: the drive root
    await delta(fake, store)
    assert (await store.get_document(DRIVE, "a"))["channel_id"] is None and events(store, "a") == ["created", "moved"]


async def test_moved_in_again_after_moving_out():
    store = await synced()
    fake = feed(FakeGraph(), [{"id": "a", "deleted": {}, "parentReference": {"driveId": DRIVE, "id": FOLDER}}])
    fake.add(f"/drives/{DRIVE}/items/a", {"id": "a"})
    await delta(fake, store)
    await delta(feed(FakeGraph(), [item("a", "syllabus.pdf", FOLDER)], token="NEXT"), store)
    a = await store.get_document(DRIVE, "a")
    assert a["channel_id"] == CH and a["path"] == "syllabus.pdf" and events(store, "a") == ["created", "moved", "moved"]


async def test_410_clears_state_and_does_a_full_resync():
    store = await synced()
    fake = tree(FakeGraph())
    fake.add(f"{D}?token=START", {"error": {"code": "resyncRequired"}}, status=410)
    fake.add(f"/drives/{DRIVE}/items/{FOLDER}/children", {"value": [item("L", "Lectures", FOLDER, True)]})  # only Lectures remain
    status, ctx = await delta(fake, store)
    assert status == "ok" and ctx.stats["resyncs"] == 1 and ctx.stats["full_syncs"] == 1
    assert (await store.get_document(DRIVE, "a"))["deleted_at"] is not None
    assert (await store.get_sync_state(f"folder:{CH}"))["delta_link"].endswith("token=START")


async def test_folder_delta_unsupported_falls_back_to_root_delta_with_filtering():
    store = await synced()
    fake = tree(FakeGraph())
    fake.add(f"{D}?token=START", {"error": {"code": "notSupported"}}, status=501)
    fake.add(ROOT, {"value": [], "@odata.deltaLink": "{base}" + ROOT + "?token=R1"})
    status, ctx = await delta(fake, store)
    assert status == "ok" and ctx.stats["delta_fallbacks"] == 1
    assert await store.get_sync_state(f"folder:{CH}") is None
    assert (await store.get_sync_state(f"root:{CH}"))["delta_link"].endswith("token=R1")
    # now the root feed: one in-scope change, one item elsewhere in the library
    fake2 = feed(FakeGraph(), [item("a", "syllabus-r.pdf", FOLDER), item("zz", "other.pdf", "OTHERDIR")], path=ROOT, token="R1")
    fake2.add(f"/drives/{DRIVE}/items/OTHERDIR", {"id": "OTHERDIR", "name": "Other", "parentReference": {"driveId": DRIVE, "id": "R"}})
    fake2.add(f"/drives/{DRIVE}/items/R", {"id": "R", "name": "root"})
    status, ctx = await delta(fake2, store)
    assert status == "ok" and events(store, "a") == ["created", "renamed"]
    assert await store.get_document(DRIVE, "zz") is None and ctx.stats["out_of_scope"] == 1


async def test_partial_run_resumes_from_saved_next_link_and_keeps_old_link_until_done():
    store = await synced()
    old = store.sync_state[f"folder:{CH}"]["delta_link"]
    fake = feed(FakeGraph(), [item("n1", "one.pdf", FOLDER)], [item("n2", "two.pdf", FOLDER)])
    status, _ = await delta(fake, store, budget=-1)
    assert status == "partial"
    st = store.sync_state[f"folder:{CH}"]
    assert st["delta_link"].endswith("token=START") and st["cursor"]["next"].endswith("token=P2")
    assert await store.get_document(DRIVE, "n1") and await store.get_document(DRIVE, "n2") is None
    status, _ = await delta(feed(FakeGraph(), [item("n1", "one.pdf", FOLDER)], [item("n2", "two.pdf", FOLDER)]), store)
    assert status == "ok" and events(store, "n1") == ["created"] and events(store, "n2") == ["created"]
    st = store.sync_state[f"folder:{CH}"]
    assert st["delta_link"].endswith("token=NEXT") and st["cursor"] is None


async def test_unfinished_full_sync_takes_precedence():
    store = await synced()
    await store.put_sync_state(f"fullsync:{CH}", cursor={"queue": [], "started_at": "2026-10-24T00:00:00+00:00"})
    status, _ = await delta(FakeGraph(), store)
    assert status == "needs_full"


async def test_other_graph_errors_propagate_and_leave_state_untouched():
    store = await synced()
    link = store.sync_state[f"folder:{CH}"]["delta_link"]
    fake = FakeGraph().add(f"{D}?token=START", {"error": {"code": "accessDenied"}}, status=403)
    from skim.graph.auth import GraphAuthError
    with pytest.raises(GraphAuthError):
        await delta(fake, store)
    assert store.sync_state[f"folder:{CH}"]["delta_link"].endswith("token=START")


# ---- net-state semantics (delta reports the net result between two syncs, not the history)
async def test_delete_and_restore_between_syncs_is_one_modified_event():
    """Item deleted and restored from the recycle bin between two syncs: delta only shows the live item with a new
    eTag, so the log says `modified`, never deleted/restored (P2 doc: delta is net state)."""
    store = await synced()
    await delta(feed(FakeGraph(), [item("w3", "week3.pdf", "L", etag="e2")]), store)
    assert events(store, "w3") == ["created", "modified"]
    assert (await store.get_document(DRIVE, "w3"))["deleted_at"] is None


async def test_delete_and_restore_between_syncs_with_same_etag_is_nothing():
    store = await synced()
    _, ctx = await delta(feed(FakeGraph(), [item("w3", "week3.pdf", "L")]), store)
    assert events(store, "w3") == ["created"] and ctx.stats["unchanged"] == 1


async def test_rename_and_rename_back_between_syncs_is_modified_or_nothing():
    store = await synced()
    await delta(feed(FakeGraph(), [item("a", "syllabus.pdf", FOLDER, etag="e2")]), store)  # same name, new eTag
    assert events(store, "a") == ["created", "modified"]
    _, ctx = await delta(feed(FakeGraph(), [item("a", "syllabus.pdf", FOLDER, etag="e2")], token="NEXT"), store)
    assert events(store, "a") == ["created", "modified"] and ctx.stats["unchanged"] == 1


# ---- event payloads and folder cascades
async def test_renamed_and_moved_events_store_old_and_new_name_and_path():
    store = await synced()
    await delta(feed(FakeGraph(), [item("w3", "week3-final.pdf", "L")]), store)       # renamed in place
    await delta(feed(FakeGraph(), [item("w3", "week3-final.pdf", "S")], token="NEXT"), store)  # moved into Slides
    d = await store.get_document(DRIVE, "w3")
    ren, mov = [e for e in await store.list_document_events(d["id"]) if e["event"] in ("renamed", "moved")]
    assert (ren["old_name"], ren["new_name"], ren["old_path"], ren["new_path"]) == (
        "week3.pdf", "week3-final.pdf", "Lectures/week3.pdf", "Lectures/week3-final.pdf")
    assert (mov["event"], mov["old_name"], mov["new_name"], mov["old_path"], mov["new_path"]) == (
        "moved", "week3-final.pdf", "week3-final.pdf", "Lectures/week3-final.pdf", "Lectures/Slides/week3-final.pdf")


async def test_rename_and_move_in_one_change_logs_both_events_with_paths():
    store = await synced()
    await delta(feed(FakeGraph(), [item("a", "renamed.pdf", "L")]), store)
    d = await store.get_document(DRIVE, "a")
    evs = {e["event"]: e for e in await store.list_document_events(d["id"])}
    assert evs["renamed"]["old_name"] == "syllabus.pdf" and evs["renamed"]["new_name"] == "renamed.pdf"
    assert evs["moved"]["old_path"] == "syllabus.pdf" and evs["moved"]["new_path"] == "Lectures/renamed.pdf"


async def test_deleted_folder_marks_children_deleted_with_paths_and_restore_brings_them_back():
    store = await synced()
    gone = {"id": "L", "deleted": {}, "parentReference": {"driveId": DRIVE, "id": FOLDER}}
    fake = feed(FakeGraph(), [gone])
    fake.add(f"/drives/{DRIVE}/items/L", {"error": {"code": "itemNotFound"}}, status=404)
    await delta(fake, store)
    for iid in ("L", "w3", "w4", "S", "s1"):
        assert (await store.get_document(DRIVE, iid))["deleted_at"] is not None, iid
    ev = next(e for e in await store.list_document_events((await store.get_document(DRIVE, "s1"))["id"]) if e["event"] == "deleted")
    assert ev["old_path"] == "Lectures/Slides/deep.pdf" and ev["old_name"] == "deep.pdf"
    assert {d["item_id"] for d in await store.list_documents()} == {"a"}

    # the folder comes back from the recycle bin; Graph reports only the folder itself
    await delta(feed(FakeGraph(), [item("L", "Lectures", FOLDER, True)], token="NEXT"), store)
    live = {d["item_id"]: d["path"] for d in await store.list_documents()}
    assert live == {"a": "syllabus.pdf", "L": "Lectures", "w3": "Lectures/week3.pdf", "w4": "Lectures/week4.pdf",
                    "S": "Lectures/Slides", "s1": "Lectures/Slides/deep.pdf"}
    assert events(store, "s1") == ["created", "deleted", "restored"]


async def test_restored_folder_under_a_new_name_rewrites_restored_children_paths():
    store = await synced()
    fake = feed(FakeGraph(), [{"id": "L", "deleted": {}, "parentReference": {"driveId": DRIVE, "id": FOLDER}}])
    fake.add(f"/drives/{DRIVE}/items/L", {"error": {"code": "itemNotFound"}}, status=404)
    await delta(fake, store)
    await delta(feed(FakeGraph(), [item("L", "Course", FOLDER, True)], token="NEXT"), store)
    assert (await store.get_document(DRIVE, "s1"))["path"] == "Course/Slides/deep.pdf"


async def test_child_deleted_earlier_stays_deleted_when_the_folder_is_restored():
    store = await synced()
    fake = feed(FakeGraph(), [{"id": "w4", "deleted": {}, "parentReference": {"driveId": DRIVE, "id": "L"}}])
    fake.add(f"/drives/{DRIVE}/items/w4", {"error": {"code": "itemNotFound"}}, status=404)
    await delta(fake, store)
    fake = feed(FakeGraph(), [{"id": "L", "deleted": {}, "parentReference": {"driveId": DRIVE, "id": FOLDER}}], token="NEXT")
    fake.add(f"/drives/{DRIVE}/items/L", {"error": {"code": "itemNotFound"}}, status=404)
    await delta(fake, store)
    await delta(feed(FakeGraph(), [item("L", "Lectures", FOLDER, True)], token="NEXT"), store)
    assert (await store.get_document(DRIVE, "w4"))["deleted_at"] is not None
    assert (await store.get_document(DRIVE, "w3"))["deleted_at"] is None
