"""The deck (P4-D-107/110/112/115), pure: which candidate cards are dealt, which fold into a change card, the order, paging."""
from __future__ import annotations

import base64
import json
from datetime import datetime

from ..eventtime import is_upcoming

BACKLOG_DAYS = 7  # P4-D-112
EPOCH_US = 0


def _us(dt: datetime | None) -> int:
    return int(dt.timestamp() * 1_000_000) if dt else EPOCH_US


def key(c: dict) -> tuple:
    """Age order for folding: time of the thing in Teams; at the same time a change card is newer than the message that
    caused it; then card id."""
    return (c["occurred_at"], 1 if c["kind"] == "change" else 0, c["id"])


def eligible(cands: list[dict], events: dict[int, dict], now: datetime) -> list[dict]:
    """D-112: a card is dealt if it was put back / came back from a snooze, or happened in the 7 days before the user
    joined (or after), or is linked to a still-upcoming event. Older things are never dealt."""
    return [c for c in cands
            if c["returned"] or c["in_window"] or any(is_upcoming(events[e], now) for e in c["event_ids"] if e in events)]


def fold(cards: list[dict]) -> tuple[list[dict], dict[int, list[int]]]:
    """D-110: older cards linked to an event are folded into the newest change card for that event. Returns the cards that
    stay in the deck (input order kept) and {change card id: [folded card ids, newest first]}."""
    newest_change: dict[int, dict] = {}
    for c in cards:
        if c["kind"] == "change":
            for e in c["event_ids"]:
                if e not in newest_change or key(c) > key(newest_change[e]):
                    newest_change[e] = c
    target: dict[int, dict] = {}
    for c in cards:
        best = None
        for e in c["event_ids"]:
            n = newest_change.get(e)
            if n is not None and n["id"] != c["id"] and key(n) > key(c) and (best is None or key(n) > key(best)):
                best = n
        if best is not None:
            target[c["id"]] = best
    by_id = {c["id"]: c for c in cards}
    folded: dict[int, list[dict]] = {}
    for cid in target:
        t = target[cid]
        while t["id"] in target:  # a change card folded into a newer one: follow to the final card (keys only increase)
            t = target[t["id"]]
        folded.setdefault(t["id"], []).append(by_id[cid])
    stay = [c for c in cards if c["id"] not in target]
    return stay, {t: [c["id"] for c in sorted(v, key=key, reverse=True)] for t, v in folded.items()}


def sort_key(c: dict) -> tuple:
    """The deck order: returned/put-back cards first (latest return first), then newest first."""
    return (0 if c["returned"] else 1, -_us(c.get("snoozed_until")), -_us(c["occurred_at"]), -c["id"])


def encode_cursor(c: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(sort_key(c))).encode()).decode().rstrip("=")


def decode_cursor(raw: str) -> tuple | None:
    try:
        v = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return tuple(v) if isinstance(v, list) and len(v) == 4 and all(isinstance(x, int) for x in v) else None
    except Exception:
        return None


def page(cards: list[dict], limit: int, cursor: tuple | None) -> tuple[list[dict], str | None]:
    ordered = sorted(cards, key=sort_key)
    if cursor is not None:
        ordered = [c for c in ordered if sort_key(c) > cursor]
    out = ordered[:limit]
    return out, (encode_cursor(out[-1]) if len(ordered) > limit and out else None)


def build_deck(cands: list[dict], events: dict[int, dict], now: datetime) -> tuple[list[dict], dict[int, list[int]]]:
    """Eligible, folded cards in deck order, and the fold map."""
    stay, folded = fold(eligible(cands, events, now))
    return sorted(stay, key=sort_key), folded
