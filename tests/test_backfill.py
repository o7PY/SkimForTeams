"""T8: chatMessage normalisation + backfill job (FR-205, P2-D-114/115)."""
import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fake_graph import FakeGraph
from skim.bot import ingest
from skim.config import Settings
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.backfill import backfill_channel, backfill_team
from skim.jobs.common import Budget, JobContext
from skim.jobs.runner import error_text
from skim.normalize_graph import chat_message_row, fill_empty, html_to_text
from skim.storage.memory import MemoryStorage

GRAPH_FIX = Path(__file__).parent / "fixtures" / "graph"
TEAM, GID = "19:team@thread.tacv2", "gid-1"
CH = "19:chan@thread.tacv2"
BASE = f"/teams/{GID}/channels/{CH}/messages"
NOW = datetime.now(timezone.utc)


def iso(days_ago=0.0):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def msg(mid, days_ago=1, text="hello", **kw):
    m = {"id": mid, "replyToId": None, "messageType": "message", "createdDateTime": iso(days_ago),
         "lastModifiedDateTime": iso(days_ago), "deletedDateTime": None, "subject": None, "importance": "normal",
         "from": {"user": {"id": "u-1", "displayName": "Test Prof"}},
         "body": {"contentType": "html", "content": f"<p>{text}</p>"}, "mentions": [], "reactions": [], "attachments": []}
    m.update(kw)
    return m


class CountBudget:
    """Expires after n checks: lets a test interrupt a run at a precise point."""
    def __init__(self, n):
        self.n = n

    def expired(self):
        self.n -= 1
        return self.n < 0


async def make_store(channels=(CH,)):
    s = MemoryStorage()
    await s.upsert_team({"id": TEAM, "aad_group_id": GID})
    for c in channels:
        await s.upsert_channel({"id": c, "team_id": TEAM, "display_name": "General", "membership_type": "standard"})
    return s


def reaim(store, base):
    """A saved nextLink points at the previous fake server's port; the real host never changes."""
    from urllib.parse import urlsplit
    for st in store.sync_state.values():
        nxt = (st["cursor"] or {}).get("next")
        if nxt:
            u = urlsplit(nxt)
            st["cursor"]["next"] = base + u.path + ("?" + u.query if u.query else "")


async def run(fake, store, *, budget=None, since=180, cap=2000, channel=CH):
    async with fake.serve() as base:
        reaim(store, base)
        g = GraphClient(StaticTokenProvider(), base_url=base, sleep=lambda s: asyncio.sleep(0))
        ctx = JobContext(store, g, budget or Budget(60))
        team = store.teams[TEAM]
        status = await backfill_channel(ctx, team, store.channels[channel], since, cap)
        await g.close()
    return status, ctx


def first_pages(fake):
    return sum(1 for p, q, _ in fake.requests if p == BASE and not q.get("token"))


def tenant(top, replies=None, pages=None):
    """top: list of top-level messages, served as one page (or `pages`, a list of lists, chained by nextLink)."""
    f = FakeGraph()
    pages = pages or [top]
    for i, p in enumerate(pages):
        body = {"value": p}
        if i < len(pages) - 1:
            body["@odata.nextLink"] = "{base}" + BASE + f"?token=P{i + 2}"
        f.add(BASE if i == 0 else f"{BASE}?token=P{i + 1}", body)
    for mid, rs in (replies or {}).items():
        f.add(f"{BASE}/{mid}/replies", {"value": rs})
    return f


# ------------------------------------------------------------------ normalisation
def test_html_to_text_drops_mentions_tags_and_entities():
    assert html_to_text('<p><at id="0">Skim</at> Midterm &amp; quiz</p><p>Nov 3</p>') == "Midterm & quiz\nNov 3"


def test_chat_message_row_fields_subject_reply_attachments_reactions():
    fix = json.loads((GRAPH_FIX / "chatMessage_subject.json").read_text()) if (GRAPH_FIX / "chatMessage_subject.json").exists() else {}
    row = chat_message_row({**msg("m1", text="Midterm"), "subject": "Exam", "replyToId": "root", "importance": "high",
                            "attachments": [{"id": "a", "contentType": "reference", "contentUrl": "https://x/y.pdf", "name": "y.pdf"}],
                            "reactions": [{"reactionType": "like", "createdDateTime": iso(1), "user": {"user": {"id": "u2", "displayName": "S"}}}]}, CH)
    m = row["message"]
    assert (m["message_id"], m["conversation_id"], m["subject"], m["reply_to_id"], m["importance"]) == ("m1", CH, "Exam", "root", "high")
    assert m["text"] == "Midterm" and m["html"] == "<p>Midterm</p>" and m["from_aad_id"] == "u-1" and m["from_name"] == "Test Prof"
    assert row["attachments"][0]["content_url"] == "https://x/y.pdf" and row["attachments"][0]["payload"]["id"] == "a"
    assert row["reactions"][0]["reaction_type"] == "like" and row["reactions"][0]["action"] == "added"
    assert fix  # the sanitised fixture from the spike still parses
    assert chat_message_row(fix, CH)["message"]["subject"]


def test_system_event_messages_are_not_messages():
    assert chat_message_row({**msg("s1"), "messageType": "systemEventMessage"}, CH) is None


def test_fill_empty_never_overwrites():
    before = {"text": "live", "subject": None, "html": None, "from_name": "A"}
    new = {"text": "graph", "subject": "S", "html": "<p>g</p>", "from_name": "B", "importance": None}
    assert fill_empty(before, new) == {"subject": "S", "html": "<p>g</p>"}


# ------------------------------------------------------------------ job
async def test_top_level_replies_and_announcement_subject_are_stored_as_graph_source():
    store = await make_store()
    reply = msg("r1", text="a reply", replyToId="m1")
    fake = tenant([msg("m1", 2, "Welcome", subject="Welcome to the course"), msg("m2", 1, "Second")], {"m1": [reply], "m2": []})
    status, ctx = await run(fake, store)
    assert status == "ok"
    got = {k[1]: v for k, v in store.messages.items()}
    assert set(got) == {"m1", "m2", "r1"} and all(v["source"] == "graph" for v in got.values())
    assert got["m1"]["subject"] == "Welcome to the course" and got["r1"]["reply_to_id"] == "m1" and got["r1"]["text"] == "a reply"
    assert ctx.stats["messages_created"] == 3 and ctx.stats["replies_stored"] == 1
    assert store.conversations[CH]["team_id"] == TEAM
    assert {r["kind"] for r in store.graph_raw} == {"chatMessage"} and len(store.graph_raw) == 3
    assert len([v for v in store.versions if v["message_id"] == "m1"]) == 1


async def test_deleted_message_gets_deleted_at():
    store = await make_store()
    fake = tenant([msg("d1", 1, "", deletedDateTime=iso(0.5), body={"contentType": "html", "content": ""})], {"d1": []})
    _, ctx = await run(fake, store)
    m = store.messages[(CH, "d1")]
    assert m["deleted_at"] is not None and ctx.stats["messages_deleted_seen"] == 1


async def test_system_events_are_skipped_but_kept_raw():
    store = await make_store()
    fake = tenant([{**msg("s1"), "messageType": "systemEventMessage"}, msg("m1")], {"m1": []})
    _, ctx = await run(fake, store)
    assert set(k[1] for k in store.messages) == {"m1"} and ctx.stats["system_messages_skipped"] == 1


async def test_live_rows_are_never_overwritten_only_empty_fields_filled(load):
    store = await make_store()
    settings = Settings.from_env({"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true", "STORAGE": "memory", "PAIRING_SECRET": "s"})
    await ingest(store, settings, load("channel_post"))
    live_cid, live_mid = "19:chanGeneral@thread.tacv2", "1700000000001"
    before = copy.deepcopy(store.messages[(live_cid, live_mid)])
    store.channels[live_cid] = {**store.channels[CH], "id": live_cid}
    fake = FakeGraph()
    live_base = f"/teams/{GID}/channels/{live_cid}/messages"
    fake.add(live_base, {"value": [msg(live_mid, 3, "GRAPH VERSION OF THE TEXT", subject="Midterm notice", importance="high")]})
    fake.add(f"{live_base}/{live_mid}/replies", {"value": []})
    async with fake.serve() as b:
        g = GraphClient(StaticTokenProvider(), base_url=b)
        ctx = JobContext(store, g, Budget(60))
        await backfill_channel(ctx, store.teams[TEAM], store.channels[live_cid], 180, 100)
        await g.close()
    after = store.messages[(live_cid, live_mid)]
    for col in ("text", "html", "from_name", "from_aad_id", "created_at", "current_version", "raw_activity_id"):
        assert after[col] == before[col], col
    assert after.get("source", "live") != "graph"
    assert after["subject"] == "Midterm notice" and after["importance"] == "high"  # live value was empty -> filled
    assert after["backfilled_at"] is not None  # ... and noted
    assert ctx.stats["messages_filled"] == 1 and ctx.stats["messages_created"] == 0
    assert len([v for v in store.versions if v["message_id"] == live_mid]) == 1  # no extra version


async def test_rerun_is_idempotent():
    store = await make_store()
    fake = tenant([msg("m1", 2), msg("m2", 1)], {"m1": [msg("r1", 1, replyToId="m1")], "m2": []})
    await run(fake, store)
    snapshot = copy.deepcopy(store.messages)
    _, ctx = await run(fake, store)
    assert store.messages == snapshot and ctx.stats["messages_unchanged"] == 3 and ctx.stats["messages_created"] == 0


async def test_old_messages_are_skipped_and_paging_stops_at_the_cutoff():
    store = await make_store()
    fake = tenant(None, {"n1": [], "n2": []}, pages=[[msg("n1", 1), msg("n2", 5)], [msg("old1", 400)], [msg("old2", 500)]])
    _, ctx = await run(fake, store, since=30)
    assert set(k[1] for k in store.messages) == {"n1", "n2"}
    assert first_pages(fake) == 1 and sum(1 for p, q, _ in fake.requests if q.get("token")) == 1  # page 3 never fetched
    assert ctx.stats["messages_before_cutoff"] == 1


async def test_max_messages_per_channel_caps_the_backfill():
    store = await make_store()
    ms = [msg(f"m{i}", i + 1) for i in range(5)]
    fake = tenant(ms, {m["id"]: [] for m in ms})
    status, ctx = await run(fake, store, cap=3)
    assert status == "ok" and len(store.messages) == 3 and ctx.stats["capped_channels"] == 1


async def test_resumes_after_budget_runs_out_mid_replies_without_duplicates():
    store = await make_store()
    ms = [msg("m1", 3), msg("m2", 2), msg("m3", 1)]
    fake = tenant(None, {m["id"]: [msg("r" + m["id"], 1, replyToId=m["id"])] for m in ms}, pages=[ms[:2], ms[2:]])
    status, ctx1 = await run(fake, store, budget=CountBudget(2))  # page 1 stored, one set of replies, then out of time
    assert status == "partial"
    st = await store.get_sync_state(f"backfill:{CH}")
    assert st["cursor"]["pending"] and st["last_status"] == "partial"
    before = len(store.messages)
    status, ctx2 = await run(fake, store)
    assert status == "ok" and len(store.messages) == 6 and before < 6
    assert all(fake.count(f"{BASE}/{m}/replies") == 1 for m in ("m1", "m2", "m3"))  # nobody's replies fetched twice
    assert (await store.get_sync_state(f"backfill:{CH}"))["cursor"] is None
    assert all(sum(1 for v in store.versions if v["message_id"] == k[1]) == 1 for k in store.messages)


async def test_resume_continues_from_the_saved_next_page_not_from_the_start():
    store = await make_store()
    fake = tenant(None, {"a": [], "b": []}, pages=[[msg("a", 2)], [msg("b", 1)]])
    status, _ = await run(fake, store, budget=CountBudget(2))  # page 1 + its replies, then expires before page 2
    assert status == "partial" and (await store.get_sync_state(f"backfill:{CH}"))["cursor"]["next"]
    await run(fake, store)
    assert first_pages(fake) == 1  # the first page was fetched once only
    assert set(k[1] for k in store.messages) == {"a", "b"}


# ------------------------------------------------------------------ team level
async def run_team(fake, store, **kw):
    async with fake.serve() as base:
        reaim(store, base)
        g = GraphClient(StaticTokenProvider(), base_url=base, sleep=lambda s: asyncio.sleep(0))
        ctx = JobContext(store, g, kw.pop("budget", None) or Budget(60))
        status = await backfill_team(ctx, store.teams[TEAM], 180, 2000, error_text)
        await g.close()
    return status, ctx


async def test_needs_initial_sync_is_cleared_only_after_every_channel_finished():
    other = "19:other@thread.tacv2"
    store = await make_store((CH, other))
    store.teams[TEAM]["needs_initial_sync"] = True
    fake = tenant([msg("m1")], {"m1": []})
    fake.add(f"/teams/{GID}/channels/{other}/messages", {"error": {"code": "Forbidden"}}, status=403)
    status, ctx = await run_team(fake, store)
    assert status == "ok" and ctx.stats["channel_errors"] == 1
    assert store.teams[TEAM]["needs_initial_sync"] is True  # a channel failed: try again next run
    assert "backfill failed: GraphAuthError 403" in store.channels[other]["sync_error"]
    assert "m1" in {k[1] for k in store.messages}
    fake.add(f"/teams/{GID}/channels/{other}/messages", {"value": [msg("o1")]})
    fake.add(f"/teams/{GID}/channels/{other}/messages/o1/replies", {"value": []})
    count_before = first_pages(fake)
    status, ctx = await run_team(fake, store)
    assert store.teams[TEAM]["needs_initial_sync"] is False and store.teams[TEAM]["last_backfill_at"] is not None
    assert first_pages(fake) == count_before  # the channel finished earlier is not fetched again
    assert not [k for k in store.sync_state if k.startswith("backfill:")]  # states cleaned up for a future reinstall


async def test_partial_team_keeps_the_flag():
    store = await make_store()
    store.teams[TEAM]["needs_initial_sync"] = True
    status, _ = await run_team(tenant([msg("m1")], {"m1": []}), store, budget=CountBudget(1))
    assert status == "partial" and store.teams[TEAM]["needs_initial_sync"] is True


# ------------------------------------------------------------------ storage contract (memory + postgres)
async def seed_conv(s):
    await s.upsert_team({"id": TEAM, "aad_group_id": GID})


def mrow(mid, **kw):
    base = {"conversation_id": CH, "message_id": mid, "reply_to_id": None, "from_aad_id": "u1", "from_name": "P",
            "text": "t", "html": "<p>t</p>", "importance": "normal", "mentions": None, "subject": None,
            "created_at": NOW - timedelta(days=1), "last_edited_at": None, "deleted_at": None}
    return {**base, **kw}


def att(mid, url="https://x/y.pdf"):
    return {"conversation_id": CH, "message_id": mid, "content_type": "reference", "name": "y.pdf", "content_url": url,
            "payload": {"id": "a1", "contentUrl": url}}


async def test_storage_backfill_message_contract(store):
    await seed_conv(store)
    rx = {"conversation_id": CH, "message_id": "m1", "user_aad_id": "u2", "user_name": "S", "reaction_type": "like",
          "action": "added", "at": NOW}
    assert await store.backfill_message(mrow("m1", subject="S"), [att("m1")], [rx]) == "created"
    got = await store.get_message(CH, "m1")
    assert got["message"]["source"] == "graph" and got["message"]["subject"] == "S" and got["message"]["backfilled_at"]
    assert len(got["versions"]) == 1 and len(got["attachments"]) == 1 and len(got["reactions"]) == 1
    # same data again: nothing changes
    assert await store.backfill_message(mrow("m1", subject="S"), [att("m1")], [rx]) == "unchanged"
    got = await store.get_message(CH, "m1")
    assert len(got["attachments"]) == 1 and len(got["reactions"]) == 1 and len(got["versions"]) == 1
    # an existing row is filled, never overwritten
    assert await store.backfill_message(mrow("m1", text="OTHER", subject="OTHER", importance="high"), [], []) == "unchanged"
    assert (await store.get_message(CH, "m1"))["message"]["text"] == "t"
    assert await store.backfill_message(mrow("m1", deleted_at=NOW), [], []) == "filled"
    assert (await store.get_message(CH, "m1"))["message"]["deleted_at"] is not None


async def test_storage_backfill_adds_attachments_only_when_none_exist(store):
    await seed_conv(store)
    assert await store.backfill_message(mrow("m2"), [], []) == "created"
    assert await store.backfill_message(mrow("m2"), [att("m2")], []) == "filled"
    assert await store.backfill_message(mrow("m2"), [att("m2", "https://x/other.pdf")], []) == "unchanged"
    assert len((await store.get_message(CH, "m2"))["attachments"]) == 1
