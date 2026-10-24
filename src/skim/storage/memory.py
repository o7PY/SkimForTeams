"""In-memory Storage for tests and local dev."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
        # Phase 2
        self.graph_raw: list[dict] = []
        self._graph_keys: dict[str, int] = {}
        self.channels: dict[str, dict] = {}
        self.documents: dict[int, dict] = {}
        self.document_events: list[dict] = []
        self.sync_state: dict[str, dict] = {}
        self.job_runs: list[dict] = []
        self.links: set[tuple[int, int, str]] = set()
        self._locks: set[str] = set()

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
                                                 "removed_at": None, "installed_by_aad": None,
                                                 "welcome_sent_at": None, "needs_initial_sync": True,
                                                 "last_discovered_at": None, "last_files_sync_at": None,
                                                 "last_backfill_at": None})
        for k in ("aad_group_id", "name", "tenant_id"):
            if team.get(k) is not None:
                cur[k] = team[k]
            else:
                cur.setdefault(k, None)

    async def mark_team_installed(self, team_id, pairing_code, by_aad, at) -> None:
        await self.upsert_team({"id": team_id})
        t = self.teams[team_id]
        if t["installed_at"] is None or t["removed_at"] is not None:  # a new install, not a retry/upgrade
            t.update(installed_at=at, removed_at=None, installed_by_aad=by_aad, welcome_sent_at=None)
        t["pairing_code"] = pairing_code

    async def claim_welcome(self, team_id, at) -> bool:
        t = self.teams.get(team_id)
        if t is None or t["welcome_sent_at"] is not None:
            return False
        t["welcome_sent_at"] = at
        return True

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

    # ------------------------------------------------------------------ Phase 2
    _TEAM_FIELDS = {"needs_initial_sync", "last_discovered_at", "last_files_sync_at", "last_backfill_at"}
    _CHANNEL_FIELDS = {"display_name", "membership_type", "files_drive_id", "files_folder_item_id",
                       "files_folder_path", "last_synced_at", "sync_error"}
    _DOC_COLS = ("drive_id", "item_id", "team_id", "channel_id", "name", "parent_item_id", "path", "is_folder",
                 "size_bytes", "mime_type", "web_url", "web_url_norm", "etag", "ctag", "created_at", "created_by_aad",
                 "created_by_name", "modified_at", "modified_by_aad", "modified_by_name", "deleted_at", "graph_raw_id")

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _check(fields: dict, allowed: set[str], what: str) -> None:
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {what}.{sorted(bad)}")

    async def insert_graph_raw(self, row: dict) -> tuple[int, bool]:
        if row["dedupe_key"] in self._graph_keys:
            return self._graph_keys[row["dedupe_key"]], False
        rid = len(self.graph_raw) + 1
        self.graph_raw.append({"id": rid, "fetched_at": self._now(), **row})
        self._graph_keys[row["dedupe_key"]] = rid
        return rid, True

    async def list_sync_teams(self) -> list[dict]:
        return [t for t in self.teams.values() if t.get("aad_group_id") and t["removed_at"] is None]

    async def update_team(self, team_id: str, **fields) -> None:
        self._check(fields, self._TEAM_FIELDS, "teams")
        self.teams[team_id].update(fields)

    async def upsert_channel(self, ch: dict) -> None:
        cur = self.channels.setdefault(ch["id"], {
            "id": ch["id"], "team_id": ch["team_id"], "display_name": None, "membership_type": None,
            "files_drive_id": None, "files_folder_item_id": None, "files_folder_path": None,
            "discovered_at": self._now(), "last_synced_at": None, "sync_error": None})
        cur["team_id"] = ch["team_id"]
        for k in ("display_name", "membership_type", "files_drive_id", "files_folder_item_id", "files_folder_path"):
            if ch.get(k) is not None:
                cur[k] = ch[k]

    async def update_channel(self, channel_id: str, **fields) -> None:
        self._check(fields, self._CHANNEL_FIELDS, "channels")
        self.channels[channel_id].update(fields)

    async def list_channels(self, team_id: str | None = None) -> list[dict]:
        return sorted((c for c in self.channels.values() if team_id in (None, c["team_id"])),
                      key=lambda c: (c["team_id"], c["display_name"] or "", c["id"]))

    def _doc_key(self, drive_id: str, item_id: str) -> int | None:
        return next((i for i, d in self.documents.items() if (d["drive_id"], d["item_id"]) == (drive_id, item_id)), None)

    async def get_document(self, drive_id: str, item_id: str) -> dict | None:
        i = self._doc_key(drive_id, item_id)
        return dict(self.documents[i]) if i else None

    async def get_document_by_id(self, document_id: int) -> dict | None:
        d = self.documents.get(document_id)
        return dict(d) if d else None

    async def upsert_document(self, doc: dict) -> int:
        now = doc.get("last_seen_at") or self._now()
        i = self._doc_key(doc["drive_id"], doc["item_id"])
        row = {c: doc.get(c) for c in self._DOC_COLS}
        row["is_folder"] = bool(doc.get("is_folder"))
        if i is None:
            i = max(self.documents, default=0) + 1
            self.documents[i] = {"id": i, "first_seen_at": self._now(), **row, "last_seen_at": now}
        else:
            self.documents[i].update(row, last_seen_at=now)
        return i

    async def list_documents(self, team_id=None, channel_id=None, include_deleted=False, limit=100) -> list[dict]:
        rows = [dict(d) for d in self.documents.values()
                if team_id in (None, d["team_id"]) and channel_id in (None, d["channel_id"])
                and (include_deleted or d["deleted_at"] is None)]
        rows.sort(key=lambda d: (d["channel_id"] or "", d["path"] or ""))
        return rows[:limit]

    async def list_child_documents(self, drive_id: str, parent_item_id: str) -> list[dict]:
        return [dict(d) for d in self.documents.values()
                if d["drive_id"] == drive_id and d["parent_item_id"] == parent_item_id]

    async def soft_delete_unseen(self, channel_id: str, seen_since: datetime, at: datetime) -> list[dict]:
        out = []
        for d in self.documents.values():
            if d["channel_id"] == channel_id and d["deleted_at"] is None and d["last_seen_at"] < seen_since:
                d["deleted_at"] = at
                out.append(dict(d))
        return out

    async def add_document_event(self, ev: dict) -> int:
        row = {"id": len(self.document_events) + 1, **{k: ev.get(k) for k in (
            "document_id", "event", "at", "old_name", "new_name", "old_path", "new_path", "graph_raw_id")}}
        self.document_events.append(row)
        return row["id"]

    async def list_document_events(self, document_id: int) -> list[dict]:
        return sorted((dict(e) for e in self.document_events if e["document_id"] == document_id),
                      key=lambda e: (e["at"], e["id"]))

    async def get_sync_state(self, key: str) -> dict | None:
        r = self.sync_state.get(key)
        return dict(r) if r else None

    async def put_sync_state(self, key, *, delta_link=None, cursor=None, last_status=None, last_error=None) -> None:
        self.sync_state[key] = {"key": key, "delta_link": delta_link, "cursor": cursor, "updated_at": self._now(),
                                "last_status": last_status, "last_error": last_error}

    async def delete_sync_state(self, key: str) -> None:
        self.sync_state.pop(key, None)

    async def list_sync_state(self) -> list[dict]:
        return [dict(self.sync_state[k]) for k in sorted(self.sync_state)]

    async def start_job_run(self, job: str) -> int:
        rid = len(self.job_runs) + 1
        self.job_runs.append({"id": rid, "job": job, "started_at": self._now(), "finished_at": None,
                              "status": None, "stats": None, "error": None})
        return rid

    async def finish_job_run(self, run_id: int, status: str, stats: dict, error: str | None = None) -> None:
        self.job_runs[run_id - 1].update(finished_at=self._now(), status=status, stats=stats, error=error)

    async def latest_job_runs(self) -> list[dict]:
        latest: dict[str, dict] = {}
        for r in self.job_runs:
            latest[r["job"]] = r
        return [dict(latest[j]) for j in sorted(latest)]

    @asynccontextmanager
    async def job_lock(self, name: str):
        got = name not in self._locks
        if got:
            self._locks.add(name)
        try:
            yield got
        finally:
            if got:
                self._locks.discard(name)

    async def add_attachment_link(self, attachment_id: int, document_id: int, method: str) -> None:
        if not any(a == attachment_id and d == document_id for a, d, _ in self.links):
            self.links.add((attachment_id, document_id, method))

    async def list_document_links(self, document_id: int) -> list[dict]:
        by_id = {a.get("id"): a for a in self.attachments}
        return [{"match_method": m, **by_id.get(a, {"id": a})} for a, d, m in sorted(self.links) if d == document_id]
