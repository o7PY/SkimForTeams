"""In-memory Storage for tests and local dev."""
from __future__ import annotations

from datetime import datetime

from ..normalize import MessageEvent


class MemoryStorage:
    def __init__(self) -> None:
        self.raw: list[dict] = []
        self.teams: dict[str, dict] = {}
        self.conversations: dict[str, dict] = {}
        self.messages: dict[tuple[str, str], dict] = {}
        self.versions: list[dict] = []
        self.attachments: list[dict] = []
        self.reactions: list[dict] = []
        self._keys: dict[str, int] = {}

    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool:
        return True

    async def insert_raw(self, row: dict) -> tuple[int, bool]:
        if row["dedupe_key"] in self._keys:
            return self._keys[row["dedupe_key"]], False
        rid = len(self.raw) + 1
        self.raw.append({"id": rid, "received_at": datetime.now().astimezone(), **row})
        self._keys[row["dedupe_key"]] = rid
        return rid, True

    async def upsert_team(self, team: dict) -> None:
        cur = self.teams.setdefault(team["id"], {"id": team["id"], "pairing_code": None, "installed_at": None,
                                                 "removed_at": None, "installed_by_aad": None})
        for k in ("aad_group_id", "name", "tenant_id"):
            if team.get(k) is not None:
                cur[k] = team[k]
            else:
                cur.setdefault(k, None)

    async def mark_team_installed(self, team_id, pairing_code, by_aad, at) -> None:
        await self.upsert_team({"id": team_id})
        self.teams[team_id].update(pairing_code=pairing_code, installed_at=at, removed_at=None,
                                   installed_by_aad=by_aad)

    async def mark_team_removed(self, team_id, at) -> None:
        await self.upsert_team({"id": team_id})
        self.teams[team_id]["removed_at"] = at

    async def upsert_conversation(self, conv: dict) -> None:
        cur = self.conversations.setdefault(conv["id"], {"id": conv["id"], "first_seen_at": datetime.now().astimezone()})
        for k, v in conv.items():
            if v is not None:
                cur[k] = v
            else:
                cur.setdefault(k, None)

    async def apply_message_event(self, ev: MessageEvent, raw_id: int) -> bool:
        key = (ev.conversation_id, ev.message_id)
        msg = self.messages.get(key)
        known = msg is not None
        if msg is None:
            msg = self.messages[key] = {
                "conversation_id": ev.conversation_id, "message_id": ev.message_id,
                "reply_to_id": ev.reply_to_id, "from_aad_id": ev.from_aad_id, "from_name": ev.from_name,
                "text": ev.text, "html": ev.html, "importance": ev.importance, "mentions": ev.mentions,
                "created_at": ev.at if ev.event == "created" else None,
                "last_edited_at": ev.at if ev.event in ("edited", "undeleted") else None,
                "deleted_at": ev.at if ev.event == "deleted" else None,
                "current_version": 1, "raw_activity_id": raw_id,
            }
            version = 1
        elif ev.event == "created":
            return True  # duplicate create with different payload; keep what we have
        else:
            msg["current_version"] += 1
            version = msg["current_version"]
            if ev.event in ("edited", "undeleted"):
                msg["text"], msg["html"], msg["last_edited_at"] = ev.text, ev.html, ev.at
                msg["mentions"] = ev.mentions
                if ev.event == "undeleted":
                    msg["deleted_at"] = None
            elif ev.event == "deleted":
                msg["deleted_at"] = ev.at
        v_text = msg["text"] if ev.event == "deleted" else ev.text
        v_html = msg["html"] if ev.event == "deleted" else ev.html
        self.versions.append({"conversation_id": ev.conversation_id, "message_id": ev.message_id,
                              "version": version, "text": v_text, "html": v_html, "event": ev.event,
                              "at": ev.at, "raw_activity_id": raw_id})
        return known or ev.event == "created"

    async def add_attachments(self, rows, raw_id) -> None:
        for r in rows:
            if not any(a["conversation_id"] == r["conversation_id"] and a["message_id"] == r["message_id"]
                       and a["payload"] == r["payload"] for a in self.attachments):
                self.attachments.append({"id": len(self.attachments) + 1, **r, "raw_activity_id": raw_id})

    async def add_reactions(self, rows, raw_id) -> None:
        for r in rows:
            self.reactions.append({"id": len(self.reactions) + 1, **r, "raw_activity_id": raw_id})

    async def list_raw(self, limit, type) -> list[dict]:
        rows = [r for r in self.raw if not type or r["activity_type"] == type]
        return list(reversed(rows))[:limit]

    def _msg_summary(self, m: dict) -> dict:
        k = (m["conversation_id"], m["message_id"])
        return {**m,
                "version_count": sum(1 for v in self.versions if (v["conversation_id"], v["message_id"]) == k),
                "attachment_count": sum(1 for a in self.attachments
                                        if (a["conversation_id"], a["message_id"]) == k),
                "deleted": m["deleted_at"] is not None}

    async def list_messages(self, limit, team_id, conversation_id) -> list[dict]:
        out = []
        for m in self.messages.values():
            if conversation_id and m["conversation_id"] != conversation_id:
                continue
            if team_id and self.conversations.get(m["conversation_id"], {}).get("team_id") != team_id:
                continue
            out.append(m)
        out.sort(key=lambda m: m["created_at"] or m["last_edited_at"] or m["deleted_at"], reverse=True)
        return [self._msg_summary(m) for m in out[:limit]]

    async def get_message(self, conversation_id, message_id):
        m = self.messages.get((conversation_id, message_id))
        if not m:
            return None
        k = (conversation_id, message_id)
        return {
            "message": m,
            "versions": sorted((v for v in self.versions if (v["conversation_id"], v["message_id"]) == k),
                               key=lambda v: v["version"]),
            "attachments": [a for a in self.attachments if (a["conversation_id"], a["message_id"]) == k],
            "reactions": [r for r in self.reactions if (r["conversation_id"], r["message_id"]) == k],
        }

    async def list_teams(self) -> list[dict]:
        return list(self.teams.values())
