"""feed-build step (P4-D-108/109/127): turn messages, file events and event changes into student-app cards (feed_items).

Idempotent and resumable by design: each pass asks storage for sources that have no card yet (an anti-join) inside a
look-back window, applies the pure rules in skim/feed.py, and creates the cards. Nothing depends on a high-water mark, so
a message whose AI analysis finishes late still gets its card on a later pass. Card *content* is joined live when read."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta

from ..feed import (CHANGE_CARD_TYPES, document_qualifies, message_qualifies, modified_is_coalesced)
from ..storage.base import Storage
from .common import JobContext

log = logging.getLogger("skim.feed")
def _add(stats: Counter, key: str, n: int) -> None:
    if n:
        stats[key] += n


LIMIT = 5000  # per source kind and pass; a class team produces hundreds


async def build_feed(storage: Storage, staff_ids: frozenset[str], now: datetime, lookback_days: int | None,
                     stats: Counter, team_ids: set[str] | None = None) -> str:
    """One pass. lookback_days=None scans all history (the --backfill mode). Returns "ok" or "partial" (more to do)."""
    since = None if lookback_days is None else now - timedelta(days=lookback_days)
    partial = False

    def wanted(row: dict) -> bool:
        return team_ids is None or row["team_id"] in team_ids

    # 1. messages (first, so the file rule below can see which messages are cards)
    rows = [r for r in await storage.feed_candidate_messages(since, LIMIT) if wanted(r)]
    made = 0
    for r in rows:
        if not message_qualifies(r, staff_ids):
            stats["feed_messages_not_cards"] += 1
            continue
        await storage.upsert_feed_item({"kind": "message", "team_id": r["team_id"], "conversation_id": r["conversation_id"],
                                        "message_id": r["message_id"], "occurred_at": r["created_at"] or r["last_edited_at"]},
                                       list(r["event_ids"]))
        made += 1
    _add(stats, "feed_message_cards", made)
    partial = partial or (len(rows) >= LIMIT and made > 0)
    _add(stats, "feed_cards_synced_with_teams_deletes", await storage.sync_feed_hidden())

    # 2. file events
    rows = [r for r in await storage.feed_candidate_doc_events(since, LIMIT) if wanted(r)]
    made = 0
    new_modified: dict[int, list[datetime]] = {}
    for r in rows:  # chronological, so a "modified" burst is judged against the cards made earlier in this very pass
        if not document_qualifies(r, staff_ids):
            stats["feed_files_not_cards"] += 1
            continue
        if r["event"] == "created" and r["covered_by_message"]:
            stats["feed_files_on_message_cards"] += 1  # shown as a file chip on the message card (P4-D-109)
            continue
        if r["event"] == "modified":
            if modified_is_coalesced(r["at"], list(r["modified_card_times"]) + new_modified.get(r["document_id"], [])):
                stats["feed_files_coalesced"] += 1
                continue
            new_modified.setdefault(r["document_id"], []).append(r["at"])
        await storage.upsert_feed_item({"kind": "document", "team_id": r["team_id"],
                                        "document_event_id": r["document_event_id"], "occurred_at": r["at"]}, [])
        made += 1
    _add(stats, "feed_file_cards", made)
    partial = partial or (len(rows) >= LIMIT and made > 0)

    # 3. event changes
    rows = [r for r in await storage.feed_candidate_changes(since, LIMIT, CHANGE_CARD_TYPES) if wanted(r)]
    for r in rows:
        await storage.upsert_feed_item({"kind": "change", "team_id": r["team_id"], "event_change_id": r["event_change_id"],
                                        "occurred_at": r["at"]}, [r["event_id"]])
    _add(stats, "feed_change_cards", len(rows))
    partial = partial or len(rows) >= LIMIT

    # 4. reconcile: a late message card hides the file's duplicate card; links appear once analyses exist (or after a reset)
    _add(stats, "feed_duplicate_file_cards_folded", await storage.hide_duplicate_document_cards())
    _add(stats, "feed_event_links_added", await storage.link_feed_items_to_events())
    return "partial" if partial else "ok"


async def feed_build(ctx: JobContext, team_id: str | None, team_ids: set[str] | None = None) -> str:
    return await build_feed(ctx.storage, ctx.options.get("staff_aad_ids", frozenset()), ctx.clock(),
                            ctx.options.get("feed_lookback_days", 30), ctx.stats, team_ids)
