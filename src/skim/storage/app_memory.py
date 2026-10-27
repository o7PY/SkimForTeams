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
        self.auth_deleted: set[str] = set()  # stands in for deleted rows of Supabase's auth.users
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

    async def auth_user_exists(self, user_id: str) -> bool:
        return user_id not in self.auth_deleted

    async def delete_auth_user(self, user_id: str) -> bool:
        existed = user_id not in self.auth_deleted
        self.auth_deleted.add(user_id)
        return existed

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
        events = {i for i, e in self.events.items() if e["team_id"] == team_id}
        self.reminders = {k: v for k, v in self.reminders.items()
                          if not (v["user_id"] == user_id and (v.get("item_id") in items or v.get("event_id") in events))}
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
            self.feed_items[iid] = {"id": iid, "created_at": datetime.now().astimezone(), **row}
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

    async def tombstone_reminder(self, user_id: str, reminder: dict, at: datetime) -> bool:
        cur = self.reminders.get(reminder["id"])
        if cur is not None:
            if cur["user_id"] != user_id:
                return False
            cur["deleted_at"], cur["updated_at"] = at, at
            return True
        self.reminders[reminder["id"]] = {
            "id": reminder["id"], "user_id": user_id, "item_id": reminder.get("item_id"), "event_id": reminder.get("event_id"),
            "kind": reminder["kind"], "offset_minutes": reminder.get("offset_minutes"), "fire_at": reminder.get("fire_at"),
            "deleted_at": at, "created_at": at, "updated_at": at}
        return True

    async def restore_reminder(self, user_id: str, reminder_id: str, at: datetime) -> bool:
        cur = self.reminders.get(reminder_id)
        if cur is None or cur["user_id"] != user_id:
            return False
        cur["deleted_at"], cur["updated_at"] = None, at
        return True

    async def get_reminder(self, user_id: str, reminder_id: str) -> dict | None:
        r = self.reminders.get(reminder_id)
        return dict(r) if r and r["user_id"] == user_id else None

    async def list_reminders(self, user_id: str, include_deleted: bool = False) -> list[dict]:
        rows = [dict(r) for r in self.reminders.values()
                if r["user_id"] == user_id and (include_deleted or r["deleted_at"] is None)]
        return sorted(rows, key=lambda r: (r["created_at"], r["id"]))

    # --- devices ---
    async def upsert_device(self, user_id: str, token: str, platform: str, at: datetime) -> bool:
        d = self.devices.get(token)
        if d is None:
            self._device_seq += 1
            self.devices[token] = {"id": self._device_seq, "user_id": user_id, "expo_push_token": token,
                                   "platform": platform, "created_at": at, "last_seen_at": at, "disabled_at": None}
            return True
        same = d["user_id"] == user_id
        if not same and d["disabled_at"] is None:
            return False
        d.update(user_id=user_id, platform=platform, last_seen_at=at, disabled_at=None if same else d["disabled_at"])
        return True

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


class MemoryFeedMixin:
    """feed-build candidates for MemoryStorage (twin of PostgresFeedMixin; keep the two in step)."""

    def _team_live(self, team_id) -> dict | None:
        t = self.teams.get(team_id)
        return t if t is not None and t.get("removed_at") is None else None

    def _standard_channel(self, channel_id) -> bool:
        ch = self.channels.get(channel_id)
        return (ch.get("membership_type") if ch else None) in (None, "standard")

    def _has_item(self, kind: str, **match) -> bool:
        return any(f["kind"] == kind and all(f.get(k) == v for k, v in match.items()) for f in self.feed_items.values())

    async def feed_candidate_messages(self, since: datetime | None, limit: int) -> list[dict]:
        out = []
        for (cid, mid), m in self.messages.items():
            conv = self.conversations.get(cid) or {}
            team = self._team_live(conv.get("team_id"))
            when = m.get("created_at") or m.get("last_edited_at")
            if (team is None or conv.get("conversation_type") != "channel" or m.get("deleted_at") is not None
                    or not self._standard_channel(cid) or when is None or (since is not None and when < since)):
                continue
            has_att = any(a["conversation_id"] == cid and a["message_id"] == mid for a in self.attachments)
            if not (m.get("text") or m.get("subject") or has_att) or self._has_item("message", conversation_id=cid, message_id=mid):
                continue
            ok = [a for k, a in self.analyses.items()
                  if k[0] == cid and k[1] == mid and k[2] == m["current_version"] and a["status"] == "ok"]
            ok.sort(key=lambda a: a["processed_at"])
            out.append({"conversation_id": cid, "message_id": mid, "from_aad_id": m.get("from_aad_id"),
                        "created_at": m.get("created_at"), "last_edited_at": m.get("last_edited_at"),
                        "team_id": conv["team_id"], "installed_by_aad": team.get("installed_by_aad"),
                        "category": ok[-1]["category"] if ok else None,
                        "event_ids": sorted(l["event_id"] for k, l in self.message_event_links.items() if k[:2] == (cid, mid)),
                        "_when": when})
        out.sort(key=lambda r: (r["_when"], r["message_id"]))
        return [{k: v for k, v in r.items() if k != "_when"} for r in out[:limit]]

    def _message_card_visible(self, cid: str, mid: str) -> bool:
        return any(f["kind"] == "message" and (f["conversation_id"], f["message_id"]) == (cid, mid) and not f["hidden"]
                   for f in self.feed_items.values())

    def _doc_covered_by_message(self, document_id: int) -> bool:
        by_id = {a["id"]: a for a in self.attachments}
        return any(d == document_id and a_id in by_id
                   and self._message_card_visible(by_id[a_id]["conversation_id"], by_id[a_id]["message_id"])
                   for a_id, d, _ in self.links)

    async def feed_candidate_doc_events(self, since: datetime | None, limit: int) -> list[dict]:
        out = []
        for de in self.document_events:
            d = self.documents.get(de["document_id"])
            team = self._team_live(d.get("team_id")) if d else None
            if (d is None or team is None or d.get("is_folder") or not self._standard_channel(d.get("channel_id"))
                    or (since is not None and de["at"] < since) or self._has_item("document", document_event_id=de["id"])):
                continue
            modified_ids = {e["id"] for e in self.document_events
                            if e["document_id"] == de["document_id"] and e["event"] == "modified"}
            times = sorted(f["occurred_at"] for f in self.feed_items.values()
                           if f["kind"] == "document" and f["document_event_id"] in modified_ids)
            out.append({"document_event_id": de["id"], "document_id": de["document_id"], "event": de["event"], "at": de["at"],
                        "team_id": d["team_id"], "created_by_aad": d.get("created_by_aad"),
                        "modified_by_aad": d.get("modified_by_aad"), "modified_by_name": d.get("modified_by_name"),
                        "installed_by_aad": team.get("installed_by_aad"),
                        "covered_by_message": self._doc_covered_by_message(de["document_id"]),
                        "modified_card_times": times})
        out.sort(key=lambda r: (r["at"], r["document_event_id"]))
        return out[:limit]

    async def feed_candidate_changes(self, since: datetime | None, limit: int, types: tuple[str, ...]) -> list[dict]:
        out = []
        for ec in self.event_changes:
            e = self.events.get(ec["event_id"])
            if (e is None or self._team_live(e["team_id"]) is None or ec["change_type"] not in types
                    or (since is not None and ec["at"] < since) or self._has_item("change", event_change_id=ec["id"])):
                continue
            out.append({"event_change_id": ec["id"], "event_id": ec["event_id"], "change_type": ec["change_type"],
                        "at": ec["at"], "team_id": e["team_id"]})
        out.sort(key=lambda r: (r["at"], r["event_change_id"]))
        return out[:limit]

    async def sync_feed_hidden(self) -> int:
        n = 0
        for f in self.feed_items.values():
            m = self.messages.get((f["conversation_id"], f["message_id"])) if f["kind"] == "message" else None
            if m is not None and f["hidden"] != (m.get("deleted_at") is not None):
                f["hidden"] = m.get("deleted_at") is not None
                n += 1
        return n

    async def hide_duplicate_document_cards(self) -> int:
        n = 0
        for f in self.feed_items.values():
            if f["kind"] != "document" or f["hidden"]:
                continue
            de = next(e for e in self.document_events if e["id"] == f["document_event_id"])
            if de["event"] == "created" and self._doc_covered_by_message(de["document_id"]):
                f["hidden"] = True
                n += 1
        return n

    async def link_feed_items_to_events(self) -> int:
        want: set[tuple[int, int]] = set()
        for iid, f in self.feed_items.items():
            if f["kind"] == "message":
                want |= {(iid, l["event_id"]) for k, l in self.message_event_links.items()
                         if k[:2] == (f["conversation_id"], f["message_id"])}
            elif f["kind"] == "change":
                ec = next((c for c in self.event_changes if c["id"] == f["event_change_id"]), None)
                if ec:
                    want.add((iid, ec["event_id"]))
            else:
                de = next(e for e in self.document_events if e["id"] == f["document_event_id"])
                want |= {(iid, eid) for eid, e in self.events.items() if e.get("source_kind") == "document"
                         and str((e.get("source_ref") or {}).get("document_id")) == str(de["document_id"])}
        new = want - self.feed_item_events
        self.feed_item_events |= new
        return len(new)
