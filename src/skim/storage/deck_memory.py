"""Phase 4 read side for MemoryStorage (twin of deck_postgres.py; keep the two in step)."""
from __future__ import annotations

from datetime import datetime, timedelta


class MemoryDeckMixin:
    def _active_teams(self, user_id: str) -> dict[str, dict]:
        return {tid: s for (uid, tid), s in self.subscriptions.items() if uid == user_id and s["left_at"] is None}

    async def deck_candidates(self, user_id: str, now: datetime, backlog_days: int, limit: int) -> list[dict]:
        subs = self._active_teams(user_id)
        out = []
        for iid, f in self.feed_items.items():
            sub = subs.get(f["team_id"])
            if sub is None or f["hidden"]:
                continue
            st = self.user_item_state.get((user_id, iid))
            if st is not None and not (st["state"] is None and st["snoozed_until"] <= now):
                continue
            in_window = f["occurred_at"] >= sub["joined_at"] - timedelta(days=backlog_days)
            event_ids = sorted(e for i, e in self.feed_item_events if i == iid)
            if st is None and not (in_window or event_ids):
                continue
            out.append({"id": iid, "team_id": f["team_id"], "kind": f["kind"], "occurred_at": f["occurred_at"],
                        "snoozed_until": st["snoozed_until"] if st else None, "returned": st is not None,
                        "in_window": in_window, "event_ids": event_ids})
        epoch = datetime.min.replace(tzinfo=now.tzinfo)
        out.sort(key=lambda r: (r["returned"], r["snoozed_until"] or epoch, r["occurred_at"], r["id"]), reverse=True)
        return out[:limit]

    async def accessible_item_ids(self, user_id: str, ids: list[int]) -> set[int]:
        subs = self._active_teams(user_id)
        return {i for i in ids if i in self.feed_items and self.feed_items[i]["team_id"] in subs}

    async def get_feed_items(self, ids: list[int]) -> dict[int, dict]:
        return {i: {**self.feed_items[i], "event_ids": sorted(e for j, e in self.feed_item_events if j == i)}
                for i in ids if i in self.feed_items}

    async def get_events_by_ids(self, ids: list[int]) -> dict[int, dict]:
        return {i: dict(self.events[i]) for i in ids if i in self.events}

    async def get_messages(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        keep = ("conversation_id", "message_id", "reply_to_id", "from_aad_id", "from_name", "text", "subject", "created_at",
                "last_edited_at", "deleted_at", "current_version")
        return {p: {k: self.messages[p].get(k) for k in keep} for p in pairs if p in self.messages}

    async def get_message_files(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
        out: dict[tuple[str, str], list[dict]] = {}
        wanted = set(pairs)
        for a in self.attachments:
            key = (a["conversation_id"], a["message_id"])
            if key not in wanted:
                continue
            docs = [self.documents[d] for aid, d, _ in sorted(self.links) if aid == a["id"] and d in self.documents] or [None]
            for d in docs:
                if a.get("content_type") == "reference" or d is not None:
                    out.setdefault(key, []).append({
                        "conversation_id": key[0], "message_id": key[1], "name": a.get("name"), "content_url": a.get("content_url"),
                        "document_id": d["id"] if d else None, "document_name": d.get("name") if d else None,
                        "document_url": d.get("web_url") if d else None})
        return out

    async def get_document_cards(self, document_event_ids: list[int]) -> dict[int, dict]:
        out = {}
        for de in self.document_events:
            d = self.documents.get(de["document_id"])
            if de["id"] in document_event_ids and d is not None:
                out[de["id"]] = {
                    **{k: de.get(k) for k in ("id", "document_id", "event", "at", "old_name", "new_name", "old_path", "new_path")},
                    **{k: d.get(k) for k in ("team_id", "name", "path", "web_url", "created_by_aad", "created_by_name",
                                             "modified_by_aad", "modified_by_name")},
                    "installed_by_aad": (self.teams.get(d.get("team_id")) or {}).get("installed_by_aad")}
        return out

    async def get_event_changes_by_ids(self, ids: list[int]) -> dict[int, dict]:
        return {c["id"]: dict(c) for c in self.event_changes if c["id"] in ids}

    async def list_event_history(self, event_ids: list[int]) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for c in sorted(self.event_changes, key=lambda c: (c["at"], c["id"]), reverse=True):
            if c["event_id"] in event_ids:
                out.setdefault(c["event_id"], []).append(dict(c))
        return out

    async def get_stars(self, user_id: str, item_ids: list[int]) -> dict[int, dict]:
        return {i: dict(self.stars[(user_id, i)]) for i in item_ids if (user_id, i) in self.stars}

    async def list_history(self, user_id: str, limit: int) -> list[dict]:
        subs = self._active_teams(user_id)
        rows = [{"item_id": i, "state": st["state"], "acted_at": st["acted_at"]}
                for (uid, i), st in self.user_item_state.items()
                if uid == user_id and st["state"] is not None and st["folded_into"] is None
                and i in self.feed_items and self.feed_items[i]["team_id"] in subs]
        rows.sort(key=lambda r: (r["acted_at"], r["item_id"]), reverse=True)
        return rows[:limit]

    async def folded_children(self, user_id: str, parent_ids: list[int]) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for (uid, i), st in sorted(self.user_item_state.items(), key=lambda kv: kv[0][1]):
            if uid == user_id and st["folded_into"] in parent_ids:
                out.setdefault(st["folded_into"], []).append(i)
        return out

    async def list_events_for_user(self, user_id: str, since_date) -> dict[int, dict]:
        subs = self._active_teams(user_id)
        return {i: dict(e) for i, e in self.events.items()
                if e["team_id"] in subs and e.get("event_date") is not None and e["event_date"] >= since_date}

    async def latest_items_for_events(self, user_id: str, event_ids: list[int]) -> dict[int, int]:
        subs = self._active_teams(user_id)
        best: dict[int, tuple] = {}
        for iid, eid in self.feed_item_events:
            f = self.feed_items.get(iid)
            if eid in event_ids and f and not f["hidden"] and f["team_id"] in subs:
                k = (f["occurred_at"], iid)
                if eid not in best or k > best[eid]:
                    best[eid] = k
        return {e: k[1] for e, k in best.items()}
