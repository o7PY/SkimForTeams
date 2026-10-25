from datetime import date, datetime, time

import pytest

from skim.ai.schemas import Relative, When
from skim.ai.timeparse import IST, UTC, inherit_time, resolve_when, wants_same_place


def posted(s):  # "2026-10-20 11:00" in IST
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)


def rel(anchor, weekday=None, qualifier=None, days=None, weeks=None, **kw):
    return When(kind="relative", relative=Relative(anchor=anchor, weekday=weekday, qualifier=qualifier, days=days, weeks=weeks),
                **kw)


def absolute(d, t=None, end=None, text=""):
    return When(kind="absolute", date=d, time=t, end_time=end, text=text)


def R(when, at, deadline=False, existing=None):
    return resolve_when(when, posted(at), is_deadline=deadline, existing=existing)


# (name, when, posted, is_deadline, date, time "HH:MM"|None, time_known, defaulted, confidence cap)
CASES = [
    # --- every relative / tricky phrase in tests/eval/eval_messages.csv ---
    ("m4 this Friday (Tue)", rel("weekday", "fri", "this"), "2026-10-20 11:00", False, "2026-10-23", None, False, False, "high"),
    ("m8 tomorrow", rel("posted", days=1), "2026-10-22 09:00", False, "2026-10-23", None, False, False, "high"),
    ("m6 today", rel("posted", days=0), "2026-10-21 08:15", False, "2026-10-21", None, False, False, "high"),
    ("m14 next Monday (Tue), deadline", rel("weekday", "mon", "next"), "2026-10-27 10:00", True, "2026-11-02", "23:59", False, True, "high"),
    ("m25 tomorrow night, deadline", rel("posted", days=1), "2026-11-01 21:00", True, "2026-11-02", "23:59", False, True, "high"),
    ("m26 by Friday (Mon), deadline", rel("weekday", "fri"), "2026-11-02 08:00", True, "2026-11-06", "23:59", False, True, "high"),
    ("m28 today", rel("posted", days=0), "2026-11-03 09:00", False, "2026-11-03", None, False, False, "high"),
    ("m29 today at 2 PM", rel("posted", days=0, text="today"), "2026-11-03 13:00", False, "2026-11-03", None, False, False, "high"),
    ("m36 tomorrow (Sunday) 4 PM", rel("posted", days=1), "2026-11-07 10:00", False, "2026-11-08", None, False, False, "high"),
    ("m38 Wednesday + 1 week, deadline", rel("weekday", "wed", weeks=1), "2026-11-09 20:00", True, "2026-11-18", "23:59", False, True, "medium"),
    ("m39 this Wednesday", rel("weekday", "wed", "this"), "2026-11-10 09:00", False, "2026-11-11", None, False, False, "high"),
    ("m39 this Friday", rel("weekday", "fri", "this"), "2026-11-10 09:00", False, "2026-11-13", None, False, False, "high"),
    ("m42 Thursday (Wed)", rel("weekday", "thu"), "2026-11-11 11:00", False, "2026-11-12", None, False, False, "high"),
    ("m47 tonight 11:59 PM stated", rel("posted", days=0), "2026-11-13 08:00", True, "2026-11-13", "23:59", False, True, "high"),
    ("m49 next Thursday (Fri)", rel("weekday", "thu", "next"), "2026-11-13 15:05", False, "2026-11-19", None, False, False, "high"),
    ("m48 first week of December", When(kind="vague", text="first week of December"), "2026-11-13 15:00", False, None, None, False, False, "low"),
    ("m27 last week of classes", When(kind="vague"), "2026-11-02 08:05", False, None, None, False, False, "low"),
    ("m17 nothing", When(kind="none"), "2026-10-28 09:35", False, None, None, False, False, "high"),
    # --- absolute dates ---
    ("m3 stated 11:59 PM", absolute("2026-10-26", "23:59"), "2026-10-19 17:30", True, "2026-10-26", "23:59", True, False, "high"),
    ("m7 range 11-12", absolute("2026-10-24", "11:00", "12:00"), "2026-10-21 20:00", False, "2026-10-24", "11:00", True, False, "high"),
    ("m18 date only", absolute("2026-11-04"), "2026-10-28 19:00", False, "2026-11-04", None, False, False, "high"),
    ("deadline without time", absolute("2026-11-13"), "2026-11-02 08:05", True, "2026-11-13", "23:59", False, True, "high"),
    # --- edge cases ---
    ("weekday today, time not yet passed -> today", rel("weekday", "fri"), "2026-10-23 09:00", False, "2026-10-23", None, False, False, "medium"),
    ("posted-day offset in 3 days", rel("offset", days=3), "2026-10-23 09:00", False, "2026-10-26", None, False, False, "high"),
    ("in 2 weeks", rel("offset", weeks=2), "2026-10-23 09:00", False, "2026-11-06", None, False, False, "high"),
    ("next Friday said Monday is ambiguous", rel("weekday", "fri", "next"), "2026-10-19 09:00", False, "2026-10-23", None, False, False, "medium"),
    ("next Monday said Friday is not", rel("weekday", "mon", "next"), "2026-10-23 09:00", False, "2026-10-26", None, False, False, "high"),
    ("next Friday said on Friday = a week later", rel("weekday", "fri", "next"), "2026-10-23 09:00", False, "2026-10-30", None, False, False, "high"),
    ("year rollover: LLM guessed the past year", absolute("2026-01-04"), "2026-12-28 10:00", False, "2027-01-04", None, False, False, "high"),
    ("year rollover: weekday across new year", rel("weekday", "mon"), "2026-12-30 10:00", False, "2027-01-04", None, False, False, "high"),
    ("LLM guessed a far-future year", absolute("2027-11-04"), "2026-10-28 19:00", False, "2026-11-04", None, False, False, "high"),
    ("leap day kept", absolute("2028-02-29"), "2028-02-01 10:00", False, "2028-02-29", None, False, False, "high"),
    ("end before start is dropped", absolute("2026-10-24", "23:00", "01:00"), "2026-10-21 20:00", False, "2026-10-24", "23:00", True, False, "high"),
]


@pytest.mark.parametrize("name,when,at,deadline,d,t,known,defaulted,conf", CASES, ids=[c[0] for c in CASES])
def test_resolve(name, when, at, deadline, d, t, known, defaulted, conf):
    r = R(when, at, deadline)
    assert r.event_date == (date.fromisoformat(d) if d else None)
    assert (r.time.strftime("%H:%M") if r.time else None) == t
    assert (r.time_known, r.time_defaulted, r.max_confidence) == (known, defaulted, conf)
    assert r.needs_review == (conf == "low")


def test_weekday_today_after_the_stated_time_means_next_week():
    morning = R(rel("weekday", "fri", time="15:00"), "2026-10-23 10:00")
    after = R(rel("weekday", "fri", time="15:00"), "2026-10-23 16:00")
    assert morning.event_date == date(2026, 10, 23) and after.event_date == date(2026, 10, 30)
    assert morning.max_confidence == "high"


def test_times_are_stored_as_utc():
    r = R(absolute("2026-10-30", "14:00", "16:00"), "2026-10-29 08:00")
    assert r.starts_at == datetime(2026, 10, 30, 8, 30, tzinfo=UTC) and r.ends_at == datetime(2026, 10, 30, 10, 30, tzinfo=UTC)
    assert r.starts_at.astimezone(IST).hour == 14


def test_deadline_default_is_2359_ist_in_utc():
    r = R(absolute("2026-10-26"), "2026-10-19 17:30", deadline=True)
    assert r.starts_at == datetime(2026, 10, 26, 18, 29, tzinfo=UTC) and r.ends_at is None


def test_date_only_has_no_starts_at():
    r = R(absolute("2026-11-04"), "2026-10-28 19:00")
    assert r.starts_at is None and r.ends_at is None and r.event_date == date(2026, 11, 4)


def test_posted_time_not_processing_time_decides():
    # same words, different posted day, different answer
    assert R(rel("posted", days=1), "2026-10-22 09:00").event_date == date(2026, 10, 23)
    assert R(rel("posted", days=1), "2026-12-31 23:30").event_date == date(2027, 1, 1)


def test_late_night_utc_posted_time_uses_the_ist_day():
    utc_posted = datetime(2026, 10, 22, 20, 0, tzinfo=UTC)  # = 23 Oct 01:30 IST
    r = resolve_when(rel("posted", days=1), utc_posted)
    assert r.event_date == date(2026, 10, 24)


def test_garbage_inputs_never_invent_a_date():
    assert R(When(kind="absolute", date=None), "2026-10-20 11:00").needs_review
    assert R(When(kind="absolute", date="2026-02-30"), "2026-10-20 11:00").event_date is None
    assert R(When(kind="relative", relative=None), "2026-10-20 11:00").event_date is None
    assert R(When(kind="relative", relative=Relative(anchor="weekday")), "2026-10-20 11:00").event_date is None


def test_naive_posted_time_is_rejected():
    with pytest.raises(ValueError):
        resolve_when(absolute("2026-10-20"), datetime(2026, 10, 20, 11, 0))


# ---- "same time / same place" (msgs 22, 30) ----
EXISTING_TIMED = {"event_date": date(2026, 10, 30), "starts_at": datetime(2026, 10, 30, 8, 30, tzinfo=UTC),
                  "ends_at": datetime(2026, 10, 30, 10, 30, tzinfo=UTC), "time_known": True, "time_defaulted": False}
EXISTING_DATE_ONLY = {"event_date": date(2026, 11, 4), "starts_at": None, "ends_at": None, "time_known": False,
                      "time_defaulted": False}


def test_same_time_copies_the_existing_stated_time():  # msg 22
    r = inherit_time(R(absolute("2026-11-03"), "2026-10-30 10:00"), EXISTING_TIMED, "moved to Tuesday, same time and place")
    assert r.event_date == date(2026, 11, 3) and r.time == time(14, 0) and r.end_time == time(16, 0) and r.time_known


def test_same_time_with_no_existing_time_stays_date_only():  # msg 30: never invent
    r = inherit_time(R(absolute("2026-11-06"), "2026-11-04 07:30"), EXISTING_DATE_ONLY, "postponed to Friday, same time")
    assert r.event_date == date(2026, 11, 6) and r.time is None and not r.time_known and r.starts_at is None


def test_no_same_time_phrase_means_no_copy():
    r = inherit_time(R(absolute("2026-11-03"), "2026-10-30 10:00"), EXISTING_TIMED, "moved to Tuesday")
    assert r.time is None


def test_stated_time_is_never_overwritten():
    r = inherit_time(R(absolute("2026-11-03", "09:00"), "2026-10-30 10:00"), EXISTING_TIMED, "Tuesday 9 AM, same time")
    assert r.time == time(9, 0)


def test_same_as_existing_kind_copies_everything_and_never_invents():
    assert R(When(kind="same_as_existing"), "2026-10-30 10:00", existing=EXISTING_TIMED).starts_at == EXISTING_TIMED["starts_at"]
    r = R(When(kind="same_as_existing"), "2026-10-30 10:00", existing=None)
    assert r.event_date is None and r.time is None


@pytest.mark.parametrize("s,yes", [("same time and place", True), ("same place", True), ("in the same room", True),
                                   ("same venue", True), ("same time", False), ("in a different room", False)])
def test_same_place_phrases(s, yes):
    assert wants_same_place(s) is yes
