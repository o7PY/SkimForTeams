"""P2-D-143: live messages are enriched from the channel message delta feed, fill-only and bounded."""
import json
from pathlib import Path

from fake_graph import FakeGraph
from skim.bot import ingest
from skim.config import Settings
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.common import Budget, JobContext
from skim.jobs.enrich import enrich_channel, enrich_team, state_key
from skim.jobs.runner import error_text
from skim.storage.memory import MemoryStorage

FIX = Path(__file__).parent / "fixtures"
TEAM, CH, GID = "19:teamabc123@thread.tacv2", "19:chanGeneral@thread.tacv2", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MID = "1700000000060"
DELTA = f"/teams/{GID}/channels/{CH}/messages/delta"
SETTINGS = Settings.from_env({"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true",
                              "STORAGE": "memory", "PAIRING_SECRET": "s"})


def graph_copy():
    return json.loads((FIX / "graph" / "chatMessage_subject_and_text.json").read_text())


async def seed(store, live=True):
    await store.upsert_team({"id": TEAM, "aad_group_id": GID})
    await store.upsert_channel({"id": CH, "team_id": TEAM, "display_name": "General", "membership_type": "standard"})
    if live:
        await ingest(store, SETTINGS, json.loads((FIX / "channel_post_text_no_subject.json").read_text()))


async def run(store, fake, budget=None):
    async with fake.serve() as base:
        # saved links point at the previous fake server's port; the real host never changes
        for st in store.sync_state.values():
            for f in ("delta_link",):
                if st.get(f):
                    st[f] = base + st[f][st[f].index("/teams"):]
            if (st["cursor"] or {}).get("next"):
                st["cursor"]["next"] = base + st["cursor"]["next"][st["cursor"]["next"].index("/teams"):]
        g = GraphClient(StaticTokenProvider(), base_url=base)
        ctx = JobContext(store, g, budget or Budget(60))
        status = await enrich_team(ctx, {"id": TEAM, "aad_group_id": GID}, 30, error_text)
        await g.close()
    return status, ctx.stats


def feed(pages, final="D1"):
    f = FakeGraph()
    for i, p in enumerate(pages):
        body = {"value": p}
        last = i == len(pages) - 1
        body["@odata.deltaLink" if last else "@odata.nextLink"] = "{base}" + DELTA + f"?token={final if last else f'P{i + 2}'}"
        f.add(DELTA if i == 0 else f"{DELTA}?token=P{i + 1}", body)
    return f


async def test_live_post_gets_subject_importance_html_and_the_file_reference():
    store = MemoryStorage()
    await seed(store)
    before = (await store.get_message(CH, MID))["message"]
    assert before.get("subject") is None and before.get("importance") is None
    status, stats = await run(store, feed([[graph_copy()]]))
    assert status == "ok" and stats["enrich_filled"] == 1
    full = await store.get_message(CH, MID)
    m = full["message"]
    assert m["subject"] == "Assignment 3 deadline moved" and m["importance"] == "high"
    assert m["html"] == before["html"]  # the live html was there: kept
    assert m["text"] == before["text"] and m["current_version"] == before["current_version"]  # nothing overwritten
    assert [a["name"] for a in full["attachments"] if a["content_type"] == "reference"] == ["Assignment3_Brief.pdf"]
    assert len(full["versions"]) == 1


async def test_edited_live_text_is_never_overwritten_by_the_graph_copy():
    store = MemoryStorage()
    await seed(store)
    await ingest(store, SETTINGS, {**json.loads((FIX / "message_edit.json").read_text()), "id": MID, "type": "messageUpdate",
                                   "text": "My later edit"})
    assert (await store.get_message(CH, MID))["message"]["current_version"] == 2
    await run(store, feed([[graph_copy()]]))
    m = (await store.get_message(CH, MID))["message"]
    assert m["text"] == "My later edit" and m["current_version"] == 2 and m["subject"] == "Assignment 3 deadline moved"


async def test_the_delta_link_is_stored_and_the_next_run_uses_it_and_is_a_no_op():
    store = MemoryStorage()
    await seed(store)
    fake = feed([[graph_copy()]])
    await run(store, fake)
    assert store.sync_state[state_key(CH)]["delta_link"].endswith("?token=D1")
    first = fake.requests[0]
    assert first[0] == DELTA and "lastModifiedDateTime gt" in first[1]["$filter"]
    fake.add(f"{DELTA}", {"value": []})  # not used: the stored deltaLink has a token
    fake.add(f"{DELTA}?token=D1", {"value": [], "@odata.deltaLink": "{base}" + DELTA + "?token=D2"})
    snapshot = json.dumps((await store.get_message(CH, MID))["message"], default=str)
    status, stats = await run(store, fake)
    assert status == "ok" and fake.requests[-1][1] == {"token": "D1"} and stats["delta_pages"] == 1
    assert json.dumps((await store.get_message(CH, MID))["message"], default=str) == snapshot
    assert store.sync_state[state_key(CH)]["delta_link"].endswith("?token=D2")


async def test_paging_is_bounded_per_run_and_resumes_from_the_saved_next_link():
    import skim.jobs.enrich as enrich
    store = MemoryStorage()
    await seed(store, live=False)
    msgs = [{**graph_copy(), "id": f"m{i}"} for i in range(5)]
    fake = feed([[m] for m in msgs])
    old = enrich.PAGES_PER_CHANNEL
    enrich.PAGES_PER_CHANNEL = 2
    try:
        s1, st1 = await run(store, fake)
        assert s1 == "partial" and st1["delta_pages"] == 2 and store.sync_state[state_key(CH)]["cursor"]["next"]
        s2, st2 = await run(store, fake)
        s3, st3 = await run(store, fake)
    finally:
        enrich.PAGES_PER_CHANNEL = old
    assert (s2, s3) == ("partial", "ok") and st3["msgdelta_channels_done"] == 1
    assert len([k for k in store.messages]) == 5 and store.sync_state[state_key(CH)]["cursor"] is None


async def test_system_events_and_removed_items_are_skipped_and_an_expired_token_restarts():
    store = MemoryStorage()
    await seed(store, live=False)
    sysmsg = {**graph_copy(), "id": "s1", "messageType": "systemEventMessage"}
    status, stats = await run(store, feed([[sysmsg, {"id": "gone", "@removed": {"reason": "deleted"}}, graph_copy()]]))
    assert stats["delta_system_messages_skipped"] == 1 and {k[1] for k in store.messages} == {MID}
    fake = FakeGraph()
    fake.add(f"{DELTA}?token=D1", {"error": {"code": "syncStateNotFound"}}, status=410)
    store.sync_state[state_key(CH)]["delta_link"] = "http://x" + DELTA + "?token=D1"
    status, stats = await run(store, fake)
    assert status == "partial" and stats["msgdelta_resets"] == 1 and state_key(CH) not in store.sync_state


async def test_a_failing_channel_is_counted_and_does_not_raise():
    store = MemoryStorage()
    await seed(store, live=False)
    fake = FakeGraph()
    fake.add(DELTA, {"error": {"code": "Forbidden"}}, status=403)
    status, stats = await run(store, fake)
    assert status == "ok" and stats["enrich_errors"] == 1
