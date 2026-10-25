from datetime import date, datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 10, 20, 5, 30, tzinfo=timezone.utc)
CONV = "19:chan@thread.tacv2"
PV = "p3-test"


async def seed(s, n=3, **over):
    await s.upsert_team({"id": "T1", "aad_group_id": "g1", "name": "MA 221", "tenant_id": "x"})
    await s.mark_team_installed("T1", "ABC123", "prof-aad", T0)
    await s.upsert_conversation({"id": CONV, "conversation_type": "channel", "team_id": "T1", "channel_id": CONV, "name": "General"})
    for i in range(n):
        await s.backfill_message({"conversation_id": CONV, "message_id": f"m{i}", "reply_to_id": None,
                                  "from_aad_id": "prof-aad", "from_name": "Prof", "text": f"hello {i}", "html": None,
                                  "importance": None, "mentions": None, "subject": over.get("subject"),
                                  "created_at": T0 + timedelta(hours=n - i),  # m0 is the newest
                                  "last_edited_at": None, "deleted_at": None}, [], [])


def analysis(mid, status="ok", version=1, **kw):
    return {"conversation_id": CONV, "message_id": mid, "message_version": version, "prompt_version": PV,
            "status": status, **kw}


def ev(**kw):
    return {"team_id": "T1", "type": "quiz", "title": "Quiz 1", "is_deadline": False, "starts_at": None, "ends_at": None,
            "event_date": date(2026, 10, 23), "time_known": False, "time_defaulted": False, "venue": None,
            "details": None, "status": "scheduled", "confidence": "high", "needs_review": False, **kw}


SRC = lambda mid: {"kind": "message", "ref": {"conversation_id": CONV, "message_id": mid}, "at": T0}  # noqa: E731


async def test_pending_is_oldest_first_and_excludes_done_items(store):
    await seed(store)
    rows = await store.list_pending_messages(PV, 3, 10)
    assert [r["message_id"] for r in rows] == ["m2", "m1", "m0"]
    assert rows[0]["team_id"] == "T1" and rows[0]["installed_by_aad"] == "prof-aad"
    await store.commit_message_analysis(analysis("m2", category="chitchat"), [], SRC("m2"))
    assert [r["message_id"] for r in await store.list_pending_messages(PV, 3, 10)] == ["m1", "m0"]
    assert len(await store.list_pending_messages(PV, 3, 1)) == 1
    assert len(await store.list_pending_messages("other-version", 3, 10)) == 3  # a new prompt version re-opens everything


async def test_failed_items_are_retried_until_max_attempts(store):
    await seed(store, n=1)
    for expected_pending in (True, True, False):
        await store.commit_message_analysis(analysis("m0", status="failed", error_class="AllProvidersFailed"), [], SRC("m0"))
        assert bool(await store.list_pending_messages(PV, 3, 10)) is expected_pending
    (a,) = await store.list_message_analyses(CONV, "m0")
    assert a["attempts"] == 3 and a["error_class"] == "AllProvidersFailed"


async def test_private_channel_deleted_and_empty_messages_are_not_pending(store):
    await seed(store, n=1)
    await store.upsert_channel({"id": CONV, "team_id": "T1", "membership_type": "private"})
    assert await store.list_pending_messages(PV, 3, 10) == []
    await store.upsert_channel({"id": CONV, "team_id": "T1", "membership_type": "standard"})
    assert len(await store.list_pending_messages(PV, 3, 10)) == 1


async def test_commit_creates_and_updates_events_with_history(store):
    await seed(store)
    res = await store.commit_message_analysis(
        analysis("m2", category="event"),
        [{"kind": "create", "event": ev(), "relation": "created",
          "changes": [{"change_type": "created", "old_values": None, "new_values": {"date": "2026-10-23"}, "confidence": "high"}]}],
        SRC("m2"))
    eid = res[0]["event_id"]
    res2 = await store.commit_message_analysis(
        analysis("m1", category="change"),
        [{"kind": "update", "event_id": eid, "fields": {"status": "rescheduled", "event_date": date(2026, 10, 27)},
          "relation": "changed",
          "changes": [{"change_type": "rescheduled", "old_values": {"date": "2026-10-23"},
                       "new_values": {"date": "2026-10-27"}, "confidence": "high"}]}],
        {**SRC("m1"), "at": T0 + timedelta(days=1)})
    assert res2 == [{"event_id": eid, "relation": "changed"}]
    got = await store.get_event(eid)
    assert got["event"]["status"] == "rescheduled" and got["event"]["event_date"] == date(2026, 10, 27)
    assert [c["change_type"] for c in got["changes"]] == ["created", "rescheduled"]
    assert got["changes"][1]["old_values"] == {"date": "2026-10-23"}
    assert {(m["message_id"], m["relation"]) for m in got["messages"]} == {("m2", "created"), ("m1", "changed")}
    assert [e["id"] for e in await store.list_open_events("T1")] == [eid]
    assert [e["id"] for e in await store.list_events(team_id="T1", status="rescheduled", type="quiz")] == [eid]
    assert await store.list_events(team_id="T1", needs_review=True) == []


async def test_relation_created_is_never_downgraded_by_a_later_version(store):
    await seed(store, n=1)
    r = await store.commit_message_analysis(analysis("m0"), [{"kind": "create", "event": ev(), "relation": "created", "changes": []}], SRC("m0"))
    await store.commit_message_analysis(analysis("m0", version=2), [{"kind": "update", "event_id": r[0]["event_id"], "fields": {},
                                                                    "relation": "restated", "changes": []}], SRC("m0"))
    (link,) = await store.list_message_links(CONV, "m0")
    assert link["relation"] == "created"


async def test_commit_is_atomic(store):
    await seed(store, n=1)
    bad = [{"kind": "create", "event": ev(), "relation": "created", "changes": []},
           {"kind": "update", "event_id": 99999, "fields": {"status": "done"}, "relation": "changed",
            "changes": [{"change_type": "cancelled", "confidence": "high"}]}]
    with pytest.raises(Exception):
        await store.commit_message_analysis(analysis("m0"), bad, SRC("m0"))
    if type(store).__name__ == "PostgresStorage":  # memory has no transactions; the contract is for the real database
        assert await store.list_message_analyses(CONV, "m0") == []
        assert await store.list_events(team_id="T1") == []


async def test_ai_status_counts(store):
    await seed(store)
    await store.commit_message_analysis(analysis("m0", status="failed", error_class="Timeout"), [], SRC("m0"))
    await store.commit_message_analysis(analysis("m1", category="chitchat"), [], SRC("m1"))
    st = await store.ai_status(PV, 3)
    assert st["analyses_by_status"] == {"failed": 1, "ok": 1}
    assert st["failures_by_error_class"] == {"Timeout": 1} and st["messages_pending"] == 2
