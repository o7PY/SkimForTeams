"""What becomes a student-app card (PHASE4.md P4-D-108/109, pure rules; no I/O). The job in jobs/feed_build.py applies them."""
from __future__ import annotations

from datetime import datetime, timedelta

# Event-change types that make a card; created / restated / needs_review never do.
CHANGE_CARD_TYPES = ("rescheduled", "cancelled", "uncancelled", "venue_changed", "time_set", "date_set", "details_changed")
MESSAGE_CATEGORIES = frozenset({"event", "change", "announcement", "resource"})
MODIFIED_WINDOW = timedelta(hours=6)  # at most one "modified" card per file per window
CARD_DOCUMENT_EVENTS = frozenset({"created", "renamed", "moved", "deleted", "restored", "modified"})


def author_role(who: str | None, installed_by: str | None, staff_ids: frozenset[str]) -> str:
    """staff | student | unknown, the same rule as Phase 3 (pipeline.author_role): the installer and STAFF_AAD_IDS are
    staff; with an allow-list configured everyone else is a student, without one they are unknown."""
    if who and (who in staff_ids or who == installed_by):
        return "staff"
    return "student" if staff_ids else "unknown"


def message_qualifies(row: dict, staff_ids: frozenset[str]) -> bool:
    """(a) staff-authored, or the AI called it event / change / announcement / resource. Bot and system messages (no
    author id) never do; student chitchat and questions never do."""
    if not row.get("from_aad_id"):
        return False
    if author_role(row["from_aad_id"], row.get("installed_by_aad"), staff_ids) == "staff":
        return True
    return row.get("category") in MESSAGE_CATEGORIES


def document_actor(row: dict) -> str | None:
    """Phase 3's rule for a file's author (P3-D-019): the last modifier; the creator only if nobody else edited it."""
    edited = bool(row.get("modified_by_aad") or row.get("modified_by_name"))
    return row.get("modified_by_aad") if edited else row.get("created_by_aad")


def document_qualifies(row: dict, staff_ids: frozenset[str]) -> bool:
    """(b) a file event on a staff-authored or unknown-author file; never a student's."""
    if row["event"] not in CARD_DOCUMENT_EVENTS:
        return False
    return author_role(document_actor(row), row.get("installed_by_aad"), staff_ids) != "student"


def modified_is_coalesced(at: datetime, earlier_card_times: list[datetime]) -> bool:
    """A modified event gets no card if one of this file's modified cards sits within the 6 h before it."""
    return any(timedelta(0) <= at - t < MODIFIED_WINDOW for t in earlier_card_times)
