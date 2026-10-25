"""Per-message pipeline (FR-303..305): redact -> classify+extract -> normalise dates -> resolve -> persist in ONE transaction.

LLM output is untrusted data (§0.3): it is schema-validated and only ever turned into typed values by this module;
it is never executed or used to build SQL, URLs or paths.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from ..textclean import clean_text
from .config import AIConfig
from .prompts import load
from .providers.router import AllProvidersFailed, Router
from .redact import Redactor
from .resolver import Match, find_match, may_move, weekday_agrees
from .schemas import Item, MessageExtraction, ResolveChoice
from .timeparse import IST, Resolved, cap, inherit_time, resolve_when, wants_same_place

log = logging.getLogger("skim.ai")
DEADLINE_TYPES = {"assignment", "project"}
_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass
class Outcome:
    status: str                       # ok | failed | skipped
    category: str | None = None
    role: str = "unknown"
    created: int = 0
    changed: int = 0
    restated: int = 0
    ignored_items: int = 0
    error_class: str | None = None


def author_role(msg: dict, cfg: AIConfig) -> str:
    """staff | student | unknown (P3-D-019 / P3-D-023). The installer and STAFF_AAD_IDS are staff; when an allow-list is
    configured everyone else is a student; without one the role of other authors is unknown (treated as staff, capped medium)."""
    if msg.get("author_role") in ("staff", "student"):
        return msg["author_role"]  # eval harness / future roster
    who = msg.get("from_aad_id")
    if who and (who in cfg.staff_aad_ids or who == msg.get("installed_by_aad")):
        return "staff"
    return "student" if cfg.staff_aad_ids else "unknown"


def _names(msg: dict) -> list[str]:
    names = [msg.get("from_name") or "", msg.get("parent_from_name") or ""]  # the replied-to author is quoted in <parent>

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for k in ("name", "displayName", "text"):
                if isinstance(x.get(k), str):
                    names.append(re.sub(r"</?at>", "", x[k]))
            for v in x.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    mentions = msg.get("mentions")
    walk(json.loads(mentions) if isinstance(mentions, str) else mentions)
    return [n for n in names if n]



_OUR_TAGS = re.compile(r"<(/?)(message|parent|subject|document|author_role|posted_ist|item|candidates)\b", re.I)


def _data(text: str | None) -> str:
    """Untrusted text can neither close nor imitate our wrapper tags (a zero-width space breaks them up)."""
    return _OUR_TAGS.sub(lambda m: "<\u200b" + m.group(1) + m.group(2), clean_text(text or "").replace("</", "<\u200b/"))


def _posted(msg: dict, fallback: datetime) -> tuple[datetime, bool]:
    for k in ("created_at", "last_edited_at"):
        if msg.get(k):
            return msg[k], k == "created_at"
    return fallback, False


def build_prompt(msg: dict, role: str, red: Redactor, posted: datetime) -> str:
    p = posted.astimezone(IST)
    parts = [f"<posted_ist>{p.strftime('%A, %Y-%m-%d %H:%M')} IST</posted_ist>", f"<author_role>{role}</author_role>"]
    if msg.get("parent_text"):
        parts.append(f"<parent>{_data(red.redact(msg['parent_text']))}</parent>")
    if msg.get("subject"):
        parts.append(f"<subject>{_data(red.redact(msg['subject']))}</subject>")
    parts.append(f"<message>{_data(red.redact(msg.get('text')))}</message>")
    return "\n".join(parts)


def build_doc_prompt(doc: dict, chunk_text: str, red: Redactor, posted: datetime) -> str:
    p = posted.astimezone(IST)
    return "\n".join([f"<posted_ist>{p.strftime('%A, %Y-%m-%d %H:%M')} IST</posted_ist>",
                      "<author_role>unknown</author_role>", f"<document>{_data(red.redact(chunk_text))}</document>"])


def _snapshot(e: dict) -> dict:
    return {"date": e["event_date"].isoformat() if e.get("event_date") else None,
            "time": e["starts_at"].astimezone(IST).strftime("%H:%M") if e.get("starts_at") else None,
            "venue": e.get("venue"), "status": e["status"], "title": e["title"]}


def _fmt_event(e: dict, red: Redactor) -> str:
    """One candidate line for the tie-break prompt. Free text (the title) goes through the message's redactor so author and
    mentioned names never leak; the venue is not needed to tell events apart and is left out."""
    s = _snapshot(e)
    return f"{e['id']} | {e['type']} | {red.redact(e['title'])} | {s['date'] or 'no date'} {s['time'] or ''} | {e['status']}"


@dataclass
class Pipeline:
    storage: Any
    router: Router
    cfg: AIConfig
    clock: Any = lambda: datetime.now(timezone.utc)
    prompt_version: str = ""
    stats: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.prompt_version = self.prompt_version or self.cfg.prompt_version
        self._system = load("classify_extract")

    # ---------------------------------------------------------------- public
    async def process(self, msg: dict, apply_events: bool = True) -> Outcome:
        cid, mid, version = msg["conversation_id"], msg["message_id"], msg["version"]
        role = author_role(msg, self.cfg)
        posted, _ = _posted(msg, self.clock())
        edited = version > 1 and msg.get("last_edited_at")
        at = msg["last_edited_at"] if edited else posted
        source = {"kind": "message", "team_id": msg["team_id"], "at": at,
                  "ref": {"conversation_id": cid, "message_id": mid}}
        base = {"conversation_id": cid, "message_id": mid, "message_version": version, "prompt_version": self.prompt_version,
                "author_role": role}
        red = Redactor(_names(msg))
        try:
            comp = await self.router.complete(MessageExtraction, self._system, build_prompt(msg, role, red, posted))
        except AllProvidersFailed as e:
            await self.storage.commit_message_analysis({**base, "status": "failed", "error_class": e.error_class}, [], source)
            return Outcome("failed", role=role, error_class=e.error_class)
        ex: MessageExtraction = comp.value
        analysis = {**base, "status": "ok", "category": ex.category, "confidence": ex.confidence,
                    "provider": comp.provider, "model": comp.model, "output": ex.model_dump()}
        out = Outcome("ok", ex.category, role)
        items = ex.items if ex.category in ("event", "change") else []
        if role == "student" or not apply_events:  # P3-D-019: students never act; re-processing only records analysis
            out.ignored_items = len(items) if role == "student" else 0
            await self.storage.commit_message_analysis(analysis, [], source)
            return out

        ops = await self._plan(msg, items, ex, role, posted, at, red, out, analysis, source)
        await self.storage.commit_message_analysis(analysis, ops, source)
        return out

    async def process_document_chunk(self, doc: dict, chunk: dict) -> Outcome:
        """One document chunk -> events (FR-306). Same extraction schema and the same resolver as messages; the author is
        unknown (confidence capped at medium) and the LLM output is not stored (the chunk text is the record)."""
        posted = doc.get("modified_at") or doc.get("created_at") or self.clock()
        source = {"kind": "document", "team_id": doc["team_id"], "at": posted,
                  "ref": {"document_id": doc["id"], "chunk_index": chunk["chunk_index"]}}
        # P3-D-019 applies to files too. The role is that of the LAST MODIFIER. The creator is used only for a file that has
        # never been modified by anyone else; a modifier we cannot identify gives no id (student if a staff list is set,
        # else unknown), never the creator's role (a student may have co-edited a staff file).
        edited = bool(doc.get("modified_by_aad") or doc.get("modified_by_name"))
        who = doc.get("modified_by_aad") if edited else doc.get("created_by_aad")
        role = author_role({"from_aad_id": who, "installed_by_aad": doc.get("installed_by_aad")}, self.cfg)
        if role == "student":
            return Outcome("ok", None, role)  # students never create or change events, in chat or in files
        plan_role = "unknown"  # files can be co-edited: even staff documents are capped at medium confidence
        red = Redactor([n for n in (doc.get("created_by_name"), doc.get("modified_by_name")) if n])
        system = load("classify_extract") + load("document_extract")
        try:
            comp = await self.router.complete(MessageExtraction, system, build_doc_prompt(doc, chunk["text"], red, posted))
        except AllProvidersFailed as e:
            return Outcome("failed", role="unknown", error_class=e.error_class)
        ex: MessageExtraction = comp.value
        out = Outcome("ok", ex.category, role)
        items = ex.items if ex.category in ("event", "change") else []
        if not items:
            return out
        ops = await self._plan({"team_id": doc["team_id"], "version": 1, "text": chunk["text"]}, items, ex, plan_role,
                               posted, posted, red, out, {}, source)
        if ops:
            await self.storage.commit_message_analysis(None, ops, source)
        return out

    # ---------------------------------------------------------------- planning
    async def _plan(self, msg, items, ex, role, posted, at, red, out, analysis, source) -> list[dict]:
        events = [dict(e) for e in await self.storage.list_open_events(msg["team_id"])]
        prior = await self.storage.list_message_links(msg["conversation_id"], msg["message_id"]) if msg["version"] > 1 else []
        prior_created = {l["event_id"] for l in prior if l["relation"] == "created"}
        claimed: set[int] = set()
        ops: list[dict] = []
        next_virtual = -1
        for item in items:
            title = red.restore(item.title) or item.title
            venue = red.restore(item.venue)
            details = red.restore(item.details)
            conf = item.confidence
            if role == "unknown":
                conf = cap(conf, "medium")  # P3-D-019
            guess = resolve_when(item.when, posted, is_deadline=_is_deadline(item), source_text=msg.get("text") or "")
            match = await find_match({"type": item.type, "title": title, "action": item.action,
                                      "refers_to_existing": item.refers_to_existing}, events, claimed,
                                     posted.astimezone(IST).date(), guess.event_date, self._tiebreak_fn(red, item, title, guess))
            target = match.event
            res = resolve_when(item.when, posted, is_deadline=_is_deadline(item), existing=target, source_text=msg.get("text") or "")
            res = inherit_time(res, target, msg.get("text") or "")
            conf = cap(conf, res.max_confidence)
            review = res.needs_review or conf == "low" or match.needs_review
            if target is None:
                op = self._new_event(msg, item, title, venue, details, res, conf, review)
                if op is None:  # a change with nothing to attach it to and no date: analysis only (DIFF-001 residue)
                    analysis["needs_review"] = True
                    continue
                virtual = {**op["event"], "id": next_virtual, "updated_at": self.clock()}
                next_virtual -= 1
                events.append(virtual)
                claimed.add(virtual["id"])
                op["virtual_id"] = virtual["id"]
                ops.append(op)
                out.created += 1
                # P3-DIFF-017: a specific event was created next to a GENERIC one of the same type ("Quiz 2" vs "Quiz"):
                # we did not merge them, so ask a human to look at the generic one.
                for g in match.generic_events:
                    if g["id"] > 0 and not g.get("needs_review") and g["id"] not in claimed:
                        g["needs_review"] = True
                        ops.append(self._flag_generic(g, title))
                continue
            claimed.add(target["id"])
            op = self._update_event(target, item, title, venue, details, res, conf, review, msg, own_edit=target["id"] in prior_created)
            if op["relation"] == "restated":
                out.restated += 1
            else:
                out.changed += 1
            ops.append(op)
            target.update(op["fields"])
        # FR-304: an edit that no longer mentions an event this message created
        for eid in prior_created - claimed - {-1}:
            ops.append({"kind": "update", "event_id": eid, "fields": {"needs_review": True}, "relation": "changed",
                        "changes": [{"change_type": "details_changed", "old_values": None, "confidence": "medium",
                                     "new_values": {"needs_review": True, "reason": "edited message no longer mentions this event"}}]})
            analysis["needs_review"] = True
        for op in ops:
            op.pop("virtual_id", None)
        return ops

    @staticmethod
    def _flag_generic(g: dict, specific_title: str) -> dict:
        return {"kind": "update", "event_id": g["id"], "fields": {"needs_review": True}, "relation": "flagged", "link": False,
                "changes": [{"change_type": "needs_review", "old_values": {"needs_review": False}, "confidence": "medium",
                             "new_values": {"needs_review": True, "reason": "a more specific event of the same type was announced",
                                            "specific_title": specific_title}}]}

    def _tiebreak_fn(self, red: Redactor, item: Item, title: str, guess: Resolved):
        async def ask(_item: dict, pool: list[dict]) -> str | None:
            when = f"{guess.event_date or 'no date'} {guess.time.strftime('%H:%M') if guess.time else ''}".strip()
            user = (f"<item>type: {item.type}; title: {red.redact(title)}; hint: {red.redact(item.reference_hint or '-')}; "
                    f"action: {item.action}; new date: {when}</item>\n<candidates>\n"
                    + "\n".join(_fmt_event(e, red) for e in pool) + "\n</candidates>")
            comp = await self.router.complete(ResolveChoice, load("resolve"), user)
            return None if comp.value.match == "new" else comp.value.match
        return ask

    # ---------------------------------------------------------------- new event
    def _new_event(self, msg, item: Item, title, venue, details, res: Resolved, conf, review) -> dict | None:
        changeish = item.action != "create"
        if changeish and item.action not in ("cancel", "reschedule") and res.event_date is None:
            return None
        if changeish and res.event_date is None:
            return None  # cancel/reschedule with no usable date and no target: nothing to attach it to
        status = {"cancel": "cancelled", "reschedule": "rescheduled"}.get(item.action, "scheduled")
        if changeish:
            conf = cap(conf, "medium")  # DIFF-001: created from a change; we have not seen the original announcement
        ev = {"team_id": msg["team_id"], "type": item.type, "title": title, "is_deadline": _is_deadline(item),
              "starts_at": res.starts_at, "ends_at": res.ends_at, "event_date": res.event_date, "time_known": res.time_known,
              "time_defaulted": res.time_defaulted, "venue": venue, "details": details, "status": status,
              "confidence": conf, "needs_review": review or conf == "low"}
        return {"kind": "create", "event": ev, "relation": "created",
                "changes": [{"change_type": "created", "old_values": None, "new_values": _snapshot(ev | {"id": 0}),
                             "confidence": conf}]}

    # ---------------------------------------------------------------- existing event
    def _update_event(self, old: dict, item: Item, title, venue, details, res: Resolved, conf, review, msg, own_edit: bool = False) -> dict:
        was = _snapshot(old)
        fields: dict[str, Any] = {}
        changes: list[dict] = []
        conflicts: list[str] = []

        def change(kind: str, before: dict, after: dict) -> None:
            changes.append({"change_type": kind, "old_values": before, "new_values": after, "confidence": conf})

        if item.action == "cancel":
            if old["status"] != "cancelled":
                fields["status"] = "cancelled"
                change("cancelled", {"status": old["status"]}, {"status": "cancelled"})
        else:
            rescheduled = False
            moves = may_move(item.action, msg.get("text")) or own_edit  # an edit of the message that CREATED the event supersedes it
            if res.event_date is not None:
                new_t = res.time
                old_has_time = bool(old.get("time_known"))
                old_t = old["starts_at"].astimezone(IST).time().replace(second=0, microsecond=0) if old.get("starts_at") else None
                after = {"date": res.event_date.isoformat(), "time": new_t.strftime("%H:%M") if new_t else None}
                before = {"date": was["date"], "time": was["time"]}
                kind = None
                if old.get("event_date") is None:
                    kind = "date_set"
                elif res.event_date != old["event_date"]:
                    kind = "rescheduled"
                elif res.time_known and old_has_time and old_t != new_t:
                    kind = "rescheduled"
                elif res.time_known and not old_has_time:
                    kind = "time_set"
                if kind == "rescheduled" and not moves:
                    # A reminder / restatement may not move the event. Same weekday = the same day, only a time can differ.
                    same_day = res.event_date == old["event_date"] or weekday_agrees(item.when, old["event_date"])
                    kind = None
                    if not same_day:
                        conflicts.append("date")
                    elif res.time_known and not old_has_time:
                        kind, after = "time_set", {"date": old["event_date"].isoformat(), "time": new_t.strftime("%H:%M")}
                    elif res.time_known and old_t != new_t:
                        conflicts.append("time")
                if kind:
                    rescheduled = kind == "rescheduled"
                    change(kind, before, after)
                    if kind == "time_set" and res.event_date != old.get("event_date") and old.get("event_date"):
                        # the stated time belongs to the event's own date (the reminder's weekday resolved to another day)
                        fields.update(starts_at=datetime.combine(old["event_date"], new_t, tzinfo=IST).astimezone(timezone.utc),
                                      ends_at=(datetime.combine(old["event_date"], res.end_time, tzinfo=IST).astimezone(timezone.utc)
                                               if res.end_time else None),
                                      time_known=True, time_defaulted=False)
                    else:
                        fields.update(event_date=res.event_date, starts_at=res.starts_at, ends_at=res.ends_at,
                                      time_known=res.time_known, time_defaulted=res.time_defaulted)
            if venue and not wants_same_place(msg.get("text") or "") and _norm(venue) != _norm(old.get("venue")):
                change("venue_changed", {"venue": old.get("venue")}, {"venue": venue})
                fields["venue"] = venue
            if details and item.action not in ("create", "restate") and _norm(details) != _norm(old.get("details")):
                change("details_changed", {"details": old.get("details")}, {"details": details})
                fields["details"] = details
            if old["status"] == "cancelled" and changes:
                fields["status"] = "rescheduled" if rescheduled else "scheduled"
                changes.insert(0, {"change_type": "uncancelled", "old_values": {"status": "cancelled"},
                                   "new_values": {"status": fields["status"]}, "confidence": conf})
            elif item.action == "uncancel" and old["status"] == "cancelled":
                fields["status"] = "scheduled"
                change("uncancelled", {"status": "cancelled"}, {"status": "scheduled"})
            elif rescheduled:
                fields["status"] = "rescheduled"
        if conflicts:  # P3-DIFF-020: the message disagrees with the event but is not allowed to change it: flag, never apply
            fields["needs_review"] = True
            changes.append({"change_type": "needs_review", "old_values": {"needs_review": bool(old.get("needs_review"))},
                            "confidence": "medium",
                            "new_values": {"needs_review": True, "reason": f"reminder conflicts with the event's {' and '.join(conflicts)}",
                                           "stated_date": res.event_date.isoformat() if res.event_date else None,
                                           "stated_time": res.time.strftime("%H:%M") if res.time_known and res.time else None}})
        if not [c for c in changes if c["change_type"] != "needs_review"]:
            relation = "restated"
            if not changes:
                changes.append({"change_type": "restated", "old_values": None, "new_values": None, "confidence": conf})
        else:
            relation = "changed"
            fields["confidence"] = conf
        if review and relation == "changed":  # a doubtful reminder must not flag a good event
            fields["needs_review"] = True
        return {"kind": "update", "event_id": old["id"], "fields": fields, "relation": relation, "changes": changes}


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _is_deadline(item: Item) -> bool:
    return item.is_deadline or item.type in DEADLINE_TYPES
