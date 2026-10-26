"""When an event starts, ends and is over (pure; Asia/Kolkata is the calendar, P4-D-133). Shared by the deck backlog rule
(P4-D-112), temp stars (P4-D-114), reminders (P4-D-116/119) and change alerts (P4-D-122)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_LENGTH = timedelta(hours=1)  # an event with a start but no end is over one hour after it starts
DEAD_STATUSES = frozenset({"cancelled", "done"})


def event_end(e: dict) -> datetime | None:
    """The moment the event is over, or None if it has no date at all. A date without a time is over when that IST day ends."""
    if e.get("starts_at"):
        return e.get("ends_at") or e["starts_at"] + DEFAULT_LENGTH
    d: date | None = e.get("event_date")
    if d:
        return datetime.combine(d + timedelta(days=1), time.min, tzinfo=IST).astimezone(timezone.utc)
    return None


def is_over(e: dict, now: datetime) -> bool:
    end = event_end(e)
    return end is not None and now >= end


def is_upcoming(e: dict, now: datetime) -> bool:
    """Dated, not over, and neither cancelled nor done."""
    end = event_end(e)
    return end is not None and now < end and e.get("status") not in DEAD_STATUSES
