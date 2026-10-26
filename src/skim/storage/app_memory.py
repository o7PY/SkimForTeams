"""Phase 4 storage for MemoryStorage (student-app tables). See app_postgres.py for the SQL twin."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .app_postgres import FEED_COLS


class MemoryAppMixin:
    def _init_app(self) -> None:
        self.app_users: dict[str, dict] = {}
        self.subscriptions: dict[tuple[str, str], dict] = {}
        self.join_attempts: list[dict] = []
        self.feed_items: dict[int, dict] = {}
        self.feed_item_events: set[tuple[int, int]] = set()
        self.user_item_state: dict[tuple[str, int], dict] = {}
        self.stars: dict[tuple[str, int], dict] = {}
        self.reminders: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}  # by token
        self.push_outbox: list[dict] = []
        self._feed_seq = 0
        self._device_seq = 0
        self._push_seq = 0

    # --- users ---
    async def upsert_app_user(self, user_id: str, email: str | None, name: str | None, at: datetime) -> dict:
        u = self.app_users.get(user_id)
        if u is None:
            u = self.app_users[user_id] = {"id": user_id, "email": email, "name": name, "created_at": at,
                                           "last_seen_at": at, "settings": {}}
        else:
            u["email"] = email if email is not None else u["email"]
            u["name"] = name if name is not None else u["name"]
            u["last_seen_at"] = at
        return dict(u)

    async def get_app_user(self, user_id: str) -> dict | None:
        u = self.app_users.get(user_id)
        return dict(u) if u else None

    async def set_user_settings(self, user_id: str, settings: dict) -> None:
        if user_id in self.app_users:
            self.app_users[user_id]["settings"] = dict(settings)

    async def delete_app_user(self, user_id: str) -> bool:
        if user_id not in self.app_users:
            return False
        del self.app_users[user_id]
        for table in (self.subscriptions, self.user_item_state, self.stars):
            for k in [k for k in table if k[0] == user_id]:
                del table[k]
        self.join_attempts = [a for a in self.join_attempts if a["user_id"] != user_id]
        self.reminders = {k: v for k, v in self.reminders.items() if v["user_id"] != user_id}
        self.devices = {k: v for k, v in self.devices.items() if v["user_id"] != user_id}
        self.push_outbox = [p for p in self.push_outbox if p["user_id"] != user_id]
        return True

    # --- joining ---
    async def get_team_by_code(self, code: str) -> dict | None:
        norm = "".join(code.split()).upper()
        for t in self.teams.values():
            if t.get("pairing_code") and t["pairing_code"].upper() == norm:
                return dict(t)
        return None

    async def record_join_attempt(self, user_id: str, at: datetime, ok: bool) -> None:
        self.join_attempts.append({"user_id": user_id, "at": at, "ok": ok})

    async def count_join_attempts(self, user_id: str, since: datetime) -> int:
        return sum(1 for a in self.join_attempts if a["user_id"] == user_id and a["at"] >= since)

    async def add_subscription(self, user_id: str, team_id: str, at: datetime) -> bool:
        s = self.subscriptions.get((user_id, team_id))
        if s is not None and s["left_at"] is None:
            return False
        self.subscriptions[(user_id, team_id)] = {"user_id": user_id, "team_id": team_id, "joined_at": at,
                                                  "muted": False, "left_at": None}
        return True

    async def list_subscriptions(self, user_id: str, include_left: bool = False) -> list[dict]:
        out = []
        for (uid, tid), s in self.subscriptions.items():
            if uid == user_id and (include_left or s["left_at"] is None):
                t = self.teams[tid]
                out.append({**s, "name": t.get("name"), "removed_at": t.get("removed_at")})
        return sorted(out, key=lambda r: (r["joined_at"], r["team_id"]))

    async def set_subscription_muted(self, user_id: str, team_id: str, muted: bool) -> bool:
        s = self.subscriptions.get((user_id, team_id))
        if s is None or s["left_at"] is not None:
            return False
        s["muted"] = muted
        return True

    async def leave_subscription(self, user_id: str, team_id: str, at: datetime) -> bool:
        s = self.subscriptions.get((user_id, team_id))
        if s is None or s["left_at"] is not None:
            return False
        s["left_at"] = at
        items = {i for i, f in self.feed_items.items() if f["team_id"] == team_id}
        for table in (self.user_item_state, self.stars):
            for k in [k for k in table if k[0] == user_id and k[1] in items]:
                del table[k]
        self.reminders = {k: v for k, v in self.reminders.items()
                          if not (v["user_id"] == user_id and v.get("item_id") in items)}
        return True

    # --- feed items ---
    def _find_feed(self, item: dict) -> int | None:
        kind = item["kind"]
        for iid, f in self.feed_items.items():
            if f["kind"] != kind:
                continue
            if kind == "message" and (f["conversation_id"], f["message_id"]) == (item.get("conversation_id"), item.get("message_id")):
                return iid
            if kind == "document" and f["document_event_id"] == item.get("document_event_id"):
                return iid
            if kind == "change" and f["event_change_id"] == item.get("event_change_id"):
                return iid
        return None

    async def upsert_feed_item(self, item: dict, event_ids: list[int]) -> tuple[int, bool]:
        iid = self._find_feed(item)
        created = iid is None
        if created:
            self._feed_seq += 1
            iid = self._feed_seq
            row = {c: item.get(c) for c in FEED_COLS}
            row["hidden"] = bool(item.get("hidden", False))
            self.feed_items[iid] = {"id": iid, **row}
        for eid in event_ids:
            self.feed_item_events.add((iid, eid))
        return iid, created

    async def set_feed_item_hidden(self, item_id: int, hidden: bool) -> None:
        if item_id in self.feed_items:
            self.feed_items[item_id]["hidden"] = hidden

    async def get_feed_item(self, item_id: int) -> dict | None:
        f = self.feed_items.get(item_id)
        if f is None:
            return None
        return {**f, "event_ids": sorted(e for i, e in self.feed_item_events if i == item_id)}

    # --- per-user swipe state ---
    async def set_item_state(self, user_id: str, item_ids: list[int], state: str | None, at: datetime,
                             snoozed_until: datetime | None = None, folded_into: int | None = None) -> None:
        if state is None and snoozed_until is None:
            raise ValueError("state may be None only with snoozed_until")
        for iid in item_ids:
            self.user_item_state[(user_id, iid)] = {"user_id": user_id, "item_id": iid, "state": state, "acted_at": at,
                                                    "snoozed_until": snoozed_until, "folded_into": folded_into}

    async def clear_item_state(self, user_id: str, item_ids: list[int]) -> None:
        for iid in item_ids:
            self.user_item_state.pop((user_id, iid), None)

    async def get_item_states(self, user_id: str, item_ids: list[int]) -> dict[int, dict]:
        return {i: dict(self.user_item_state[(user_id, i)]) for i in item_ids if (user_id, i) in self.user_item_state}

    # --- stars ---
    async def put_star(self, user_id: str, item_id: int, expires_at: datetime | None, until_event_over: bool,
                       at: datetime) -> None:
        old = self.stars.get((user_id, item_id))
        self.stars[(user_id, item_id)] = {"user_id": user_id, "item_id": item_id, "expires_at": expires_at,
                                          "until_event_over": until_event_over, "created_at": old["created_at"] if old else at}

    async def delete_star(self, user_id: str, item_id: int) -> bool:
        return self.stars.pop((user_id, item_id), None) is not None

    async def list_stars(self, user_id: str) -> list[dict]:
        rows = [dict(s) for s in self.stars.values() if s["user_id"] == user_id]
        return sorted(rows, key=lambda s: (s["created_at"], s["item_id"]), reverse=True)

    # --- reminders ---
    async def upsert_reminder(self, r: dict, at: datetime) -> dict | None:
        cur = self.reminders.get(r["id"])
        if cur is not None and cur["user_id"] != r["user_id"]:
            return None  # not the owner: nothing changes (SQL: the guarded update returns no row)
        row = {"id": r["id"], "user_id": r["user_id"], "item_id": r.get("item_id"), "event_id": r.get("event_id"),
               "kind": r["kind"], "offset_minutes": r.get("offset_minutes"), "fire_at": r.get("fire_at"),
               "deleted_at": None, "created_at": cur["created_at"] if cur else at, "updated_at": at}
        self.reminders[r["id"]] = row
        return dict(row)

    async def tombstone_reminder(self, user_id: str, reminder: dict, at: datetime) -> None:
        cur = self.reminders.get(reminder["id"])
        if cur is not None:
            if cur["user_id"] == user_id:
                cur["deleted_at"], cur["updated_at"] = at, at
            return
        self.reminders[reminder["id"]] = {
            "id": reminder["id"], "user_id": user_id, "item_id": reminder.get("item_id"), "event_id": reminder.get("event_id"),
            "kind": reminder["kind"], "offset_minutes": reminder.get("offset_minutes"), "fire_at": reminder.get("fire_at"),
            "deleted_at": at, "created_at": at, "updated_at": at}

    async def get_reminder(self, user_id: str, reminder_id: str) -> dict | None:
        r = self.reminders.get(reminder_id)
        return dict(r) if r and r["user_id"] == user_id else None

    async def list_reminders(self, user_id: str, include_deleted: bool = False) -> list[dict]:
        rows = [dict(r) for r in self.reminders.values()
                if r["user_id"] == user_id and (include_deleted or r["deleted_at"] is None)]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]))

    # --- devices ---
    async def upsert_device(self, user_id: str, token: str, platform: str, at: datetime) -> None:
        d = self.devices.get(token)
        if d is None:
            self._device_seq += 1
            self.devices[token] = {"id": self._device_seq, "user_id": user_id, "expo_push_token": token,
                                   "platform": platform, "created_at": at, "last_seen_at": at, "disabled_at": None}
        else:
            d.update(user_id=user_id, platform=platform, last_seen_at=at, disabled_at=None)

    async def disable_device(self, token: str, at: datetime) -> None:
        d = self.devices.get(token)
        if d and d["disabled_at"] is None:
            d["disabled_at"] = at

    async def delete_device(self, user_id: str, token: str) -> bool:
        d = self.devices.get(token)
        if d and d["user_id"] == user_id:
            del self.devices[token]
            return True
        return False

    async def list_devices(self, user_id: str, active_only: bool = True) -> list[dict]:
        rows = [dict(d) for d in self.devices.values()
                if d["user_id"] == user_id and (not active_only or d["disabled_at"] is None)]
        return sorted(rows, key=lambda d: d["id"])

    # --- push outbox ---
    async def add_push(self, row: dict) -> bool:
        if any(p["user_id"] == row["user_id"] and p["event_change_id"] == row["event_change_id"] for p in self.push_outbox):
            return False
        self._push_seq += 1
        self.push_outbox.append({"id": self._push_seq, "user_id": row["user_id"], "event_change_id": row["event_change_id"],
                                 "title": row["title"], "body": row["body"], "data": row.get("data") or {},
                                 "not_before": row["not_before"], "status": row.get("status", "pending"), "ticket_id": None,
                                 "receipt_status": None, "error": row.get("error"), "created_at": row["not_before"],
                                 "sent_at": None})
        return True

    async def list_due_push(self, now: datetime, limit: int) -> list[dict]:
        rows = [dict(p) for p in self.push_outbox if p["status"] == "pending" and p["not_before"] <= now]
        return sorted(rows, key=lambda p: (p["not_before"], p["id"]))[:limit]

    async def list_push_awaiting_receipt(self, sent_before: datetime, limit: int) -> list[dict]:
        rows = [dict(p) for p in self.push_outbox if p["status"] == "sent" and p["ticket_id"]
                and p["receipt_status"] is None and p["sent_at"] and p["sent_at"] <= sent_before]
        return sorted(rows, key=lambda p: p["id"])[:limit]

    async def update_push(self, push_id: int, **fields: Any) -> None:
        bad = set(fields) - {"status", "ticket_id", "receipt_status", "error", "sent_at"}
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        for p in self.push_outbox:
            if p["id"] == push_id:
                p.update(fields)

    async def list_push(self, user_id: str | None = None) -> list[dict]:
        return [dict(p) for p in self.push_outbox if user_id is None or p["user_id"] == user_id]
