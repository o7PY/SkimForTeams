"""Per-user settings (PHASE4.md section 5): defaults, validation of a partial update, and merging.
Stored sparse (only what the user changed); the effective settings are DEFAULTS merged with the stored overrides."""
from __future__ import annotations

import copy
import re
from typing import Any

EVENT_TYPES = ["quiz", "exam", "assignment", "project", "lab", "class", "office_hours", "presentation", "other"]
TIMEZONE = "Asia/Kolkata"  # P4-D-133: the only supported value in Phase 4

DEFAULTS: dict[str, Any] = {
    "timezone": TIMEZONE,
    "history_limit": 50,
    "quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00"},
    "urgent_only": False,
    "new_card_alerts": True,   # a push when a new post or file arrives (P5-D-13); mute and quiet hours still apply
    "important_types": ["quiz", "exam", "assignment"],
    "reminders": {"day_before_time": "20:00", "same_day_hours": 5, "earliest": "07:00", "auto_for_important": True},
}

_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def effective(stored: dict | None) -> dict:
    return deep_merge(DEFAULTS, stored or {})


def _bool(v) -> bool:
    if not isinstance(v, bool):
        raise ValueError("must be true or false")
    return v


def _hhmm(v) -> str:
    if not isinstance(v, str) or not _HHMM.fullmatch(v):
        raise ValueError("must be a time like 20:00")
    return v


def _int_between(lo: int, hi: int):
    def check(v) -> int:
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"must be a whole number from {lo} to {hi}")
        return v
    return check


def _hours(v) -> int | float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.5 <= v <= 24 or (v * 2) % 1:
        raise ValueError("must be from 0.5 to 24 hours, in steps of 0.5")
    return int(v) if v == int(v) else float(v)


def _timezone(v) -> str:
    if v != TIMEZONE:
        raise ValueError(f"only {TIMEZONE} is supported")
    return v


def _types(v) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ValueError("must be a list of event types")
    bad = sorted(set(v) - set(EVENT_TYPES))
    if bad:
        raise ValueError(f"unknown event type(s): {', '.join(bad)}")
    return [t for t in EVENT_TYPES if t in v]  # canonical order, no duplicates


SCHEMA: dict[str, Any] = {
    "timezone": _timezone,
    "history_limit": _int_between(10, 500),
    "quiet_hours": {"enabled": _bool, "start": _hhmm, "end": _hhmm},
    "urgent_only": _bool,
    "new_card_alerts": _bool,
    "important_types": _types,
    "reminders": {"day_before_time": _hhmm, "same_day_hours": _hours, "earliest": _hhmm, "auto_for_important": _bool},
}


def validate_patch(patch: Any) -> tuple[dict, list[str]]:
    """A partial update -> (clean partial, problems). Unknown keys and bad values are problems; nothing is applied
    unless there are none."""
    problems: list[str] = []

    def walk(p: Any, schema: dict, path: str) -> dict:
        out: dict = {}
        if not isinstance(p, dict):
            problems.append(f"{path or 'settings'}: must be an object")
            return out
        for k, v in p.items():
            where = f"{path}{k}"
            rule = schema.get(k)
            if rule is None:
                problems.append(f"{where}: unknown setting")
            elif isinstance(rule, dict):
                sub = walk(v, rule, where + ".")
                if sub:
                    out[k] = sub
            else:
                try:
                    out[k] = rule(v)
                except ValueError as e:
                    problems.append(f"{where}: {e}")
        return out

    clean = walk(patch, SCHEMA, "")
    return ({} if problems else clean), problems
