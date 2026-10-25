"""Phase 3 storage for MemoryStorage (messages analysis, events, change history). See ai_postgres.py for the SQL twin."""
from __future__ import annotations

from datetime import datetime, timezone

_MIN = datetime.min.replace(tzinfo=timezone.utc)
EVENT_FIELDS = {"type", "title", "is_deadline", "starts_at", "ends_at", "event_date", "time_known", "time_defaulted",
                "venue", "details", "status", "confidence", "needs_review"}


class MemoryAIMixin:
    def _init_ai(self) -> None:
        self.analyses: dict[tuple, dict] = {}
        self.events: dict[int, dict] = {}
        self.event_changes: list[dict] = []
        self.message_event_links: dict[tuple, dict] = {}
        self._event_seq = 0

    async def list_pending_messages(self, prompt_version: str, max_attempts: int, limit: int) -> list[dict]:
        out = []
        for (cid, mid), m in self.messages.items():
            conv = self.conversations.get(cid) or {}
            team = self.teams.get(conv.get("team_id") or "")
            if (m.get("deleted_at") or conv.get("conversation_type") != "channel" or team is None
                    or team.get("removed_at")):
                continue
            ch = self.channels.get(cid)
            if ch and ch.get("membership_type") not in (None, "standard"):
                continue
            if not (m.get("text") or m.get("subject")):
                continue
            a = self.analyses.get((cid, mid, m["current_version"], prompt_version))
            if a and not (a["status"] == "failed" and a["attempts"] < max_attempts):
                continue
            parent = self.messages.get((cid, m["reply_to_id"])) if m.get("reply_to_id") else None
            out.append({"conversation_id": cid, "message_id": mid, "version": m["current_version"],
                        "text": m.get("text"), "subject": m.get("subject"), "created_at": m.get("created_at"),
                        "last_edited_at": m.get("last_edited_at"), "from_aad_id": m.get("from_aad_id"),
                        "from_name": m.get("from_name"), "reply_to_id": m.get("reply_to_id"),
                        "mentions": m.get("mentions"), "team_id": team["id"],
                        "installed_by_aad": team.get("installed_by_aad"),
                        "parent_text": parent.get("text") if parent else None})
        out.sort(key=lambda r: (r["created_at"] or r["last_edited_at"] or _MIN, r["message_id"]))
        return out[:limit]

    async def list_open_events(self, team_id: str, limit: int = 100) -> list[dict]:
        rows = [dict(e) for e in self.events.values() if e["team_id"] == team_id and e["status"] != "done"]
        rows.sort(key=lambda e: (e["updated_at"], e["id"]), reverse=True)
        return rows[:limit]

    async def list_message_links(self, conversation_id: str, message_id: str) -> list[dict]:
        return [dict(v) for k, v in self.message_event_links.items() if k[:2] == (conversation_id, message_id)]

    async def commit_message_analysis(self, analysis: dict, ops: list[dict], source: dict) -> list[dict]:
        key = (analysis["conversation_id"], analysis["message_id"], analysis["message_version"], analysis["prompt_version"])
        prev = self.analyses.get(key)
        self.analyses[key] = {"error_class": None, "output": None, "provider": None, "model": None, "category": None,
                              "confidence": None, "author_role": None, "needs_review": False, **analysis,
                              "attempts": (prev["attempts"] if prev else 0) + 1,
                              "processed_at": datetime.now(timezone.utc)}
        now = datetime.now(timezone.utc)
        results = []
        for op in ops:
            if op["kind"] == "create":
                self._event_seq += 1
                eid = self._event_seq
                self.events[eid] = {"id": eid, "source_kind": source["kind"], "source_ref": source["ref"],
                                    "created_at": now, "updated_at": now, **op["event"]}
            else:
                eid = op["event_id"]
                unknown = set(op["fields"]) - EVENT_FIELDS
                assert not unknown, unknown
                self.events[eid].update(op["fields"], updated_at=now)
            for ch in op["changes"]:
                self.event_changes.append({"id": len(self.event_changes) + 1, "event_id": eid, "source_kind": source["kind"],
                                           "source_ref": source["ref"], "at": source["at"], "recorded_at": now, **ch})
            if source["kind"] == "message":
                lk = (source["ref"]["conversation_id"], source["ref"]["message_id"], eid)
                old = self.message_event_links.get(lk)
                rel = "created" if old and old["relation"] == "created" else op["relation"]
                self.message_event_links[lk] = {"conversation_id": lk[0], "message_id": lk[1], "event_id": eid,
                                                "relation": rel}
            results.append({"event_id": eid, "relation": op["relation"]})
        return results

    async def list_events(self, team_id=None, status=None, type=None, needs_review=None, limit=100) -> list[dict]:
        rows = [dict(e) for e in self.events.values()
                if (not team_id or e["team_id"] == team_id) and (not status or e["status"] == status)
                and (not type or e["type"] == type) and (needs_review is None or e["needs_review"] == needs_review)]
        rows.sort(key=lambda e: (e["event_date"] is None, e["event_date"] or datetime.max.date(), e["id"]))
        return rows[:limit]

    async def get_event(self, event_id: int) -> dict | None:
        e = self.events.get(event_id)
        if e is None:
            return None
        changes = sorted((dict(c) for c in self.event_changes if c["event_id"] == event_id), key=lambda c: (c["at"], c["id"]))
        links = [dict(v) for v in self.message_event_links.values() if v["event_id"] == event_id]
        return {"event": dict(e), "changes": changes, "messages": links}

    async def list_message_analyses(self, conversation_id: str, message_id: str) -> list[dict]:
        rows = [dict(a) for k, a in self.analyses.items() if k[:2] == (conversation_id, message_id)]
        return sorted(rows, key=lambda a: (a["message_version"], a["prompt_version"]))

    async def flag_events_of_deleted_messages(self) -> int:
        """Events created by a message that was later deleted, with no live message still linked, need a human look."""
        n = 0
        for e in self.events.values():
            if e["source_kind"] != "message" or e["needs_review"]:
                continue
            links = [v for v in self.message_event_links.values() if v["event_id"] == e["id"]]
            alive = [v for v in links
                     if not (self.messages.get((v["conversation_id"], v["message_id"])) or {}).get("deleted_at")]
            src = self.messages.get((e["source_ref"].get("conversation_id"), e["source_ref"].get("message_id")))
            if src and src.get("deleted_at") and not alive:
                e["needs_review"] = True
                n += 1
        return n

    async def ai_status(self, prompt_version: str, max_attempts: int) -> dict:
        by_status: dict[str, int] = {}
        failures: dict[str, int] = {}
        for a in self.analyses.values():
            by_status[a["status"]] = by_status.get(a["status"], 0) + 1
            if a["status"] == "failed":
                failures[a["error_class"] or "unknown"] = failures.get(a["error_class"] or "unknown", 0) + 1
        pending = len(await self.list_pending_messages(prompt_version, max_attempts, 10**6))
        return {"analyses_by_status": by_status, "failures_by_error_class": failures, "messages_pending": pending,
                "events": len(self.events), "events_needing_review": sum(1 for e in self.events.values() if e["needs_review"])}
