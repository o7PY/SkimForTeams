"""T4: feed-build (P4-D-108/109/128). Pure rules, then the job on both storages (memory, and Postgres with TEST_DATABASE_URL)."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

from skim.feed import (CHANGE_CARD_TYPES, author_role, document_qualifies, message_qualifies, modified_is_coalesced)
from skim.jobs.feed_build import build_feed
from skim.normalize import MessageEvent

T0 = datetime(2026, 10, 20, 5, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
CONV = "19:chan@thread.tacv2"
PRIV = "19:private@thread.tacv2"
PROF, STUDENT, OTHER = "prof-aad", "stud-aad", "other-aad"
STAFF = frozenset({PROF})


# ---------------------------------------------------------------- pure rules
def test_author_role_matches_phase3():
    assert author_role(PROF, None, STAFF) == "staff"
    assert author_role("ta-aad", None, frozenset({"ta-aad"})) == "staff"
    assert author_role(PROF, None, frozenset()) == "unknown"            # no allow-list and not the installer
    assert author_role(PROF, PROF, frozenset()) == "staff"              # the installer is always staff
    assert author_role(STUDENT, PROF, STAFF) == "student"
    assert author_role(None, PROF, STAFF) == "student"                  # allow-list set and the author is unknown
    assert author_role(None, PROF, frozenset()) == "unknown"


@pytest.mark.parametrize("who,category,staff,expect", [
    (PROF, None, STAFF, True),            # staff posts always become a card, even unanalysed
    (PROF, "chitchat", STAFF, True),      # ... and even when the AI called it chitchat or a question
    (PROF, "question", STAFF, True),
    (STUDENT, "event", STAFF, True), (STUDENT, "change", STAFF, True),
    (STUDENT, "announcement", STAFF, True), (STUDENT, "resource", STAFF, True),
    (STUDENT, "chitchat", STAFF, False), (STUDENT, "question", STAFF, False), (STUDENT, None, STAFF, False),
    (None, "announcement", STAFF, False), ("", None, STAFF, False),   # bot / system messages have no author id
    (OTHER, None, frozenset(), False),                                # no allow-list: unknown author, no category
    (OTHER, "event", frozenset(), True),
])
def test_message_rule(who, category, staff, expect):
    assert message_qualifies({"from_aad_id": who, "category": category, "installed_by_aad": None}, staff) is expect


@pytest.mark.parametrize("event", ["created", "renamed", "moved", "deleted", "restored", "modified"])
def test_document_rule_by_author(event):
    base = {"event": event, "created_by_aad": PROF, "installed_by_aad": None}
    assert document_qualifies(base, STAFF) is True
    assert document_qualifies({**base, "created_by_aad": STUDENT}, STAFF) is False
    assert document_qualifies({**base, "created_by_aad": None}, STAFF) is False         # allow-list set: unknown author = student
    assert document_qualifies({**base, "created_by_aad": None}, frozenset()) is True    # no allow-list: unknown author is shown
    # the LAST MODIFIER decides once somebody edited it (P3-D-019): a student co-editing a staff file hides it
    assert document_qualifies({**base, "modified_by_aad": STUDENT}, STAFF) is False
    assert document_qualifies({**base, "created_by_aad": STUDENT, "modified_by_aad": PROF}, STAFF) is True
    assert document_qualifies({**base, "modified_by_name": "Someone"}, STAFF) is False  # edited by an unidentified person
    assert document_qualifies({**base, "event": "viewed"}, STAFF) is False


def test_coalescing_window_edges():
    t = T0
    assert modified_is_coalesced(t + timedelta(hours=5, minutes=59), [t]) is True
    assert modified_is_coalesced(t + timedelta(hours=6), [t]) is False
    assert modified_is_coalesced(t, [t]) is True
    assert modified_is_coalesced(t - timedelta(hours=1), [t]) is False   # a card that comes later does not suppress it
    assert modified_is_coalesced(t, []) is False


def test_change_types_are_the_seven_from_the_spec():
    assert set(CHANGE_CARD_TYPES) == {"rescheduled", "cancelled", "uncancelled", "venue_changed", "time_set", "date_set",
                                      "details_changed"}


# ---------------------------------------------------------------- the job
async def team(s, installer=PROF):
    await s.upsert_team({"id": "T1", "aad_group_id": "g1", "name": "MA 221", "tenant_id": "x"})
    await s.mark_team_installed("T1", "ABC123", installer, T0)
    await s.upsert_conversation({"id": CONV, "conversation_type": "channel", "team_id": "T1", "channel_id": CONV, "name": "General"})
    await s.upsert_channel({"id": CONV, "team_id": "T1", "display_name": "General", "membership_type": "standard"})


async def msg(s, mid, who=PROF, text="hello", at=None, reply_to=None, conv=CONV, atts=(), version_text=None):
    row = {"conversation_id": conv, "message_id": mid, "reply_to_id": reply_to, "from_aad_id": who, "from_name": "N",
           "text": text, "html": None, "importance": None, "mentions": None, "subject": None,
           "created_at": at or (NOW - timedelta(days=1)), "last_edited_at": None, "deleted_at": None}
    await s.backfill_message(row, [{"conversation_id": conv, "message_id": mid, "content_type": "reference", "name": n,
                                    "content_url": u, "payload": {"n": n}} for n, u in atts], [])


async def analyse(s, mid, category, version=1, conv=CONV):
    await s.commit_message_analysis({"conversation_id": conv, "message_id": mid, "message_version": version,
                                     "prompt_version": "p3-test", "status": "ok", "category": category}, [],
                                    {"kind": "message", "team_id": "T1", "ref": {"conversation_id": conv, "message_id": mid}, "at": T0})


def quiz_op(**kw):
    return {"kind": "create", "relation": "created", "event": {
        "team_id": "T1", "type": "quiz", "title": "Quiz 1", "is_deadline": False, "starts_at": None, "ends_at": None,
        "event_date": date(2026, 11, 2), "time_known": False, "time_defaulted": False, "venue": None, "details": None,
        "status": "scheduled", "confidence": "high", "needs_review": False},
        "changes": [{"change_type": "created", "old_values": None, "new_values": {}, "confidence": "high"}], **kw}


async def raw(s, key):
    """A real raw_activities row (the message versions reference one)."""
    rid, _ = await s.insert_raw({"dedupe_key": key, "activity_type": "messageDelete", "activity_id": key, "event_type": None,
                                 "conversation_id": CONV, "tenant_id": "x", "payload": {}})
    return rid


async def run(s, staff=STAFF, lookback=30, team_ids=None, stats=None):
    stats = Counter() if stats is None else stats
    status = await build_feed(s, staff, NOW, lookback, stats, team_ids)
    return status, stats


async def cards(s, kind=None, hidden=None):
    out = [f for f in await _all_items(s) if (kind is None or f["kind"] == kind) and (hidden is None or f["hidden"] == hidden)]
    return out


async def _all_items(s):
    """Every feed item, newest id last, through the public storage API only."""
    out, i, misses = [], 1, 0
    while misses < 3:
        it = await s.get_feed_item(i)
        misses = 0 if it else misses + 1
        if it:
            out.append(it)
        i += 1
    return out


def mids(items):
    return sorted(i["message_id"] for i in items if i["kind"] == "message")


async def test_message_rules_end_to_end(store):
    await team(store)
    await msg(store, "staff")                                         # staff, unanalysed -> card
    await msg(store, "chat", who=STUDENT)
    await analyse(store, "chat", "chitchat")                          # student chitchat -> no card
    await msg(store, "ann", who=STUDENT)
    await analyse(store, "ann", "announcement")                       # student announcement -> card
    await msg(store, "raw", who=STUDENT)                              # student, not analysed yet -> no card
    await msg(store, "bot", who=None)                                 # no author id -> no card
    await msg(store, "empty", text="")                                # nothing to show -> no card
    await msg(store, "fileonly", text="", atts=[("syllabus.pdf", "https://x/s.pdf")])  # staff file post -> card
    await msg(store, "reply", reply_to="staff", text="see above")     # replies are separate cards
    status, stats = await run(store)
    assert status == "ok" and mids(await cards(store)) == ["ann", "fileonly", "reply", "staff"]
    assert stats["feed_message_cards"] == 4 and stats["feed_messages_not_cards"] == 3   # chat, raw, bot ("empty" never reaches the rules)
    it = [i for i in await cards(store) if i["message_id"] == "staff"][0]
    assert it["team_id"] == "T1" and it["occurred_at"] == NOW - timedelta(days=1) and it["hidden"] is False


async def test_a_late_analysis_gives_the_card_on_a_later_pass_and_old_cards_stay(store):
    await team(store)
    await msg(store, "s1", who=STUDENT)
    await analyse(store, "s1", "chitchat")
    await run(store)
    assert mids(await cards(store)) == []
    await analyse(store, "s1", "announcement")                        # (same version, later analysis row)
    await run(store)
    assert mids(await cards(store)) == ["s1"]
    await analyse(store, "s1", "chitchat")                            # re-classified later: the card stays
    await run(store)
    assert mids(await cards(store)) == ["s1"]


async def test_build_is_idempotent_and_ids_are_stable(store):
    await team(store)
    await msg(store, "a")
    await msg(store, "b")
    await run(store)
    first = await cards(store)
    status, stats = await run(store)
    assert status == "ok" and not stats.get("feed_message_cards")
    assert [i["id"] for i in await cards(store)] == [i["id"] for i in first]


async def test_cards_link_to_the_events_their_message_touches(store):
    await team(store)
    await msg(store, "m1")
    res = await store.commit_message_analysis(
        {"conversation_id": CONV, "message_id": "m1", "message_version": 1, "prompt_version": "p3-test", "status": "ok",
         "category": "event"}, [quiz_op()],
        {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": "m1"}, "at": NOW})
    eid = res[0]["event_id"]
    await run(store)
    [it] = await cards(store, "message")
    assert it["event_ids"] == [eid]


async def test_links_are_added_when_the_analysis_finishes_after_the_card_exists(store):
    await team(store)
    await msg(store, "m1")
    await run(store)                                                   # the card exists, no event yet
    [it] = await cards(store, "message")
    assert it["event_ids"] == []
    res = await store.commit_message_analysis(
        {"conversation_id": CONV, "message_id": "m1", "message_version": 1, "prompt_version": "p3-test", "status": "ok",
         "category": "event"}, [quiz_op()],
        {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": "m1"}, "at": NOW})
    _, stats = await run(store)
    assert (await store.get_feed_item(it["id"]))["event_ids"] == [res[0]["event_id"]] and stats["feed_event_links_added"] == 1


async def test_a_reset_then_reanalysis_relinks_the_surviving_cards(store):
    await team(store)
    await msg(store, "m1")
    op_src = {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": "m1"}, "at": NOW}
    a = {"conversation_id": CONV, "message_id": "m1", "message_version": 1, "prompt_version": "p3-test", "status": "ok", "category": "event"}
    await store.commit_message_analysis(a, [quiz_op()], op_src)
    await run(store)
    [it] = await cards(store, "message")
    await store.reset_ai_data("T1", dry_run=False)                     # events gone, the message card survives unlinked
    assert (await store.get_feed_item(it["id"]))["event_ids"] == []
    res = await store.commit_message_analysis(a, [quiz_op()], op_src)  # the rebuild creates a new event
    await run(store)
    assert (await store.get_feed_item(it["id"]))["event_ids"] == [res[0]["event_id"]]


async def test_deleted_messages_are_hidden_and_come_back_if_undeleted(store):
    await team(store)
    await msg(store, "m1")
    await run(store)
    [it] = await cards(store)
    ev = lambda kind: MessageEvent(conversation_id=CONV, message_id="m1", event=kind, at=NOW)  # noqa: E731
    await store.apply_message_event(ev("deleted"), await raw(store, "r1"))
    _, stats = await run(store)
    assert (await store.get_feed_item(it["id"]))["hidden"] is True and stats["feed_cards_synced_with_teams_deletes"] == 1
    await store.apply_message_event(ev("undeleted"), await raw(store, "r2"))
    await run(store)
    assert (await store.get_feed_item(it["id"]))["hidden"] is False


async def test_a_message_deleted_before_it_was_ever_a_card_never_becomes_one(store):
    await team(store)
    await msg(store, "m1")
    await store.apply_message_event(MessageEvent(conversation_id=CONV, message_id="m1", event="deleted", at=NOW), await raw(store, "r1"))
    await run(store)
    assert await cards(store) == []


async def test_private_channels_removed_teams_and_the_team_filter(store):
    await team(store)
    await store.upsert_conversation({"id": PRIV, "conversation_type": "channel", "team_id": "T1", "channel_id": PRIV, "name": "P"})
    await store.upsert_channel({"id": PRIV, "team_id": "T1", "display_name": "P", "membership_type": "private"})
    await store.upsert_team({"id": "T2", "aad_group_id": "g2", "name": "Other", "tenant_id": "x"})
    await store.mark_team_installed("T2", "ZZZ999", PROF, T0)
    await store.upsert_conversation({"id": "19:o@thread.tacv2", "conversation_type": "channel", "team_id": "T2", "channel_id": "19:o@thread.tacv2", "name": "G"})
    await msg(store, "pm", conv=PRIV)                                  # private channel: never
    await msg(store, "t2", conv="19:o@thread.tacv2")
    await msg(store, "t1")
    await run(store, team_ids={"T1"})
    assert mids(await cards(store)) == ["t1"]                          # the filter leaves T2 alone
    await run(store)
    assert mids(await cards(store)) == ["t1", "t2"]
    await store.mark_team_removed("T2", NOW)
    await msg(store, "t2b", conv="19:o@thread.tacv2")
    await run(store)
    assert mids(await cards(store)) == ["t1", "t2"]                    # an ended team gets no new cards (P4-D-126)


async def test_the_lookback_window_and_the_backfill_mode(store):
    await team(store)
    await msg(store, "old", at=NOW - timedelta(days=90))
    await msg(store, "new", at=NOW - timedelta(days=2))
    await run(store, lookback=30)
    assert mids(await cards(store)) == ["new"]
    await run(store, lookback=None)                                    # --backfill: no look-back
    assert mids(await cards(store)) == ["new", "old"]


# ---- files
DRIVE = "drive1"


async def doc(s, item="d1", by=PROF, name="notes.pdf", modified_by=None):
    return await s.upsert_document({"drive_id": DRIVE, "item_id": item, "team_id": "T1", "channel_id": CONV, "name": name,
                                    "parent_item_id": "root", "path": name, "created_by_aad": by, "created_by_name": "N",
                                    "modified_by_aad": modified_by, "web_url": f"https://x/{item}"})


async def docev(s, did, event, at):
    return await s.add_document_event({"document_id": did, "event": event, "at": at})


async def test_file_events_each_get_a_card_except_a_students(store):
    await team(store)
    d = await doc(store)
    s = await doc(store, "d2", by=STUDENT, name="mine.pdf")
    for i, ev in enumerate(["created", "renamed", "moved", "deleted", "restored"]):
        await docev(store, d, ev, NOW - timedelta(days=3) + timedelta(days=i * 0.1))
    await docev(store, s, "created", NOW - timedelta(days=1))
    status, stats = await run(store)
    assert len(await cards(store, "document")) == 5 and stats["feed_file_cards"] == 5 and stats["feed_files_not_cards"] == 1


async def test_modified_is_one_card_per_file_per_six_hours(store):
    await team(store)
    d = await doc(store)
    base = NOW - timedelta(days=2)
    for h in (0, 1, 5, 7, 8, 14):                                       # cards at 0, 7, 14
        await docev(store, d, "modified", base + timedelta(hours=h))
    _, stats = await run(store)
    assert len(await cards(store, "document")) == 3 and stats["feed_files_coalesced"] == 3
    await docev(store, d, "modified", base + timedelta(hours=16))       # within 6 h of the card at 14: coalesced across passes too
    await run(store)
    assert len(await cards(store, "document")) == 3
    await docev(store, d, "modified", base + timedelta(hours=21))       # 7 h after the card at 14: a new card
    await run(store)
    assert len(await cards(store, "document")) == 4
    other = await doc(store, "d2", name="b.pdf")                        # the window is per file
    await docev(store, other, "modified", base + timedelta(hours=1))
    await run(store)
    assert len(await cards(store, "document")) == 5


async def test_a_file_posted_with_a_card_message_has_no_card_of_its_own(store):
    await team(store)
    await msg(store, "post", text="slides attached", atts=[("slides.pdf", "https://x/slides.pdf")])
    d = await doc(store, "slides", name="slides.pdf")
    [att] = await store.list_unlinked_attachments("T1", 10)
    await store.add_attachment_link(att["id"], d, "web_url")
    await docev(store, d, "created", NOW - timedelta(days=1))
    await docev(store, d, "modified", NOW - timedelta(hours=3))        # a later edit is still its own card
    _, stats = await run(store)
    assert mids(await cards(store)) == ["post"] and stats["feed_files_on_message_cards"] == 1
    [c] = await cards(store, "document")
    assert c["document_event_id"] is not None and len(await cards(store, "document")) == 1   # only the modified one


async def test_a_file_card_made_before_the_link_existed_is_hidden_once_the_message_card_exists(store):
    await team(store)
    d = await doc(store, "slides", name="slides.pdf")
    await docev(store, d, "created", NOW - timedelta(days=1))
    await run(store)                                                    # no message/link yet: the file gets its own card
    [c] = await cards(store, "document")
    assert c["hidden"] is False
    await msg(store, "post", text="slides", atts=[("slides.pdf", "https://x/slides.pdf")])
    [att] = await store.list_unlinked_attachments("T1", 10)
    await store.add_attachment_link(att["id"], d, "web_url")
    _, stats = await run(store)
    assert (await store.get_feed_item(c["id"]))["hidden"] is True and stats["feed_duplicate_file_cards_folded"] == 1
    assert mids(await cards(store, hidden=False)) == ["post"]


async def test_a_file_posted_by_a_student_message_card_does_not_hide_a_staff_files_card(store):
    await team(store)
    d = await doc(store, "f", name="f.pdf")
    await docev(store, d, "created", NOW - timedelta(days=1))
    await msg(store, "chat", who=STUDENT, text="look", atts=[("f.pdf", "https://x/f.pdf")])   # student chatter is not a card
    [att] = await store.list_unlinked_attachments("T1", 10)
    await store.add_attachment_link(att["id"], d, "web_url")
    await analyse(store, "chat", "chitchat")
    await run(store)
    assert len(await cards(store, "document", hidden=False)) == 1


async def test_file_cards_link_to_events_extracted_from_the_file(store):
    await team(store)
    d = await doc(store, "syl", name="syllabus.pdf")
    await docev(store, d, "created", NOW - timedelta(days=1))
    res = await store.commit_message_analysis(
        None, [quiz_op()], {"kind": "document", "team_id": "T1", "ref": {"document_id": d, "chunk_index": 0}, "at": NOW})
    await run(store)
    [c] = await cards(store, "document")
    assert c["event_ids"] == [res[0]["event_id"]]


# ---- changes
async def test_only_the_seven_change_types_become_cards_and_they_link_their_event(store):
    await team(store)
    res = await store.commit_message_analysis(
        {"conversation_id": CONV, "message_id": "m1", "message_version": 1, "prompt_version": "p3-test", "status": "ok", "category": "event"},
        [quiz_op(changes=[{"change_type": t, "old_values": None, "new_values": {}, "confidence": "high"}
                          for t in ["created", "restated", "needs_review", *CHANGE_CARD_TYPES]])],
        {"kind": "message", "team_id": "T1", "ref": {"conversation_id": CONV, "message_id": "m1"}, "at": NOW - timedelta(hours=2)})
    eid = res[0]["event_id"]
    _, stats = await run(store)
    ch = await cards(store, "change")
    assert len(ch) == 7 and stats["feed_change_cards"] == 7 and all(c["event_ids"] == [eid] for c in ch)
    assert all(c["occurred_at"] == NOW - timedelta(hours=2) for c in ch)
    await run(store)
    assert len(await cards(store, "change")) == 7


async def test_the_job_step_runs_without_ai_or_graph(store):
    from skim.jobs.common import Budget, JobContext
    from skim.jobs.feed_build import feed_build
    await team(store)
    await msg(store, "m1")
    ctx = JobContext(store, None, Budget(60), options={"staff_aad_ids": STAFF, "feed_lookback_days": 30}, clock=lambda: NOW)
    assert await feed_build(ctx, None) == "ok"
    assert mids(await cards(store)) == ["m1"] and ctx.stats["feed_message_cards"] == 1


def test_run_all_includes_feed_build_after_the_ai_steps():
    from skim.jobs.runner import EXTRA_RUN_ALL_STEPS, STEPS, step_ai_all, step_feed_build
    assert STEPS["feed-build"] is step_feed_build
    assert EXTRA_RUN_ALL_STEPS.index(step_feed_build) == EXTRA_RUN_ALL_STEPS.index(step_ai_all) + 1
