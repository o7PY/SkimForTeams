"""Phase 4 storage contract (student-app tables). Runs on the memory store and, with TEST_DATABASE_URL, on Postgres."""
from datetime import datetime, timedelta, timezone

import pytest

from test_storage_phase3 import CONV, PV, SRC, T0, analysis, ev, seed

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
R1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
R2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


async def users(s):
    await s.upsert_app_user(U1, "a@x.com", "Asha", NOW)
    await s.upsert_app_user(U2, "b@x.com", "Bo", NOW)


async def msg_item(s, mid="m0"):
    return (await s.upsert_feed_item({"kind": "message", "team_id": "T1", "conversation_id": CONV, "message_id": mid,
                                      "occurred_at": T0}, []))[0]


async def event_and_change(s, mid="m0"):
    """One event with a created change; returns (event_id, change_id)."""
    op = {"kind": "create", "event": ev(), "relation": "created",
          "changes": [{"change_type": "created", "old_values": None, "new_values": {"title": "Quiz 1"}, "confidence": "high"}]}
    eid = (await s.commit_message_analysis(analysis(mid), [op], SRC(mid)))[0]["event_id"]
    return eid, (await s.get_event(eid))["changes"][0]["id"]


def test_memory_and_postgres_expose_the_same_phase4_surface():
    from skim.storage.app_memory import MemoryAppMixin
    from skim.storage.app_postgres import PostgresAppMixin
    names = lambda c: {n for n in dir(c) if not n.startswith("_")}  # noqa: E731
    assert names(MemoryAppMixin) == names(PostgresAppMixin)
    from skim.storage.memory import MemoryStorage
    from skim.storage.postgres import PostgresStorage
    assert names(PostgresAppMixin) <= names(MemoryStorage) and names(PostgresAppMixin) <= names(PostgresStorage)


async def test_user_upsert_keeps_existing_values_and_delete_cascades_everything(store):
    await seed(store)
    await users(store)
    u = await store.upsert_app_user(U1, None, None, NOW + timedelta(hours=1))  # a token without email/name changes nothing else
    assert (u["email"], u["name"], u["id"]) == ("a@x.com", "Asha", U1) and u["last_seen_at"] == NOW + timedelta(hours=1)
    assert u["created_at"] == NOW
    await store.set_user_settings(U1, {"history_limit": 80})
    assert (await store.get_app_user(U1))["settings"] == {"history_limit": 80}
    item = await msg_item(store)
    await store.add_subscription(U1, "T1", NOW)
    await store.set_item_state(U1, [item], "dismissed", NOW)
    await store.put_star(U1, item, None, False, NOW)
    await store.upsert_reminder({"id": R1, "user_id": U1, "item_id": item, "kind": "manual", "fire_at": NOW}, NOW)
    await store.upsert_device(U1, "ExponentPushToken[a]", "android", NOW)
    await store.record_join_attempt(U1, NOW, True)
    await store.add_subscription(U2, "T1", NOW)
    assert await store.delete_app_user(U1) is True
    assert await store.delete_app_user(U1) is False
    assert await store.get_app_user(U1) is None
    assert await store.list_subscriptions(U1, include_left=True) == [] and await store.list_stars(U1) == []
    assert await store.list_reminders(U1, include_deleted=True) == [] and await store.list_devices(U1, active_only=False) == []
    assert await store.get_item_states(U1, [item]) == {} and await store.count_join_attempts(U1, NOW - timedelta(days=1)) == 0
    assert [s["user_id"] for s in await store.list_subscriptions(U2)] == [U2]  # another user is untouched
    assert (await store.get_feed_item(item)) is not None                       # shared items are not user data


async def test_team_code_lookup_ignores_case_and_spaces(store):
    await seed(store)
    for code in ("ABC123", "abc123", " a b c 1 2 3 "):
        assert (await store.get_team_by_code(code))["id"] == "T1"
    assert await store.get_team_by_code("ZZZZZZ") is None


async def test_join_leave_rejoin_and_mute(store):
    await seed(store)
    await users(store)
    assert await store.add_subscription(U1, "T1", NOW) is True
    assert await store.add_subscription(U1, "T1", NOW + timedelta(minutes=1)) is False  # already a member
    assert await store.set_subscription_muted(U1, "T1", True) is True
    [s] = await store.list_subscriptions(U1)
    assert (s["name"], s["muted"], s["removed_at"], s["joined_at"]) == ("MA 221", True, None, NOW)
    assert await store.set_subscription_muted(U1, "NOPE", True) is False
    assert await store.leave_subscription(U1, "T1", NOW + timedelta(hours=1)) is True
    assert await store.leave_subscription(U1, "T1", NOW + timedelta(hours=1)) is False
    assert await store.list_subscriptions(U1) == []
    assert len(await store.list_subscriptions(U1, include_left=True)) == 1
    assert await store.set_subscription_muted(U1, "T1", False) is False                 # not a member any more
    assert await store.add_subscription(U1, "T1", NOW + timedelta(days=1)) is True      # re-join starts fresh
    [s] = await store.list_subscriptions(U1)
    assert (s["muted"], s["left_at"], s["joined_at"]) == (False, None, NOW + timedelta(days=1))


async def test_join_attempts_are_counted_inside_the_window(store):
    await users(store)
    await store.record_join_attempt(U1, NOW - timedelta(hours=2), False)
    await store.record_join_attempt(U1, NOW - timedelta(minutes=30), False)
    await store.record_join_attempt(U1, NOW - timedelta(minutes=1), True)
    await store.record_join_attempt(U2, NOW, False)
    assert await store.count_join_attempts(U1, NOW - timedelta(hours=1)) == 2
    assert await store.count_join_attempts(U1, NOW - timedelta(hours=3)) == 3


async def test_leaving_deletes_that_teams_state_stars_and_reminders_only(store):
    await seed(store)
    await store.upsert_team({"id": "T2", "aad_group_id": "g2", "name": "Other", "tenant_id": "x"})
    await users(store)
    a = await msg_item(store)
    b = (await store.upsert_feed_item({"kind": "message", "team_id": "T2", "conversation_id": "19:o@thread.tacv2",
                                       "message_id": "o1", "occurred_at": T0}, []))[0]
    for t in ("T1", "T2"):
        await store.add_subscription(U1, t, NOW)
    for i in (a, b):
        await store.set_item_state(U1, [i], "opened", NOW)
        await store.put_star(U1, i, None, False, NOW)
    await store.upsert_reminder({"id": R1, "user_id": U1, "item_id": a, "kind": "manual", "fire_at": NOW}, NOW)
    await store.upsert_reminder({"id": R2, "user_id": U1, "item_id": b, "kind": "manual", "fire_at": NOW}, NOW)
    await store.leave_subscription(U1, "T1", NOW)
    assert set(await store.get_item_states(U1, [a, b])) == {b}
    assert [s["item_id"] for s in await store.list_stars(U1)] == [b]
    assert [r["id"] for r in await store.list_reminders(U1)] == [R2]


async def test_feed_items_are_idempotent_per_source_and_events_only_accumulate(store):
    await seed(store)
    eid, cid = await event_and_change(store)
    i, created = await store.upsert_feed_item({"kind": "message", "team_id": "T1", "conversation_id": CONV, "message_id": "m1",
                                               "occurred_at": T0}, [eid])
    assert created is True
    j, created = await store.upsert_feed_item({"kind": "message", "team_id": "T1", "conversation_id": CONV, "message_id": "m1",
                                               "occurred_at": T0 + timedelta(days=9)}, [eid])
    assert (j, created) == (i, False)
    it = await store.get_feed_item(i)
    assert it["event_ids"] == [eid] and it["occurred_at"] == T0 and it["hidden"] is False   # occurred_at never moves
    c1, _ = await store.upsert_feed_item({"kind": "change", "team_id": "T1", "event_change_id": cid, "occurred_at": T0}, [eid])
    c2, created = await store.upsert_feed_item({"kind": "change", "team_id": "T1", "event_change_id": cid, "occurred_at": T0}, [])
    assert (c1, created) == (c2, False) and c1 != i
    await store.set_feed_item_hidden(i, True)
    assert (await store.get_feed_item(i))["hidden"] is True
    await store.upsert_feed_item({"kind": "message", "team_id": "T1", "conversation_id": CONV, "message_id": "m1",
                                  "occurred_at": T0}, [])
    assert (await store.get_feed_item(i))["hidden"] is True                                  # an upsert never un-hides
    assert await store.get_feed_item(999999) is None


async def test_item_state_snooze_clear_and_fold(store):
    await seed(store)
    await users(store)
    a, b = await msg_item(store, "m0"), await msg_item(store, "m1")
    await store.set_item_state(U1, [a, b], "dismissed", NOW, folded_into=None)
    await store.set_item_state(U1, [a], "opened", NOW + timedelta(minutes=1))               # overwrite
    st = await store.get_item_states(U1, [a, b])
    assert st[a]["state"] == "opened" and st[b]["state"] == "dismissed" and st[a]["acted_at"] == NOW + timedelta(minutes=1)
    until = NOW + timedelta(hours=1)
    await store.set_item_state(U1, [b], None, NOW, snoozed_until=until, folded_into=a)
    st = (await store.get_item_states(U1, [b]))[b]
    assert (st["state"], st["snoozed_until"], st["folded_into"]) == (None, until, a)
    with pytest.raises(Exception):
        await store.set_item_state(U1, [b], None, NOW)                                      # no state and no snooze
    assert await store.get_item_states(U2, [a, b]) == {}                                     # per user
    await store.clear_item_state(U1, [a, b])
    assert await store.get_item_states(U1, [a, b]) == {}


async def test_stars_upsert_keep_created_at_and_list_newest_first(store):
    await seed(store)
    await users(store)
    a, b = await msg_item(store, "m0"), await msg_item(store, "m1")
    await store.put_star(U1, a, None, False, NOW)
    await store.put_star(U1, b, NOW + timedelta(days=1), False, NOW + timedelta(minutes=5))
    await store.put_star(U1, a, NOW + timedelta(days=3), True, NOW + timedelta(minutes=9))  # re-star: replaces the expiry
    rows = await store.list_stars(U1)
    assert [r["item_id"] for r in rows] == [b, a]
    ra = rows[1]
    assert (ra["expires_at"], ra["until_event_over"], ra["created_at"]) == (NOW + timedelta(days=3), True, NOW)
    assert await store.delete_star(U1, a) is True and await store.delete_star(U1, a) is False
    assert await store.delete_star(U2, b) is False


async def test_reminders_upsert_tombstone_and_ownership(store):
    await seed(store)
    await users(store)
    eid, _ = await event_and_change(store)
    a = await msg_item(store)
    r = await store.upsert_reminder({"id": R1, "user_id": U1, "item_id": a, "event_id": eid, "kind": "manual",
                                     "offset_minutes": -60}, NOW)
    assert (r["offset_minutes"], r["fire_at"], r["deleted_at"]) == (-60, None, None)
    r = await store.upsert_reminder({"id": R1, "user_id": U1, "item_id": a, "event_id": eid, "kind": "manual",
                                     "offset_minutes": -120}, NOW + timedelta(minutes=1))
    assert r["offset_minutes"] == -120 and r["created_at"] == NOW and r["updated_at"] == NOW + timedelta(minutes=1)
    other = await store.upsert_reminder({"id": R1, "user_id": U2, "item_id": a, "kind": "manual", "fire_at": NOW}, NOW)
    assert other is None                                                                     # U2 cannot take U1's row
    assert (await store.get_reminder(U1, R1))["offset_minutes"] == -120
    assert await store.get_reminder(U2, R1) is None
    await store.tombstone_reminder(U2, {"id": R1, "kind": "manual"}, NOW)                    # not the owner: ignored
    assert [x["id"] for x in await store.list_reminders(U1)] == [R1]
    await store.tombstone_reminder(U1, {"id": R1, "kind": "manual"}, NOW + timedelta(minutes=2))
    assert await store.list_reminders(U1) == []
    [t] = await store.list_reminders(U1, include_deleted=True)
    assert t["deleted_at"] == NOW + timedelta(minutes=2)
    r = await store.upsert_reminder({"id": R1, "user_id": U1, "item_id": a, "kind": "manual", "fire_at": NOW}, NOW)
    assert r["deleted_at"] is None                                                           # re-creating clears the tombstone
    # a tombstone for a reminder that never existed (a virtual auto reminder the user deleted)
    await store.tombstone_reminder(U1, {"id": R2, "item_id": a, "event_id": eid, "kind": "auto_same_day"}, NOW)
    assert [x["id"] for x in await store.list_reminders(U1)] == [R1]
    assert {x["id"] for x in await store.list_reminders(U1, include_deleted=True)} == {R1, R2}


async def test_devices_register_move_disable_and_delete(store):
    await users(store)
    await store.upsert_device(U1, "tok1", "android", NOW)
    await store.upsert_device(U1, "tok1", "android", NOW + timedelta(hours=1))               # idempotent
    assert [d["expo_push_token"] for d in await store.list_devices(U1)] == ["tok1"]
    await store.disable_device("tok1", NOW + timedelta(hours=2))
    assert await store.list_devices(U1) == []
    assert [d["disabled_at"] is not None for d in await store.list_devices(U1, active_only=False)] == [True]
    await store.upsert_device(U1, "tok1", "android", NOW + timedelta(hours=3))               # re-register re-enables
    assert len(await store.list_devices(U1)) == 1
    await store.upsert_device(U2, "tok1", "android", NOW + timedelta(hours=4))               # the phone changed account
    assert await store.list_devices(U1) == [] and len(await store.list_devices(U2)) == 1
    await store.disable_device("tok1", NOW + timedelta(hours=5))
    await store.upsert_device(U1, "tok1", "android", NOW + timedelta(hours=6))               # a different user cannot re-enable it
    assert await store.list_devices(U1) == [] and await store.list_devices(U2) == []
    await store.upsert_device(U1, "tok1", "android", NOW + timedelta(hours=7))               # only the current owner (U1) can
    assert len(await store.list_devices(U1)) == 1 and await store.list_devices(U2) == []
    assert await store.delete_device(U2, "tok1") is False
    assert await store.delete_device(U1, "tok1") is True
    assert await store.list_devices(U1, active_only=False) == []


async def test_push_outbox_never_alerts_twice_and_follows_due_time_and_receipts(store):
    await seed(store)
    await users(store)
    _, cid = await event_and_change(store)
    row = {"user_id": U1, "event_change_id": cid, "title": "Quiz 1 moved", "body": "Mon 2 Nov", "data": {"type": "sync"},
           "not_before": NOW + timedelta(hours=1)}
    assert await store.add_push(row) is True
    assert await store.add_push({**row, "title": "again"}) is False
    assert await store.add_push({**row, "user_id": U2}) is True                              # another user is a separate alert
    assert await store.list_due_push(NOW, 10) == []
    due = await store.list_due_push(NOW + timedelta(hours=2), 10)
    assert [(p["user_id"], p["title"], p["data"]) for p in due] == [(U1, "Quiz 1 moved", {"type": "sync"}), (U2, "Quiz 1 moved", {"type": "sync"})]
    assert len(await store.list_due_push(NOW + timedelta(hours=2), 1)) == 1
    pid = due[0]["id"]
    sent = NOW + timedelta(hours=2)
    await store.update_push(pid, status="sent", ticket_id="t1", sent_at=sent)
    assert [p["id"] for p in await store.list_due_push(NOW + timedelta(hours=2), 10)] == [due[1]["id"]]
    assert await store.list_push_awaiting_receipt(sent - timedelta(minutes=1), 10) == []   # not old enough yet
    assert [p["id"] for p in await store.list_push_awaiting_receipt(sent + timedelta(minutes=16), 10)] == [pid]
    await store.update_push(pid, receipt_status="ok")
    assert await store.list_push_awaiting_receipt(sent + timedelta(minutes=16), 10) == []
    with pytest.raises(ValueError):
        await store.update_push(pid, user_id=U2)
    assert [p["status"] for p in await store.list_push(U1)] == ["sent"]


async def test_reset_ai_data_also_clears_phase4_rows_that_point_at_the_resets_events(store):
    await seed(store)
    await users(store)
    eid, cid = await event_and_change(store)
    m = (await store.upsert_feed_item({"kind": "message", "team_id": "T1", "conversation_id": CONV, "message_id": "m0",
                                       "occurred_at": T0}, [eid]))[0]
    c = (await store.upsert_feed_item({"kind": "change", "team_id": "T1", "event_change_id": cid, "occurred_at": T0}, [eid]))[0]
    await store.set_item_state(U1, [c], "dismissed", NOW)
    await store.put_star(U1, c, None, False, NOW)
    await store.upsert_reminder({"id": R1, "user_id": U1, "item_id": m, "event_id": eid, "kind": "manual", "offset_minutes": -60}, NOW)
    await store.upsert_reminder({"id": R2, "user_id": U1, "item_id": m, "kind": "manual", "fire_at": NOW}, NOW)  # absolute: kept
    await store.add_push({"user_id": U1, "event_change_id": cid, "title": "t", "body": "b", "not_before": NOW})
    await store.reset_ai_data("T1", dry_run=False)                                           # must not hit a foreign key
    assert await store.get_feed_item(c) is None and await store.get_item_states(U1, [c]) == {}
    assert await store.list_stars(U1) == [] and await store.list_push(U1) == []
    assert (await store.get_feed_item(m))["event_ids"] == []                                 # the message card stays, unlinked
    assert [r["id"] for r in await store.list_reminders(U1)] == [R2]
