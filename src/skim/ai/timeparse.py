"""Deterministic date/time normalisation (PHASE3 §3.3, P3-D-010/011). The LLM proposes, this module decides.

Pure: no I/O, no clock. Everything is computed from the message's POSTED time in the course timezone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .schemas import When

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DEADLINE_DEFAULT = time(23, 59)
_SAME_TIME = re.compile(r"\bsame\s+(?:time|slot|timing)s?\b", re.I)
_SAME_PLACE = re.compile(r"\bsame\s+(?:place|venue|room|location|hall)\b|\bsame\s+time\s+and\s+(?:place|venue)\b", re.I)
_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class Resolved:
    event_date: date | None = None          # IST calendar date
    time: time | None = None                # IST wall clock (None = no time)
    end_time: time | None = None
    time_known: bool = False                # a time was STATED (or copied from an existing event's stated time)
    time_defaulted: bool = False            # 23:59 deadline default (P3-D-011)
    max_confidence: str = "high"            # cap the time rules put on the item's confidence
    needs_review: bool = False
    reason: str = ""

    def _utc(self, t: time | None) -> datetime | None:
        if self.event_date is None or t is None:
            return None
        return datetime.combine(self.event_date, t, tzinfo=IST).astimezone(UTC)

    @property
    def starts_at(self) -> datetime | None:
        return self._utc(self.time)

    @property
    def ends_at(self) -> datetime | None:
        return self._utc(self.end_time) if self.time_known else None


def cap(confidence: str, ceiling: str) -> str:
    return confidence if _RANK[confidence] <= _RANK[ceiling] else ceiling


def _clock(s: str | None) -> time | None:
    return None if s is None else time(int(s[:2]), int(s[3:]))


FAR_FUTURE_DAYS = 270   # about nine months


def _year_stated(d: date, when: When, source_text: str) -> bool:
    """Did the message itself say the year the model chose? (the phrase, or anywhere in the message)."""
    return bool(re.search(rf"(?<!\d){d.year}(?!\d)", f"{when.text or ''} {source_text or ''}"))


def _resolve_year(d: date, today: date, when: When, source_text: str) -> tuple[date, str, str]:
    """(date, confidence cap, review reason). PHASE3 §3.3: a date with NO year is its next occurrence on or after the posted
    date, whatever year the model guessed. If that is implausibly far away it is kept but flagged (low + review); a past date
    is never stored silently. A year that the message states is taken as written; if it is already past it is flagged."""
    if _year_stated(d, when, source_text):
        return (d, "medium", "stated-past-date") if d < today - timedelta(days=1) else (d, "high", "")
    try:
        cand = d.replace(year=today.year)
        if cand < today:
            cand = cand.replace(year=today.year + 1)
    except ValueError:  # 29 Feb in a non-leap year: leave the model's reading, but flag it
        return d, "low", "bad-leap-date"
    if (cand - today).days > FAR_FUTURE_DAYS:
        return cand, "low", "year-inferred-far"
    return cand, "high", ""


def _weekday_date(today: date, target: int, qualifier: str | None, at: time | None, posted_clock: time) -> tuple[date, str, str]:
    """Return (date, confidence, why). "next X" = strictly after today; bare/"this X" = next occurrence, today allowed
    only when the stated time has not passed yet (§3.3)."""
    ahead = (target - today.weekday()) % 7
    if qualifier == "next":
        d = today + timedelta(days=ahead or 7)
        # "next Friday" said on Monday could mean this week's or next week's Friday: only unambiguous when the soonest
        # occurrence is already in the following calendar week.
        same_week = d.isocalendar()[:2] == today.isocalendar()[:2]
        return d, ("medium" if same_week else "high"), "next-weekday"
    if ahead == 0:
        if at is not None and at <= posted_clock:
            return today + timedelta(days=7), "high", "weekday-today-passed"
        return today, ("high" if at is not None else "medium"), "weekday-today"
    return today + timedelta(days=ahead), "high", "weekday"


def resolve_when(when: When, posted: datetime, *, is_deadline: bool = False, existing: dict | None = None,
                 source_text: str = "") -> Resolved:
    """Turn the LLM's `when` into dates. `existing` (the matched event) supplies "same time/place" values."""
    if posted.tzinfo is None:
        raise ValueError("posted must be timezone-aware")
    p = posted.astimezone(IST)
    today, now_clock = p.date(), p.time().replace(second=0, microsecond=0)
    at, end = _clock(when.time), _clock(when.end_time)
    conf, why = "high", ""
    review = False

    if when.kind in ("none", "same_as_existing") and at is not None:
        # A time with no date ("the quiz will be 10:15-10:45"): it belongs to the matched event's date. Never invented.
        if existing and existing.get("event_date"):
            return Resolved(existing["event_date"], at, end if end and end > at else None, True, False, "high", False,
                            "time-on-existing-date")
        return Resolved(max_confidence="low", needs_review=True, reason="time-without-date")
    if when.kind == "none":
        return Resolved(reason="no-date")
    if when.kind == "vague":
        return Resolved(max_confidence="low", needs_review=True, reason="vague")  # P3-D-012: low + review, no date
    if when.kind == "same_as_existing":
        return _from_existing(existing)

    d: date | None = None
    if when.kind == "absolute":
        try:
            d = date.fromisoformat(when.date) if when.date else None
        except ValueError:
            d = None
        if d is None:
            return Resolved(max_confidence="low", needs_review=True, reason="bad-date")
        d, ycap, yreason = _resolve_year(d, today, when, source_text)
        if yreason:
            conf, why, review = ycap, yreason, True
    elif when.kind == "relative":
        rel = when.relative
        if rel is None:
            return Resolved(max_confidence="low", needs_review=True, reason="no-relative")
        offset = timedelta(days=(rel.days or 0) + 7 * (rel.weeks or 0))
        if rel.anchor == "weekday":
            if rel.weekday is None:
                return Resolved(max_confidence="low", needs_review=True, reason="no-weekday")
            d, conf, why = _weekday_date(today, WEEKDAYS[rel.weekday], rel.qualifier, at, now_clock)
            if offset:  # "due one week after Wednesday's release": compound reading, never better than medium
                d, conf = d + offset, cap(conf, "medium")
        else:  # "posted" (today/tomorrow) and "offset" (in N days/weeks) are both relative to the posted day
            d, why = today + offset, rel.anchor

    # Times. A deadline with no time gets 23:59 (marked); anything else with no time stays date-only.
    if at is not None:
        return Resolved(d, at, end if end and end > at else None, True, False, conf, review, why)
    if is_deadline:
        return Resolved(d, DEADLINE_DEFAULT, None, False, True, conf, review, why + "+deadline-default")
    return Resolved(d, None, None, False, False, conf, review, why)


def _from_existing(existing: dict | None) -> Resolved:
    if not existing or existing.get("event_date") is None:
        return Resolved(reason="same-as-nothing")  # never invent
    t = _existing_time(existing)
    return Resolved(existing["event_date"], t, _existing_end(existing), bool(existing.get("time_known")),
                    bool(existing.get("time_defaulted")), "high", False, "same-as-existing")


def _existing_time(existing: dict) -> time | None:
    s = existing.get("starts_at")
    return s.astimezone(IST).time().replace(second=0, microsecond=0) if s else None


def _existing_end(existing: dict) -> time | None:
    e = existing.get("ends_at")
    return e.astimezone(IST).time().replace(second=0, microsecond=0) if e else None


def inherit_time(res: Resolved, existing: dict | None, phrase: str) -> Resolved:
    """"moved to Tuesday, same time": copy the existing event's STATED time, and only when the message says so.
    If the existing event has no time, the result has none (§3.3)."""
    if res.event_date is None or res.time_known or not existing or not _SAME_TIME.search(phrase or ""):
        return res
    if not existing.get("time_known") or existing.get("starts_at") is None:
        return replace(res, time=None, time_defaulted=False, reason=res.reason + "+same-time-but-none")
    return replace(res, time=_existing_time(existing), end_time=_existing_end(existing), time_known=True,
                   time_defaulted=False, reason=res.reason + "+same-time")


def wants_same_place(phrase: str) -> bool:
    return bool(_SAME_PLACE.search(phrase or ""))


def ist_date(dt: datetime) -> date:
    return dt.astimezone(IST).date()
