"""T6: /api/v1/reminders on both storages (the engine itself is tested in test_reminders_engine.py)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from skim import reminders as R
from test_api_deck import A, B, api, card_for, make_event, reschedule  # noqa: F401  (api is a fixture)
from test_feed_build import CONV, NOW, msg
from test_reminders_engine import SECRET, at

NEW = lambda: str(uuid.uuid4())  # noqa: E731


async def set_event(api, eid, **fields):
    s = api.store
    if getattr(s, "pool", None):
        sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        await s.pool.execute(f"update events set {sets} where id=$1", eid, *fields.values())
    else:
        s.events[eid].update(fields)


async def quiz_card(api, mid="orig", day=date(2026, 11, 2), start=at(2026, 11, 2, 10), **kw):
    """A staff card about a 'Quiz 1' that starts on `day` at `start` (IST)."""
    await msg(api.store, mid, at=NOW - timedelta(days=1))
    eid = await make_event(api, mid, NOW - timedelta(days=1), day)
    if start is not None:
        await set_event(api, eid, starts_at=start, time_known=True, **kw)
    item = await card_for(api, mid)
    await api.join()
    return item, eid


async def reminders(api, user="a", **q):
    st, b = await api.call("GET", "/reminders", user, params=q)
    assert st == 200, b
    return b["reminders"]


def by_kind(items):
    return {i["kind"]: i for i in items}


async def test_recommended_reminders_for_the_quiz_card_are_20_00_the_day_before_and_07_00(api):
    item, eid = await quiz_card(api)
    card = (await api.call("GET", f"/items/{item}"))[1]
    assert [(r["kind"], r["fire_at"]) for r in card["recommended_reminders"]] == [
        ("recommended_day_before", "2026-11-01T20:00:00+05:30"), ("recommended_same_day", "2026-11-02T07:00:00+05:30")]
    rid = NEW()
    st, b = await api.call("PUT", f"/reminders/{rid}", json={"recommended": True, "item_id": item})
    assert st == 200
    assert [(r["id"], r["kind"], r["fire_at"]) for r in b["reminders"]] == [
        (rid, "recommended_day_before", "2026-11-01T20:00:00+05:30"), (R.sibling_id(rid, SECRET), "recommended_same_day", "2026-11-02T07:00:00+05:30")]
    got = await reminders(api)
    assert [(r["kind"], r["fire_at"], r["virtual"]) for r in got] == [          # the automatic pair is not doubled
        ("recommended_day_before", "2026-11-01T20:00:00+05:30", False), ("recommended_same_day", "2026-11-02T07:00:00+05:30", False)]
    assert got[0]["title"] == "Quiz 1" and got[0]["body"] == "Tomorrow: Mon 2 Nov, 10:00" and got[1]["body"] == "Today: Mon 2 Nov, 10:00"
    assert {r["item_id"] for r in got} == {item}
    again = await api.call("PUT", f"/reminders/{rid}", json={"recommended": True, "item_id": item})   # idempotent
    assert again[0] == 200 and len(await reminders(api)) == 2
    card = (await api.call("GET", f"/items/{item}"))[1]
    assert {r["kind"] for r in card["user"]["reminders"]} == {"recommended_day_before", "recommended_same_day"}


async def test_a_new_member_has_automatic_reminders_for_important_events_and_can_delete_or_edit_them(api):
    item, eid = await quiz_card(api)
    got = await reminders(api)
    assert [(r["kind"], r["virtual"], r["fire_at"]) for r in got] == [
        ("auto_day_before", True, "2026-11-01T20:00:00+05:30"), ("auto_same_day", True, "2026-11-02T07:00:00+05:30")]
    assert {r["item_id"] for r in got} == {item} and {r["event_id"] for r in got} == {eid}
    day_id, same_id = by_kind(got)["auto_day_before"]["id"], by_kind(got)["auto_same_day"]["id"]
    assert day_id == R.virtual_id(A, eid, "auto_day_before", SECRET)
    assert (await api.call("DELETE", f"/reminders/{day_id}"))[0] == 204                        # deleting a virtual stores a tombstone
    assert [r["kind"] for r in await reminders(api)] == ["auto_same_day"]
    assert (await api.call("DELETE", f"/reminders/{day_id}"))[0] == 204                        # idempotent
    api.clock.advance(hours=1)
    assert [r["kind"] for r in await reminders(api)] == ["auto_same_day"]                      # it does not come back
    st, b = await api.call("PUT", f"/reminders/{same_id}", json={"kind": "auto_same_day", "event_id": eid, "offset_minutes": -45})
    assert st == 200 and b["reminder"]["fire_at"] == "2026-11-02T09:15:00+05:30" and b["reminder"]["virtual"] is False
    assert (await reminders(api))[0]["fire_at"] == "2026-11-02T09:15:00+05:30"
    assert (await api.call("PUT", f"/reminders/{NEW()}", json={"kind": "auto_same_day", "event_id": eid, "offset_minutes": -5}))[0] == 400   # wrong id
    await api.call("PATCH", "/me/settings", json={"reminders": {"auto_for_important": False}})
    assert [r["id"] for r in await reminders(api)] == [same_id]                                 # only the one the user edited remains
    await api.call("PATCH", "/me/settings", json={"reminders": {"auto_for_important": True}, "important_types": ["exam"]})
    assert [r["id"] for r in await reminders(api)] == [same_id]


async def test_deleting_an_unknown_reminder_is_404_and_another_users_is_untouchable(api):
    item, eid = await quiz_card(api)
    assert (await api.call("DELETE", f"/reminders/{NEW()}"))[0] == 404
    assert (await api.call("DELETE", "/reminders/not-a-uuid"))[0] == 404
    rid = NEW()
    await api.call("PUT", f"/reminders/{rid}", json={"kind": "manual", "item_id": item, "offset_minutes": -30})
    await api.join("b")
    assert (await api.call("PUT", f"/reminders/{rid}", "b", json={"kind": "manual", "item_id": item, "offset_minutes": -5}))[0] == 404
    assert (await api.call("DELETE", f"/reminders/{rid}", "b"))[0] == 404
    assert [r["offset_minutes"] for r in await reminders(api) if r["kind"] == "manual"] == [-30]
    assert not any(r["kind"] == "manual" for r in await reminders(api, user="b"))


async def test_manual_offset_and_absolute_reminders(api):
    item, eid = await quiz_card(api)
    a, b = NEW(), NEW()
    st, body = await api.call("PUT", f"/reminders/{a}", json={"kind": "manual", "item_id": item, "offset_minutes": -90})
    assert st == 200 and body["reminder"]["fire_at"] == "2026-11-02T08:30:00+05:30"
    when = (NOW + timedelta(hours=3)).isoformat()
    st, body = await api.call("PUT", f"/reminders/{b}", json={"kind": "manual", "item_id": item, "fire_at": when})
    assert st == 200 and body["reminder"]["fire_at"].startswith("2026-10-26T20:30")
    got = {r["id"]: r for r in await reminders(api)}
    assert got[a]["offset_minutes"] == -90 and got[b]["offset_minutes"] is None
    assert (await api.call("DELETE", f"/reminders/{a}"))[0] == 204
    assert a not in {r["id"] for r in await reminders(api)}
    # PUT again with the same id revives a deleted reminder with the new value
    await api.call("PUT", f"/reminders/{a}", json={"kind": "manual", "item_id": item, "offset_minutes": -15})
    assert {r["id"]: r for r in await reminders(api)}[a]["fire_at"] == "2026-11-02T09:45:00+05:30"


async def test_a_manual_reminder_on_a_card_with_no_event_is_titled_with_the_cards_text(api):
    await msg(api.store, "plain", text="Please read chapter 4 before class", at=NOW - timedelta(hours=2))
    item = await card_for(api, "plain")
    await api.join()
    when = (NOW + timedelta(hours=5)).isoformat()
    st, _ = await api.call("PUT", f"/reminders/{NEW()}", json={"kind": "manual", "item_id": item, "fire_at": when})
    assert st == 200
    [r] = await reminders(api)
    assert r["title"] == "Please read chapter 4 before class" and r["body"] == "Reminder you set" and r["event_id"] is None


async def test_reminders_move_with_the_event_and_cancelled_events_are_silent(api):
    item, eid = await quiz_card(api)
    await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item})
    await api.call("PUT", f"/reminders/{NEW()}", json={"kind": "manual", "item_id": item, "offset_minutes": -60})
    before = await reminders(api)
    await msg(api.store, "move", at=NOW - timedelta(hours=1), text="Quiz moved")
    await reschedule(api, eid, "move", NOW - timedelta(hours=1), date(2026, 11, 4))
    await set_event(api, eid, starts_at=at(2026, 11, 4, 15))
    after = await reminders(api)
    assert [r["fire_at"] for r in before] == ["2026-11-01T20:00:00+05:30", "2026-11-02T07:00:00+05:30", "2026-11-02T09:00:00+05:30"]
    assert [r["fire_at"] for r in after] == ["2026-11-03T20:00:00+05:30", "2026-11-04T10:00:00+05:30", "2026-11-04T14:00:00+05:30"]
    assert after[0]["body"] == "Tomorrow: Wed 4 Nov, 15:00"
    await set_event(api, eid, status="cancelled")
    assert await reminders(api) == []
    await set_event(api, eid, status="scheduled")
    assert [r["fire_at"] for r in await reminders(api)] == [r["fire_at"] for r in after]       # uncancelled: all back


async def test_the_etag_changes_when_a_time_changes_and_not_otherwise(api):
    item, eid = await quiz_card(api)
    one = (await api.call("GET", "/reminders"))[1]["etag"]
    assert (await api.call("GET", "/reminders"))[1]["etag"] == one
    await set_event(api, eid, starts_at=at(2026, 11, 2, 16))
    assert (await api.call("GET", "/reminders"))[1]["etag"] != one


async def test_days_window_and_the_default_30(api):
    item, eid = await quiz_card(api, mid="far", day=date(2026, 12, 20), start=at(2026, 12, 20, 10))
    assert await reminders(api) == []                                                         # 55 days away: not scheduled yet
    assert len(await reminders(api, days=60)) == 2
    for bad in ("0", "91", "x"):
        assert (await api.call("GET", "/reminders", params={"days": bad}))[0] == 400


@pytest.mark.parametrize("body,status", [
    ({"kind": "manual", "offset_minutes": -5}, 400),                                  # no item
    ({"kind": "manual", "item_id": "ITEM"}, 400),                                     # neither offset nor time
    ({"kind": "manual", "item_id": "ITEM", "offset_minutes": -5, "fire_at": "2026-10-27T10:00:00+05:30"}, 400),   # both
    ({"kind": "manual", "item_id": "ITEM", "offset_minutes": 5}, 400),                # after the start
    ({"kind": "manual", "item_id": "ITEM", "offset_minutes": -50000}, 400),
    ({"kind": "manual", "item_id": "ITEM", "offset_minutes": 1.5}, 400),
    ({"kind": "manual", "item_id": "ITEM", "offset_minutes": True}, 400),
    ({"kind": "manual", "item_id": "ITEM", "fire_at": "2026-10-25T10:00:00+05:30"}, 400),   # the past
    ({"kind": "manual", "item_id": "ITEM", "fire_at": "2026-10-27T10:00:00"}, 400),          # no offset
    ({"kind": "manual", "item_id": "ITEM", "fire_at": "2030-10-27T10:00:00+05:30"}, 400),    # too far
    ({"kind": "manual", "item_id": "ITEM", "fire_at": 5}, 400),
    ({"kind": "weird", "item_id": "ITEM", "offset_minutes": -5}, 400),
    ({"kind": "manual", "item_id": "ITEM", "offset_minutes": -5, "extra": 1}, 400),
    ({"kind": "manual", "item_id": "ITEM", "event_id": 9999, "offset_minutes": -5}, 404),
    ({"kind": "manual", "item_id": 999999, "offset_minutes": -5}, 404),
    ({"kind": "manual", "item_id": True, "offset_minutes": -5}, 400),
    ({"recommended": True}, 400),
    ({"recommended": True, "item_id": "ITEM", "kind": "manual"}, 400),
])
async def test_put_validation(api, body, status):
    item, eid = await quiz_card(api)
    body = {k: (item if v == "ITEM" else v) for k, v in body.items()}
    st, _ = await api.call("PUT", f"/reminders/{NEW()}", json=body)
    assert st == status
    assert [r for r in await reminders(api) if r["kind"] == "manual"] == []                  # nothing was stored


async def test_bad_path_or_body(api):
    await quiz_card(api)
    assert (await api.call("PUT", "/reminders/not-a-uuid", json={"kind": "manual"}))[0] == 400
    assert (await api.call("PUT", f"/reminders/{NEW()}", data="{nope"))[0] == 400


async def test_an_offset_needs_a_dated_event(api):
    await msg(api.store, "plain", at=NOW - timedelta(hours=2))
    item = await card_for(api, "plain")
    await api.join()
    assert (await api.call("PUT", f"/reminders/{NEW()}", json={"kind": "manual", "item_id": item, "offset_minutes": -30}))[0] == 400
    assert (await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item}))[0] == 400
    assert (await api.call("GET", f"/items/{item}"))[1]["recommended_reminders"] == []


async def test_recommended_needs_an_upcoming_event_and_a_time_that_is_left(api):
    item, eid = await quiz_card(api)
    api.clock.t = at(2026, 11, 2, 8, 30)                                                        # both times are past
    assert (await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item}))[0] == 400
    api.clock.t = at(2026, 11, 1, 21)                                                           # Sun 1 Nov 21:00 IST: only 07:00 is left
    st, b = await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item})
    assert st == 200 and [r["fire_at"] for r in b["reminders"]] == [None, "2026-11-02T07:00:00+05:30"]
    card = (await api.call("GET", f"/items/{item}"))[1]
    assert [r["fire_at"] for r in card["recommended_reminders"]] == [None, "2026-11-02T07:00:00+05:30"]
    api.clock.t = at(2026, 11, 3, 12)                                                           # the event is over
    assert (await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item}))[0] == 400


async def test_a_card_with_two_events_needs_the_event_id(api):
    item, e1 = await quiz_card(api)
    e2 = await make_event(api, "orig", NOW - timedelta(days=1), date(2026, 11, 9))             # a second event from the same message
    await set_event(api, e2, starts_at=at(2026, 11, 9, 11), time_known=True, title="Quiz 2")
    await api.build()
    assert (await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item}))[0] == 400
    st, b = await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item, "event_id": e2})
    assert st == 200 and b["reminders"][0]["title"] == "Quiz 2"
    assert (await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item, "event_id": 99999}))[0] == 404


async def test_leaving_the_team_removes_its_reminders_and_hides_other_users_events(api):
    item, eid = await quiz_card(api)
    await api.call("PUT", f"/reminders/{NEW()}", json={"kind": "manual", "item_id": item, "offset_minutes": -30})
    day_id = R.virtual_id(A, eid, "auto_day_before", SECRET)
    await api.call("PUT", f"/reminders/{day_id}", json={"kind": "auto_day_before", "event_id": eid, "offset_minutes": -600})   # event-only row
    assert len(await reminders(api)) == 3
    await api.call("DELETE", "/subscriptions/T1")
    assert await reminders(api) == []
    assert await api.store.list_reminders(A, include_deleted=True) == []                      # nothing is left to fire after rejoining either
    assert await reminders(api, user="b") == []                                              # B never joined: nothing of A's, nor of the team's


async def test_unconfirmed_events_get_no_automatic_reminders_but_can_be_reminded_manually(api):
    item, eid = await quiz_card(api, needs_review=True)
    assert await reminders(api) == []
    st, _ = await api.call("PUT", f"/reminders/{NEW()}", json={"recommended": True, "item_id": item})
    assert st == 200 and len(await reminders(api)) == 2
    card = (await api.call("GET", f"/items/{item}"))[1]
    assert card["events"][0]["unconfirmed"] is True


async def test_requires_a_token(api):
    for method, path in (("GET", "/reminders"), ("PUT", f"/reminders/{NEW()}"), ("DELETE", f"/reminders/{NEW()}")):
        r = await api.client.request(method, "/api/v1" + path)
        assert r.status == 401


async def test_nobody_can_squat_an_id_so_that_a_victims_delete_silently_does_nothing(api):
    item, eid = await quiz_card(api)
    victim_id = R.virtual_id(A, eid, "auto_day_before", SECRET)
    await api.call("GET", "/me", "b")                                                         # B exists (a request creates the user)
    # an attacker who somehow got a row onto the victim's reminder id (here: written straight to storage)
    await api.store.upsert_reminder({"id": victim_id, "user_id": B, "kind": "manual", "fire_at": NOW + timedelta(days=1)}, NOW)
    st, _ = await api.call("DELETE", f"/reminders/{victim_id}")
    assert st == 404                                                                          # never "204 deleted" when nothing was
    st, _ = await api.call("PUT", f"/reminders/{victim_id}", json={"kind": "auto_day_before", "event_id": eid, "offset_minutes": -5})
    assert st == 404
    assert (await api.store.get_reminder(B, victim_id))["deleted_at"] is None                 # the attacker's row is untouched too


async def test_a_users_put_cannot_overwrite_another_users_row_by_reusing_its_id(api):
    item, eid = await quiz_card(api)
    rid = NEW()
    await api.call("PUT", f"/reminders/{rid}", json={"kind": "manual", "item_id": item, "offset_minutes": -30})
    await api.join("b")
    assert (await api.call("PUT", f"/reminders/{rid}", "b", json={"recommended": True, "item_id": item}))[0] == 404
    assert (await api.store.get_reminder(A, rid))["offset_minutes"] == -30
    assert await api.store.get_reminder(B, rid) is None
