"""Match an extracted item to the course's open events (PHASE3 §5.2, P3-D-008): deterministic first, LLM only when ambiguous."""
from __future__ import annotations

import re
from dataclasses import dataclass
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


@dataclass
class Match:
    event: dict | None          # None = new event
    how: str                    # exact | subset | generic | tiebreak | new
    needs_review: bool = False


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
    """Group same-type open events by how well their title matches. Pure, so it is easy to test."""
    words, nums = tokens(title)
    groups: dict[str, list[dict]] = {"exact": [], "subset": [], "generic": []}
    for e in _open(events, posted_date, allow_cancelled_past):
        if e["type"] != item_type or e["id"] in claimed:
            continue
        if item_type in _RECURRING and new_date and e.get("event_date") and e["event_date"] != new_date:
            continue
        ew, en = tokens(e["title"])
        if not numbers_compatible(nums, en):
            continue
        if words == ew and nums == en:
            groups["exact"].append(e)
        elif words and ew and (words <= ew or ew <= words):
            groups["subset"].append(e)
        elif words <= _TYPE_WORDS:
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
            return await _ask(item, pool, tiebreak)
    return Match(None, "new")


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
