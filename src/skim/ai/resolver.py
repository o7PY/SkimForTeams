"""Match an extracted item to the course's open events (PHASE3 §5.2, P3-D-008): deterministic first, LLM only when ambiguous."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable

_STOP = {"the", "a", "an", "of", "for", "on", "session", "day", "part", "new", "extra"}
# Words that only restate the event type: a title made of these ("the lab", "Quiz") does not say WHICH one.
_TYPE_WORDS = {"quiz", "exam", "midsem", "endsem", "final", "test", "assignment", "project", "lab", "lecture", "class",
               "office", "hour", "presentation", "homework", "deadline", "submission", "seminar"}
_RECURRING = {"class", "office_hours"}  # the same title recurs weekly, so a different date is a different event
MAX_TIEBREAK = 5


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def tokens(title: str) -> tuple[frozenset[str], frozenset[str]]:
    """(words, numbers): "Quiz-2" == "quiz 2", "the Midsem" == "midsem"; "Lab session (Problem Set 3)" -> {lab, problem, set}, {3}."""
    parts = re.findall(r"[a-z]+|\d+", (title or "").lower())
    words = frozenset(_stem(p) for p in parts if p.isalpha() and p not in _STOP)
    return words, frozenset(p for p in parts if p.isdigit())


def numbers_compatible(a: frozenset[str], b: frozenset[str]) -> bool:
    return not a or not b or a == b


def identifiers(words: frozenset[str], nums: frozenset[str]) -> frozenset[str]:
    """What makes a title SPECIFIC: its numbers and any word that is not just the event type ("Quiz 2" -> {2},
    "Lab session (Problem Set 3)" -> {problem, set, 3}, "Quiz" / "the lab" / "Office hours" -> {})."""
    return frozenset((words - _TYPE_WORDS) | nums)


# P3-DIFF-020: only EXPLICIT change language may move an event. A reminder / restatement / venue / details item never
# overwrites a date or time; it may only fill in one that is missing, and a conflict is flagged instead of applied.
MOVING_ACTIONS = frozenset({"reschedule"})
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def may_move(action: str) -> bool:
    return action in MOVING_ACTIONS


def weekday_agrees(when, event_date: date | None) -> bool:
    """A bare weekday ("Reminder: the quiz is on Monday") that names the weekday the event already falls on is the SAME day,
    even when the next such weekday from the posted date is earlier (posted Sunday/Monday, event the Monday after)."""
    rel = getattr(when, "relative", None)
    return bool(event_date and getattr(when, "kind", None) == "relative" and rel is not None and rel.anchor == "weekday"
                and rel.weekday and _WEEKDAYS[rel.weekday] == event_date.weekday())


@dataclass
class Match:
    event: dict | None          # None = new event
    how: str                    # exact | subset | generic | tiebreak | new
    needs_review: bool = False
    generic_events: list[dict] = field(default_factory=list)  # open GENERIC events a new specific event was NOT merged into


def _open(events: list[dict], posted_date: date, allow_cancelled_past: bool) -> list[dict]:
    out = []
    for e in events:
        if e["status"] == "done":
            continue
        if e["status"] == "cancelled" and not allow_cancelled_past and e.get("event_date") and e["event_date"] < posted_date:
            continue  # a cancelled event in the past is history, not something a new message refers to
        out.append(e)
    return out


def candidates_for(item_type: str, title: str, events: list[dict], claimed: set[int], posted_date: date,
                   new_date: date | None, allow_cancelled_past: bool = False) -> dict[str, list[dict]]:
    """Group same-type open events by how well their title matches. Pure, so it is easy to test.

    P3-DIFF-017: titles with DIFFERENT identifiers never merge. "Quiz 2" is not "Quiz 1" and not a generic "Quiz" (or
    "Assignment 4" / "Assignment"): that is reported in `generic_events` so the caller can create the specific event and flag
    the generic one. Only an item with NO identifier ("the quiz", "the lab") may weakly match a more specific event."""
    words, nums = tokens(title)
    ids = identifiers(words, nums)
    groups: dict[str, list[dict]] = {"exact": [], "subset": [], "generic": [], "generic_events": []}
    for e in _open(events, posted_date, allow_cancelled_past):
        if e["type"] != item_type or e["id"] in claimed:
            continue
        if item_type in _RECURRING and new_date and e.get("event_date") and e["event_date"] != new_date:
            continue
        ew, en = tokens(e["title"])
        eids = identifiers(ew, en)
        if words == ew and nums == en:
            groups["exact"].append(e)
        elif ids and eids:
            if numbers_compatible(nums, en) and (ids <= eids or eids <= ids):
                groups["subset"].append(e)      # e.g. "Lab Problem Set 3" ~ "Lab session (Problem Set 3)"
        elif ids:                               # specific item, generic event: do NOT merge
            if e["status"] != "cancelled":
                groups["generic_events"].append(e)
        elif words <= _TYPE_WORDS:              # generic item ("the quiz"): weak match against anything of that type
            groups["generic"].append(e)
    return groups


Tiebreak = Callable[[dict, list[dict]], Awaitable[str | None]]


async def find_match(item: dict, events: list[dict], claimed: set[int], posted_date: date, new_date: date | None,
                     tiebreak: Tiebreak) -> Match:
    """item: {type, title, action, refers_to_existing}. Returns the matched event or a new-event decision."""
    action, refers = item["action"], item["refers_to_existing"]
    groups = candidates_for(item["type"], item["title"], events, claimed, posted_date, new_date,
                            allow_cancelled_past=action == "uncancel")
    wants_existing = refers or action != "create"
    if groups["exact"]:
        return Match(groups["exact"][0], "exact")
    for level in ("subset", "generic"):
        pool = groups[level]
        if not pool:
            continue
        if level == "generic" and not wants_existing:
            continue  # "Quiz" announced as new while a quiz exists: not enough to say it is the same one
        if len(pool) == 1:
            return Match(pool[0], level)
        if wants_existing:
            m = await _ask(item, pool, tiebreak)
            m.generic_events = groups["generic_events"] if m.event is None else []
            return m
    return Match(None, "new", generic_events=groups["generic_events"])


async def _ask(item: dict, pool: list[dict], tiebreak: Tiebreak) -> Match:
    shortlist = sorted(pool, key=lambda e: (e["updated_at"], e["id"]), reverse=True)[:MAX_TIEBREAK]
    try:
        answer = await tiebreak(item, shortlist)
    except Exception:  # LLM trouble must not lose the item: make it a new event and flag it
        return Match(None, "new", needs_review=True)
    for e in shortlist:
        if str(e["id"]) == str(answer):
            return Match(e, "tiebreak")
    return Match(None, "new", needs_review=answer is None)
