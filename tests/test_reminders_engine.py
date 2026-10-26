"""T6: the reminder engine (skim/reminders.py), table-driven (PHASE4.md 6.5). All times Asia/Kolkata unless stated."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from skim import reminders as R
from skim.api import user_settings

IST = ZoneInfo("Asia/Kolkata")
U = "11111111-1111-1111-1111-111111111111"
SET = user_settings.effective({})


def at(y, mo, d, h=0, mi=0):
    """An IST wall-clock time as an aware UTC datetime."""
    return datetime(y, mo, d, h, mi, tzinfo=IST).astimezone(timezone.utc)


NOW = at(2026, 10, 26, 12, 0)
NOAUTO = user_settings.effective({"reminders": {"auto_for_important": False}})   # tests of stored rows alone


def event(eid=1, start=None, day=None, typ="quiz", status="scheduled", end=None, **kw):
    d = day or (start.astimezone(IST).date() if start else None)
    return {"id": eid, "type": typ, "title": kw.pop("title", "Quiz 1"), "starts_at": start, "ends_at": end, "event_date": d,
            "time_known": start is not None, "time_defaulted": False, "venue": kw.pop("venue", None), "status": status,
            "confidence": kw.pop("confidence", "high"), "needs_review": kw.pop("needs_review", False), "is_deadline": False, **kw}


def row(kind="manual", rid="r1", event_id=1, item_id=10, offset=None, fire=None, deleted=None):
    return {"id": rid, "item_id": item_id, "event_id": event_id, "kind": kind, "offset_minutes": offset, "fire_at": fire,
            "deleted_at": deleted}


def run(rows=(), events=(), settings=SET, now=NOW, **kw):  # (settings default: automatic reminders ON, like a new user)
    return R.compute(U, list(rows), {e["id"]: e for e in events}, settings, now, **kw)


def run_noauto(rows=(), events=(), **kw):
    return run(rows, events, settings=NOAUTO, **kw)


def times(sched):
    return {s.kind: s.fire_at for s in sched}


# ---- the recommended pair (P4-D-116) ----
@pytest.mark.parametrize("name,start,day_before,same_day", [
    ("10:00 quiz: 05:00 is clamped up to 07:00", at(2026, 11, 2, 10), at(2026, 11, 1, 20), at(2026, 11, 2, 7)),
    ("15:00 event: 5 h before", at(2026, 11, 2, 15), at(2026, 11, 1, 20), at(2026, 11, 2, 10)),
    ("12:00: exactly 07:00", at(2026, 11, 2, 12), at(2026, 11, 1, 20), at(2026, 11, 2, 7)),
    ("12:01: 07:01 is later than the clamp", at(2026, 11, 2, 12, 1), at(2026, 11, 1, 20), at(2026, 11, 2, 7, 1)),
    ("07:30: 07:00 is before the start, kept", at(2026, 11, 2, 7, 30), at(2026, 11, 1, 20), at(2026, 11, 2, 7)),
    ("07:00: clamp lands AT the start, dropped", at(2026, 11, 2, 7), at(2026, 11, 1, 20), None),
    ("06:00: clamp lands after the start, dropped", at(2026, 11, 2, 6), at(2026, 11, 1, 20), None),
    ("00:30: 5 h before is the previous evening, clamp is after the start: dropped", at(2026, 11, 2, 0, 30), at(2026, 11, 1, 20), None),
    ("deadline 23:59: 18:59", at(2026, 11, 6, 23, 59), at(2026, 11, 5, 20), at(2026, 11, 6, 18, 59)),
])
def test_recommended_times(name, start, day_before, same_day):
    t = R.recommended_times(event(start=start), SET["reminders"])
    assert t[R.DAY_BEFORE] == day_before and t[R.SAME_DAY] == same_day, name


def test_date_only_event_gets_day_before_and_a_same_day_reminder_at_the_earliest_time():
    t = R.recommended_times(event(day=date(2026, 11, 2)), SET["reminders"])
    assert t == {R.DAY_BEFORE: at(2026, 11, 1, 20), R.SAME_DAY: at(2026, 11, 2, 7)}


def test_an_event_with_no_date_has_no_reminders():
    assert R.recommended_times(event(), SET["reminders"]) == {R.DAY_BEFORE: None, R.SAME_DAY: None}
    assert run([row("recommended_day_before")], [event()]) == []


def test_settings_change_the_times():
    s = user_settings.effective({"reminders": {"day_before_time": "21:30", "same_day_hours": 4.5, "earliest": "08:00"}})
    t = R.recommended_times(event(start=at(2026, 11, 2, 15)), s["reminders"])
    assert t == {R.DAY_BEFORE: at(2026, 11, 1, 21, 30), R.SAME_DAY: at(2026, 11, 2, 10, 30)}
    t = R.recommended_times(event(start=at(2026, 11, 2, 10)), s["reminders"])
    assert t[R.SAME_DAY] == at(2026, 11, 2, 8)                                       # 05:30 clamped to the user's 08:00


# ---- stored reminders ----
def test_a_recommended_pair_for_the_10am_quiz():
    e = event(start=at(2026, 11, 2, 10))
    out = run_noauto([row("recommended_day_before", "a"), row("recommended_same_day", "b")], [e])
    assert [(s.id, s.fire_at, s.virtual) for s in out] == [("a", at(2026, 11, 1, 20), False), ("b", at(2026, 11, 2, 7), False)]


def test_a_reschedule_moves_every_kind_of_reminder_because_nothing_stores_a_time():
    rows = [row("recommended_day_before", "a"), row("recommended_same_day", "b"), row("manual", "c", offset=-90)]
    before = run_noauto(rows, [event(start=at(2026, 11, 2, 10))])
    after = run_noauto(rows, [event(start=at(2026, 11, 4, 15))])
    assert [s.fire_at for s in before] == [at(2026, 11, 1, 20), at(2026, 11, 2, 7), at(2026, 11, 2, 8, 30)]
    assert [s.fire_at for s in after] == [at(2026, 11, 3, 20), at(2026, 11, 4, 10), at(2026, 11, 4, 13, 30)]


def test_manual_offset_and_absolute():
    e = event(start=at(2026, 11, 2, 10))
    out = run_noauto([row("manual", "o", offset=-60), row("manual", "x", event_id=None, fire=at(2026, 10, 30, 9))], [e])
    assert [(s.id, s.event_id, s.fire_at) for s in out] == [("x", None, at(2026, 10, 30, 9)), ("o", 1, at(2026, 11, 2, 9))]


def test_an_offset_on_a_date_only_event_counts_from_midnight_that_day():
    e = event(day=date(2026, 11, 2))
    [s] = run_noauto([row("manual", offset=-360)], [e])                                      # 6 h before midnight = 18:00 the day before
    assert s.fire_at == at(2026, 11, 1, 18)


def test_cancelled_events_suppress_their_reminders_and_uncancel_brings_them_back():
    rows = [row("recommended_day_before", "a"), row("manual", "m", offset=-60)]
    live = event(start=at(2026, 11, 2, 10))
    assert len(run_noauto(rows, [live])) == 2
    assert run_noauto(rows, [{**live, "status": "cancelled"}]) == []                         # suppressed, nothing deleted
    assert len(run_noauto(rows, [{**live, "status": "rescheduled"}])) == 2                   # a reschedule keeps them
    assert len(run_noauto(rows, [{**live, "status": "scheduled"}])) == 2                     # uncancelled: back
    assert run_noauto(rows, [{**live, "status": "done"}]) == []


def test_deleted_rows_and_past_times_and_the_horizon_are_dropped():
    e = event(start=at(2026, 11, 2, 10))
    assert run_noauto([row("manual", offset=-60, deleted=NOW)], [e]) == []
    assert [s.kind for s in run_noauto([row("recommended_day_before", "a"), row("recommended_same_day", "b")], [e], now=at(2026, 11, 1, 21))] == ["recommended_same_day"]
    assert run_noauto([row("recommended_day_before", "a"), row("recommended_same_day", "b")], [e], now=at(2026, 11, 2, 7, 1)) == []
    assert run_noauto([row("manual", "x", event_id=None, fire=NOW)], [e]) == []              # exactly now is already past
    far = event(start=at(2026, 12, 20, 10))
    assert run_noauto([row("manual", offset=-60)], [far], days=30) == []                      # beyond 30 days: not scheduled yet
    assert len(run_noauto([row("manual", offset=-60)], [far], days=60)) == 1


def test_a_reminder_for_an_event_outside_the_users_teams_is_ignored():
    assert run_noauto([row("manual", offset=-60, event_id=99)], [event(start=at(2026, 11, 2, 10))]) == []


def test_each_reminder_opens_a_card():
    e = event(start=at(2026, 11, 2, 10))
    out = run_noauto([row("recommended_day_before", "a", item_id=None), row("manual", "b", item_id=7, offset=-5)], [e], item_for_event=lambda eid: 55)
    assert {s.id: s.item_id for s in out} == {"a": 55, "b": 7}


# ---- automatic reminders (P4-D-117) ----
def test_important_types_get_the_pair_automatically_as_virtual_reminders():
    e = event(start=at(2026, 11, 2, 10))
    out = run([], [e], item_for_event=lambda _e: 5)
    assert [(s.kind, s.fire_at, s.virtual, s.item_id) for s in out] == [
        ("auto_day_before", at(2026, 11, 1, 20), True, 5), ("auto_same_day", at(2026, 11, 2, 7), True, 5)]
    assert out[0].id == R.virtual_id(U, 1, "auto_day_before") != R.virtual_id(U, 1, "auto_same_day")


@pytest.mark.parametrize("why,ev,sets", [
    ("type not ticked", {"typ": "lab"}, {}),
    ("needs review", {"needs_review": True}, {}),
    ("low confidence", {"confidence": "low"}, {}),
    ("setting off", {}, {"reminders": {"auto_for_important": False}}),
    ("cancelled", {"status": "cancelled"}, {}),
    ("already over", {"start": at(2026, 10, 25, 10)}, {}),
])
def test_no_automatic_reminders(why, ev, sets):
    e = event(**{"start": at(2026, 11, 2, 10), **ev})
    assert run([], [e], settings=user_settings.effective(sets)) == [], why


def test_ticking_another_type_turns_automatic_reminders_on_for_it():
    e = event(start=at(2026, 11, 2, 10), typ="lab")
    assert run([], [e]) == []
    assert len(run([], [e], settings=user_settings.effective({"important_types": ["lab"]}))) == 2


def test_a_tombstone_keeps_an_automatic_reminder_from_coming_back_and_an_edit_replaces_it():
    e = event(start=at(2026, 11, 2, 10))
    vid_day, vid_same = R.virtual_id(U, 1, "auto_day_before"), R.virtual_id(U, 1, "auto_same_day")
    out = run([row("auto_day_before", vid_day, deleted=NOW)], [e])
    assert [s.kind for s in out] == ["auto_same_day"]                                  # the deleted one stays gone
    edited = run([row("auto_same_day", vid_same, offset=-30)], [e])
    assert [(s.kind, s.fire_at, s.virtual) for s in edited] == [("auto_day_before", at(2026, 11, 1, 20), True),
                                                                 ("auto_same_day", at(2026, 11, 2, 9, 30), False)]


def test_an_automatic_reminder_is_not_doubled_by_a_recommended_one_at_the_same_time():
    e = event(start=at(2026, 11, 2, 10))
    out = run([row("recommended_day_before", "a"), row("recommended_same_day", "b")], [e])
    assert [s.virtual for s in out] == [False, False] and len(out) == 2
    out = run([row("manual", "m", offset=-180)], [e])                                  # 07:00 = the same-day time exactly
    assert sorted(s.kind for s in out) == ["auto_day_before", "manual"]


def test_untimed_date_only_important_event_gets_20_00_the_day_before_and_07_00():
    out = run([], [event(day=date(2026, 11, 2))])
    assert [s.fire_at for s in out] == [at(2026, 11, 1, 20), at(2026, 11, 2, 7)]


def test_sixty_item_cap_soonest_first():
    events = [event(eid=i, start=at(2026, 10, 27, 10) + timedelta(days=i)) for i in range(1, 51)]   # 50 days x 2 automatic
    out = run([], events, days=90)
    assert len(out) == 60 and out == sorted(out, key=lambda s: (s.fire_at, s.id))
    all_ = run([], events, days=90, cap=1000)
    assert len(all_) == 100 and out == all_[:60]


def test_virtual_ids_are_stable_per_user_event_and_kind():
    assert R.virtual_id(U, 1, "auto_day_before") == R.virtual_id(U, 1, "auto_day_before")
    assert len({R.virtual_id(u, e, k) for u in (U, "22222222-2222-2222-2222-222222222222") for e in (1, 2) for k in R.AUTO_KINDS}) == 8
    assert R.sibling_id("abc") == R.sibling_id("abc") != "abc"
    cat = R.virtual_catalog(U, {1: event(), 2: event(eid=2)})
    assert cat[R.virtual_id(U, 2, "auto_same_day")] == (2, "auto_same_day") and len(cat) == 4


def test_notification_text_is_course_and_event_info_only():
    e = event(start=at(2026, 11, 2, 10), venue="LHC 104", title="Quiz 1")
    assert R.describe(e, at(2026, 11, 1, 20), "x") == ("Quiz 1", "Tomorrow: Mon 2 Nov, 10:00 · LHC 104")
    assert R.describe(e, at(2026, 11, 2, 7), "x") == ("Quiz 1", "Today: Mon 2 Nov, 10:00 · LHC 104")
    assert R.describe(event(day=date(2026, 11, 2)), at(2026, 11, 1, 20), "x") == ("Quiz 1", "Tomorrow: Mon 2 Nov")
    assert R.describe(None, NOW, "Read the syllabus") == ("Read the syllabus", "Reminder you set")
