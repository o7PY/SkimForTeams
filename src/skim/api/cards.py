"""Card payloads (PHASE4.md section 6.3), built from rows the storage layer fetched. One shape for deck, detail, history and
starred. The original Teams content is always in the card; AI only adds the event chips and the change badge."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from ..eventtime import IST, event_end, is_over, is_upcoming
from .. import reminders as rem_engine
from ..feed import author_role, document_actor
from ..storage.base import Storage
from .deeplink import teams_message_link

PREVIEW_CHARS = 280


def iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(IST).isoformat()
    return v.isoformat()  # a date


def _preview(text: str | None, detail: bool) -> tuple[str, bool]:
    text = text or ""
    if detail or len(text) <= PREVIEW_CHARS:
        return text, False
    return text[:PREVIEW_CHARS].rstrip() + "…", True


def event_chip(e: dict, important_types: set[str], detail: bool = False) -> dict:
    start = e.get("starts_at") or e.get("event_date")
    chip = {"id": e["id"], "title": e["title"], "type": e["type"], "start": iso(start), "end": iso(e.get("ends_at")),
            "all_day": not (e.get("time_known") or e.get("time_defaulted")), "venue": e.get("venue"), "status": e["status"],
            "unconfirmed": bool(e.get("needs_review")) or e.get("confidence") == "low",
            "important": e["type"] in important_types}
    if detail:
        chip["details"] = e.get("details")
        chip["is_deadline"] = bool(e.get("is_deadline"))
    return chip


def change_side(v: dict | None) -> dict | None:
    """old/new values of a change as the app shows them: start (date, or date+time in IST), venue, status, details."""
    if not v:
        return None
    out: dict[str, Any] = {k: v[k] for k in ("venue", "status", "details", "title") if k in v}
    d, t = v.get("date"), v.get("time")
    if d:
        out["start"] = f"{d}T{t}:00+05:30" if t else d
    return out or None


def star_active(star: dict, events: list[dict], now: datetime) -> bool:
    """P4-D-114, applied at read time: a temp star lapses by its clock, or when every dated event on the card is over."""
    if star.get("expires_at") is not None and star["expires_at"] <= now:
        return False
    if star.get("until_event_over"):
        dated = [e for e in events if event_end(e) is not None]
        return not dated or not all(is_over(e, now) for e in dated)
    return True


def star_expires_at(star: dict, events: list[dict]) -> datetime | None:
    if star.get("expires_at") is not None:
        return star["expires_at"]
    if star.get("until_event_over"):
        ends = [event_end(e) for e in events if event_end(e) is not None]
        return max(ends) if ends else None
    return None


@dataclass
class Loaded:
    """Everything needed to render a set of items, fetched in a handful of batched queries."""
    items: dict[int, dict]
    events: dict[int, dict]
    messages: dict[tuple[str, str], dict]
    files: dict[tuple[str, str], list[dict]]
    doc_cards: dict[int, dict]
    changes: dict[int, dict]
    teams: dict[str, dict]
    channels: dict[str, str]
    stars: dict[int, dict]
    states: dict[int, dict]
    scheduled: list[rem_engine.Scheduled]
    rem: dict
    history: dict[int, list[dict]] = field(default_factory=dict)


async def load(storage: Storage, user_id: str, item_ids: list[int], detail: bool, settings: dict, now: datetime) -> Loaded:
    items = await storage.get_feed_items(item_ids)
    event_ids = sorted({e for it in items.values() for e in it["event_ids"]})
    change_ids = [it["event_change_id"] for it in items.values() if it["kind"] == "change"]
    changes = await storage.get_event_changes_by_ids(change_ids) if change_ids else {}
    pairs = {(it["conversation_id"], it["message_id"]) for it in items.values() if it["kind"] == "message"}
    for ch in changes.values():  # a change made by a message links to it (Open in Teams)
        ref = ch.get("source_ref") or {}
        if ch.get("source_kind") == "message" and ref.get("conversation_id") and ref.get("message_id"):
            pairs.add((ref["conversation_id"], ref["message_id"]))
    messages = await storage.get_messages(sorted(pairs)) if pairs else {}
    parents = {(c, m["reply_to_id"]) for (c, _), m in messages.items() if m.get("reply_to_id")} - set(messages)
    if parents:
        messages |= await storage.get_messages(sorted(parents))
    events = await storage.get_events_by_ids(event_ids) if event_ids else {}
    de_ids = [it["document_event_id"] for it in items.values() if it["kind"] == "document"]
    team_ids = {it["team_id"] for it in items.values()}
    teams = {t["id"]: t for t in await storage.list_teams() if t["id"] in team_ids}
    channels: dict[str, str] = {}
    for tid in team_ids:
        for ch in await storage.list_channels(tid):
            channels[ch["id"]] = ch.get("display_name") or ""
    # The user's reminders as the engine sees them now (real + automatic), for the whole card set at once
    user_events = await storage.list_events_for_user(user_id, (now - timedelta(days=2)).astimezone(IST).date())
    rows = await storage.list_reminders(user_id, include_deleted=True)
    newest = await storage.latest_items_for_events(user_id, list(user_events)) if user_events else {}
    scheduled = rem_engine.compute(user_id, rows, user_events, settings, now, item_for_event=newest.get, days=365, cap=10_000)
    return Loaded(
        items=items, events=events | {i: e for i, e in user_events.items() if i in event_ids}, messages=messages,
        files=await storage.get_message_files(sorted(pairs)) if pairs else {},
        doc_cards=await storage.get_document_cards(de_ids) if de_ids else {},
        changes=changes, teams=teams, channels=channels,
        stars=await storage.get_stars(user_id, item_ids), states=await storage.get_item_states(user_id, item_ids),
        scheduled=scheduled, rem=settings["reminders"],
        history=await storage.list_event_history(event_ids) if detail and event_ids else {})


def _author(row: dict, role: str, name: str | None) -> dict:
    return {"name": name, "is_staff": role == "staff"}


def build_card(ld: Loaded, item_id: int, *, now: datetime, staff_ids: frozenset[str], important: set[str],
               folded_ids: list[int] | None = None, detail: bool = False) -> dict | None:
    it = ld.items.get(item_id)
    if it is None:
        return None
    team = ld.teams.get(it["team_id"]) or {"id": it["team_id"]}
    evs = [ld.events[e] for e in it["event_ids"] if e in ld.events]
    card: dict[str, Any] = {
        "id": item_id, "kind": it["kind"],
        "team": {"id": team["id"], "name": team.get("name"), "ended": team.get("removed_at") is not None},
        "occurred_at": iso(it["occurred_at"]), "folded_ids": list(folded_ids or []),
        "author": None, "message": None, "document": None, "change": None,
        "events": [event_chip(e, important, detail) for e in evs], "links": {"teams_deep_link": None, "file_url": None}}

    def deep_link(cid: str, mid: str) -> str | None:
        m = ld.messages.get((cid, mid))
        return teams_message_link(team, ld.channels.get(cid), cid, mid, m.get("reply_to_id") if m else None)

    if it["kind"] == "message":
        key = (it["conversation_id"], it["message_id"])
        m = ld.messages.get(key) or {}
        gone = m.get("deleted_at") is not None or bool(it["hidden"])   # deleted in Teams: nothing of its content is served
        text, truncated = ("", False) if gone else _preview(m.get("text"), detail)
        parent = ld.messages.get((key[0], m["reply_to_id"])) if m.get("reply_to_id") else None
        if parent is not None and parent.get("deleted_at") is not None:
            parent = None                                                # ... nor quoted through a reply
        reply_subject = (parent.get("subject") or (parent.get("text") or "")[:60]) if parent else None
        card["author"] = _author(m, author_role(m.get("from_aad_id"), team.get("installed_by_aad"), staff_ids), m.get("from_name"))
        card["message"] = {
            "subject": None if gone else m.get("subject"), "text": text, "text_truncated": truncated,
            "edited": bool(m.get("last_edited_at")) or (m.get("current_version") or 1) > 1,
            "deleted": gone, "reply_to_subject": reply_subject or None,
            "attachments": [] if gone else [{"name": f["name"] or f.get("document_name"), "document_id": f["document_id"],
                                            "web_url": f["document_url"] or f["content_url"]} for f in ld.files.get(key, [])]}
        card["links"]["teams_deep_link"] = None if gone else deep_link(*key)
    elif it["kind"] == "document":
        d = ld.doc_cards.get(it["document_event_id"]) or {}
        who = document_actor(d) if d else None
        card["author"] = _author(d, author_role(who, d.get("installed_by_aad"), staff_ids),
                                 (d.get("modified_by_name") if d.get("modified_by_aad") or d.get("modified_by_name") else d.get("created_by_name")))
        card["document"] = {"name": d.get("new_name") or d.get("name"), "action": d.get("event"), "old_name": d.get("old_name"),
                            "old_path": d.get("old_path"), "web_url": d.get("web_url")}
        card["links"]["file_url"] = d.get("web_url")
    else:
        ch = ld.changes.get(it["event_change_id"]) or {}
        card["change"] = {"type": ch.get("change_type"), "before": change_side(ch.get("old_values")),
                          "after": change_side(ch.get("new_values"))}
        ref = ch.get("source_ref") or {}
        if ch.get("source_kind") == "message" and ref.get("conversation_id") and ref.get("message_id"):
            card["links"]["teams_deep_link"] = deep_link(ref["conversation_id"], ref["message_id"])

    star = ld.stars.get(item_id)
    st = ld.states.get(item_id)
    card["user"] = {
        "starred": bool(star and star_active(star, evs, now)),
        "temp_star_expires_at": iso(star_expires_at(star, evs)) if star and star_active(star, evs, now) else None,
        "reminders": [{"id": r.id, "kind": r.kind, "fire_at": iso(r.fire_at), "virtual": r.virtual}
                      for r in ld.scheduled if r.item_id == item_id or r.event_id in it["event_ids"]],
        "snoozed_until": iso(st["snoozed_until"]) if st and st["snoozed_until"] and st["snoozed_until"] > now else None}
    if detail:
        card["recommended_reminders"] = recommended_preview(evs, ld.rem, now)
        card["event_history"] = [
            {"event_id": e["id"], "changes": [{"id": c["id"], "type": c["change_type"], "at": iso(c["at"]),
                                               "before": change_side(c.get("old_values")), "after": change_side(c.get("new_values"))}
                                              for c in ld.history.get(e["id"], [])]} for e in evs]
        card["folded"] = [c for c in (build_card(ld, f, now=now, staff_ids=staff_ids, important=important, detail=True)
                                      for f in (folded_ids or [])) if c is not None]
    return card


def recommended_preview(evs: list[dict], rem: dict, now: datetime) -> list[dict]:
    """What 'Recommended reminders' would add for the card's first upcoming dated event, both times shown before adding (P4-D-116).
    Empty when the card has no such event."""
    for e in evs:
        if is_upcoming(e, now) and rem_engine.event_day(e) is not None:
            t = rem_engine.recommended_times(e, rem)
            return [{"kind": k, "event_id": e["id"], "fire_at": iso(t[rule]) if t[rule] and t[rule] > now else None}
                    for k, rule in (("recommended_day_before", rem_engine.DAY_BEFORE), ("recommended_same_day", rem_engine.SAME_DAY))]
    return []
