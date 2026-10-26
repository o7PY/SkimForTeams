"""Reminder engine (PHASE4.md P4-D-116..120): pure functions, the ONLY place reminder fire times are computed.

Rows are stored as intent (event + kind, or event + offset, or an absolute time); fire times are worked out here at read time from
the event's CURRENT start, the user's settings and `now`. So a reschedule moves them, a cancelled event suppresses them, an
uncancelled one brings them back, and a reminder in the past is simply not returned. Everything is in Asia/Kolkata."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from .eventtime import DEAD_STATUSES, IST, is_upcoming

NAMESPACE = uuid.UUID("5b1c1c6e-3a6e-4d0b-9f6f-2b7a1d4c8e10")  # fixed, so virtual reminder ids are stable across servers
DAY_BEFORE, SAME_DAY = "day_before", "same_day"
KIND_RULE = {"recommended_day_before": DAY_BEFORE, "auto_day_before": DAY_BEFORE,
             "recommended_same_day": SAME_DAY, "auto_same_day": SAME_DAY}
AUTO_KINDS = ("auto_day_before", "auto_same_day")
MAX_SCHEDULED = 60       # P4-D-120: what the app schedules at most
DEFAULT_DAYS = 30


@dataclass(frozen=True)
class Scheduled:
    id: str
    kind: str
    item_id: int | None
    event_id: int | None
    fire_at: datetime            # UTC
    virtual: bool                # an automatic reminder the user has not edited or deleted (no row stored)
    offset_minutes: int | None = None


def virtual_id(user_id: str, event_id: int, kind: str) -> str:
    """The id an automatic reminder has before it is stored; the app can PUT/DELETE it like any other."""
    return str(uuid.uuid5(NAMESPACE, f"{user_id}:{event_id}:{kind}"))


def sibling_id(reminder_id: str) -> str:
    """The id of the second row created by 'recommended reminders' from the one uuid the client generated."""
    return str(uuid.uuid5(NAMESPACE, f"{reminder_id}:same_day"))


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def event_day(e: dict) -> date | None:
    """The IST calendar day of the event."""
    if e.get("starts_at"):
        return e["starts_at"].astimezone(IST).date()
    return e.get("event_date")


def anchor(e: dict) -> datetime | None:
    """What an offset counts from: the start, or for an event with only a date, midnight at the start of that IST day."""
    if e.get("starts_at"):
        return e["starts_at"]
    d = e.get("event_date")
    return datetime.combine(d, time.min, tzinfo=IST).astimezone(timezone.utc) if d else None


def recommended_times(e: dict, rem: dict) -> dict[str, datetime | None]:
    """P4-D-116. Day before at `day_before_time`; same day `same_day_hours` before the start but never before `earliest`, and
    dropped (None) if that lands at or after the start. An event with no start time (date only) gets its same-day reminder at
    `earliest` (P4-D-155 / P4-D-160)."""
    d = event_day(e)
    if d is None:
        return {DAY_BEFORE: None, SAME_DAY: None}
    day_before = datetime.combine(d - timedelta(days=1), _hhmm(rem["day_before_time"]), tzinfo=IST).astimezone(timezone.utc)
    earliest = datetime.combine(d, _hhmm(rem["earliest"]), tzinfo=IST).astimezone(timezone.utc)
    start = e.get("starts_at")
    if start is None:
        same = earliest
    else:
        same = max(start - timedelta(hours=rem["same_day_hours"]), earliest)
        same = same if same < start else None
    return {DAY_BEFORE: day_before, SAME_DAY: same}


def fire_time(row: dict, e: dict | None, rem: dict) -> datetime | None:
    """One stored reminder's fire time now, or None if it cannot be worked out (no date, dropped by the clamp)."""
    if row.get("fire_at") is not None:
        return row["fire_at"]
    if e is None:
        return None
    if row.get("offset_minutes") is not None:
        a = anchor(e)
        return a + timedelta(minutes=row["offset_minutes"]) if a else None
    rule = KIND_RULE.get(row["kind"])
    return recommended_times(e, rem)[rule] if rule else None


def unconfirmed(e: dict) -> bool:
    return bool(e.get("needs_review")) or e.get("confidence") == "low"


def compute(user_id: str, rows: list[dict], events: dict[int, dict], settings: dict, now: datetime, *,
            item_for_event: Callable[[int], int | None] = lambda _e: None, days: int = DEFAULT_DAYS,
            cap: int = MAX_SCHEDULED) -> list[Scheduled]:
    """Every reminder the user should have scheduled in the next `days`, soonest first, at most `cap`.
    `events` holds only the events of the user's active teams; a reminder for any other event is ignored."""
    rem = settings["reminders"]
    horizon = now + timedelta(days=days)
    out: list[Scheduled] = []
    stored_ids = {r["id"] for r in rows}      # includes tombstones: a stored row, even deleted, replaces its virtual twin

    def live(t: datetime | None) -> bool:
        return t is not None and now < t <= horizon

    for r in rows:
        if r.get("deleted_at") is not None:
            continue
        e = events.get(r["event_id"]) if r.get("event_id") else None
        if r.get("event_id") and (e is None or e.get("status") in DEAD_STATUSES):
            continue                          # another team's / vanished event; or cancelled: suppressed, not deleted
        t = fire_time(r, e, rem)
        if live(t):
            out.append(Scheduled(r["id"], r["kind"], r.get("item_id") or (item_for_event(r["event_id"]) if r.get("event_id") else None),
                                 r.get("event_id"), t, False, r.get("offset_minutes")))

    if rem.get("auto_for_important"):
        important = set(settings["important_types"])
        for e in events.values():
            if e["type"] not in important or unconfirmed(e) or not is_upcoming(e, now):
                continue
            times = recommended_times(e, rem)
            for kind in AUTO_KINDS:
                vid = virtual_id(user_id, e["id"], kind)
                t = times[KIND_RULE[kind]]
                if vid in stored_ids or not live(t):
                    continue
                if any(s.event_id == e["id"] and abs(s.fire_at - t) < timedelta(minutes=1) for s in out):
                    continue                  # the user already has a reminder at that very time
                out.append(Scheduled(vid, kind, item_for_event(e["id"]), e["id"], t, True))
    out.sort(key=lambda s: (s.fire_at, s.id))
    return out[:cap]


def virtual_catalog(user_id: str, events: dict[int, dict]) -> dict[str, tuple[int, str]]:
    """{virtual id: (event id, kind)} for every event: lets DELETE/PUT of a not-yet-stored automatic reminder find its event."""
    return {virtual_id(user_id, e["id"], k): (e["id"], k) for e in events.values() for k in AUTO_KINDS}


def describe(e: dict | None, fire_at: datetime, fallback_title: str) -> tuple[str, str]:
    """Notification title and body: course/event info only, never a person's name (P4-D-137)."""
    if e is None:
        return fallback_title, "Reminder you set"
    start = e.get("starts_at")
    a = anchor(e)
    day = (start or a).astimezone(IST)
    when = day.strftime("%a %-d %b") + (f", {day.strftime('%H:%M')}" if start else "")
    gap = (a - fire_at) if a else timedelta(0)
    lead = "Tomorrow" if gap >= timedelta(hours=18) else "Today"
    body = f"{lead}: {when}" + (f" · {e['venue']}" if e.get("venue") else "")
    return e["title"], body
