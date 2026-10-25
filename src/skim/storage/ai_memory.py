"""Phase 3 storage for MemoryStorage (messages analysis, events, change history). See ai_postgres.py for the SQL twin."""
from __future__ import annotations

from datetime import datetime, timezone

_MIN = datetime.min.replace(tzinfo=timezone.utc)
EVENT_FIELDS = {"type", "title", "is_deadline", "starts_at", "ends_at", "event_date", "time_known", "time_defaulted",
                "venue", "details", "status", "confidence", "needs_review"}


class CharacterNotInRepertoireError(ValueError):
    """What Postgres raises for NUL; the in-memory store raises it too so tests cannot pass with unclean text."""


def _reject_nul(o) -> None:
    if isinstance(o, str):
        if "\x00" in o or any("\ud800" <= c <= "\udfff" for c in o):
            raise CharacterNotInRepertoireError("invalid byte sequence for encoding UTF8")
    elif isinstance(o, dict):
        for k, v in o.items():
            _reject_nul(k), _reject_nul(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            _reject_nul(v)


class MemoryAIMixin:
    def _init_ai(self) -> None:
        self.analyses: dict[tuple, dict] = {}
        self.events: dict[int, dict] = {}
        self.event_changes: list[dict] = []
        self.message_event_links: dict[tuple, dict] = {}
        self._event_seq = 0
        self.document_texts: dict[int, dict] = {}
        self.embeddings: dict[int, dict] = {}
        self.embed_failures: dict[str, dict] = {}
        self.doc_chunks: dict[int, list[dict]] = {}

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
            if parent and parent.get("deleted_at"):
                parent = None  # deleted content never goes to an LLM
            out.append({"conversation_id": cid, "message_id": mid, "version": m["current_version"],
                        "text": m.get("text"), "subject": m.get("subject"), "created_at": m.get("created_at"),
                        "last_edited_at": m.get("last_edited_at"), "from_aad_id": m.get("from_aad_id"),
                        "from_name": m.get("from_name"), "reply_to_id": m.get("reply_to_id"),
                        "mentions": m.get("mentions"), "team_id": team["id"],
                        "installed_by_aad": team.get("installed_by_aad"),
                        "parent_text": parent.get("text") if parent else None,
                        "parent_from_name": parent.get("from_name") if parent else None})
        out.sort(key=lambda r: (r["created_at"] or r["last_edited_at"] or _MIN, r["message_id"]))
        return out[:limit]

    async def list_open_events(self, team_id: str, limit: int = 100) -> list[dict]:
        rows = [dict(e) for e in self.events.values() if e["team_id"] == team_id and e["status"] != "done"]
        rows.sort(key=lambda e: (e["updated_at"], e["id"]), reverse=True)
        return rows[:limit]

    async def list_message_links(self, conversation_id: str, message_id: str) -> list[dict]:
        return [dict(v) for k, v in self.message_event_links.items() if k[:2] == (conversation_id, message_id)]

    async def commit_message_analysis(self, analysis: dict, ops: list[dict], source: dict) -> list[dict]:
        _reject_nul([analysis, ops, source])
        if analysis is not None:  # None for document-sourced events (no message to analyse)
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
                if op["event"]["team_id"] != source["team_id"]:
                    raise ValueError("event team differs from the source team")
                self._event_seq += 1
                eid = self._event_seq
                self.events[eid] = {"id": eid, "source_kind": source["kind"], "source_ref": source["ref"],
                                    "created_at": now, "updated_at": now, **op["event"]}
            else:
                eid = op["event_id"]
                if self.events[eid]["team_id"] != source["team_id"]:
                    raise ValueError("event does not belong to the source's team")
                unknown = set(op["fields"]) - EVENT_FIELDS
                assert not unknown, unknown
                self.events[eid].update(op["fields"], updated_at=now)
            for ch in op["changes"]:
                self.event_changes.append({"id": len(self.event_changes) + 1, "event_id": eid, "source_kind": source["kind"],
                                           "source_ref": source["ref"], "at": source["at"], "recorded_at": now, **ch})
            if source["kind"] == "message" and op.get("link", True):
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

    async def ai_status(self, prompt_version: str, max_attempts: int, embed_model: str | None = None) -> dict:
        by_status: dict[str, int] = {}
        failures: dict[str, int] = {}
        gave_up_msgs = 0
        for a in self.analyses.values():
            by_status[a["status"]] = by_status.get(a["status"], 0) + 1
            if a["status"] == "failed" and a["prompt_version"] == prompt_version:
                failures[a["error_class"] or "unknown"] = failures.get(a["error_class"] or "unknown", 0) + 1
                gave_up_msgs += a["attempts"] >= max_attempts
        msgs = len(await self.list_pending_messages(prompt_version, max_attempts, 10**6))
        docs = len(await self.list_pending_documents(10**6, max_attempts))
        doc_fail: dict[str, int] = {}
        for t in self.document_texts.values():
            if t["status"] in ("error", "failed"):
                doc_fail[t["reason"] or "unknown"] = doc_fail.get(t["reason"] or "unknown", 0) + 1
        emb_fail: dict[str, int] = {}
        for f in self.embed_failures.values():
            emb_fail[f["error_class"] or "unknown"] = emb_fail.get(f["error_class"] or "unknown", 0) + 1
        embs = len(await self.list_unembedded(embed_model, 10**6, max_attempts)) if embed_model else None
        return {"analyses_by_status": by_status, "failures_by_error_class": failures, "messages_pending": msgs,
                "events": len(self.events), "events_needing_review": sum(1 for e in self.events.values() if e["needs_review"]),
                "pending": {"messages": msgs, "documents": docs, "embeddings": embs},
                "gave_up": {"messages": gave_up_msgs,
                            "documents": sum(1 for t in self.document_texts.values() if t["status"] == "error" and t["attempts"] >= max_attempts),
                            "embeddings": sum(1 for f in self.embed_failures.values() if f["attempts"] >= max_attempts)},
                "failed_by_error_class": {"messages": failures, "documents": doc_fail, "embeddings": emb_fail}}

    # ---- documents (FR-306)
    async def list_pending_documents(self, limit: int, max_attempts: int = 3) -> list[dict]:
        """Live pdf/docx/pptx files of non-removed teams whose ctag has no document_texts row yet (oldest first)."""
        out = []
        for d in self.documents.values():
            team = self.teams.get(d.get("team_id") or "")
            if (d["is_folder"] or d["deleted_at"] is not None or team is None or team.get("removed_at")
                    or (d.get("name") or "").rsplit(".", 1)[-1].lower() not in ("pdf", "docx", "pptx")):
                continue
            t = self.document_texts.get(d["id"])
            if t and t["ctag"] == d["ctag"] and not (t["status"] == "error" and t["attempts"] < max_attempts):
                continue
            out.append(dict(d, installed_by_aad=team.get("installed_by_aad")))
        out.sort(key=lambda r: (r["modified_at"] is None, r["modified_at"] or _MIN, r["id"]))
        return out[:limit]

    async def save_document_text(self, document_id: int, ctag, status: str, reason, page_count, char_count,
                                 chunks: list[dict]) -> None:
        """Replace the document's text rows (older ctags' chunks go away)."""
        _reject_nul([reason, chunks])
        self.document_texts[document_id] = {"document_id": document_id, "ctag": ctag, "status": status, "reason": reason,
                                            "page_count": page_count, "char_count": char_count, "attempts": 0,
                                            "extracted_at": datetime.now(timezone.utc)}
        self._chunk_seq = getattr(self, "_chunk_seq", 0)
        self.embeddings = {i: e for i, e in self.embeddings.items()
                           if not (e["kind"] == "doc_chunk" and e["ref"]["document_id"] == document_id)}
        new = []
        for c in chunks:
            self._chunk_seq += 1
            new.append({"id": self._chunk_seq, "document_id": document_id, "ctag": ctag, **c})
        self.doc_chunks[document_id] = new

    async def record_document_error(self, document_id: int, ctag, error_class: str) -> int:
        prev = self.document_texts.get(document_id)
        attempts = prev["attempts"] + 1 if prev and prev["ctag"] == ctag and prev["status"] == "error" else 1
        self.document_texts[document_id] = {"document_id": document_id, "ctag": ctag, "status": "error", "reason": error_class[:80],
                                            "page_count": None, "char_count": None, "attempts": attempts,
                                            "extracted_at": datetime.now(timezone.utc)}
        return attempts

    async def list_doc_chunks(self, document_id: int) -> list[dict]:
        return [dict(c) for c in self.doc_chunks.get(document_id, [])]

    # ---- embeddings (FR-307)
    async def list_unembedded(self, model: str, limit: int, max_attempts: int = 3) -> list[dict]:
        """Current, live messages and current doc chunks without an embedding for `model` (messages first, oldest first)."""
        have = {e["ref_key"] for e in self.embeddings.values()}
        have |= {k for k, f in self.embed_failures.items() if f["attempts"] >= max_attempts}  # given up on
        out = []
        for m in await self.list_pending_messages("\0none", 10**9, 10**9):  # every live channel message (no analysis filter)
            if f"msg:{m['conversation_id']}:{m['message_id']}:v{m['version']}:{model}" not in have:
                out.append({**m, "item": "message"})
        for did, chunks in self.doc_chunks.items():
            d, t = self.documents.get(did), self.document_texts.get(did)
            team = self.teams.get((d or {}).get("team_id") or "")
            if not d or d["deleted_at"] is not None or not team or team.get("removed_at") or not t or t["status"] != "ok":
                continue
            for c in chunks:
                if c["ctag"] == t["ctag"] and f"chunk:{c.get('id', (did, c['chunk_index']))}:{model}" not in have:
                    out.append({**c, "id": c.get("id", (did, c["chunk_index"])), "item": "doc_chunk", "team_id": d["team_id"],
                                "created_by_name": d.get("created_by_name"), "modified_by_name": d.get("modified_by_name")})
        return out[:limit]

    async def record_embed_failures(self, rows: list[dict]) -> None:
        for r in rows:
            f = self.embed_failures.setdefault(f"{r['ref_key']}:{r['model']}", {"attempts": 0, "error_class": None})
            f["attempts"] += 1
            f["error_class"] = r["error_class"][:80]

    async def save_embeddings(self, rows: list[dict]) -> int:
        """rows: kind, ref, ref_key (without model), model, dims, embedding, team_id. Idempotent by ref_key+model."""
        n = 0
        for r in rows:
            key = f"{r['ref_key']}:{r['model']}"
            if key not in {e["ref_key"] for e in self.embeddings.values()}:
                self.embeddings[len(self.embeddings) + 1] = {**r, "ref_key": key}
                n += 1
        return n

    async def search_embeddings(self, vector: list[float], model: str, team_id: str | None, k: int) -> list[dict]:
        import math

        def cos(a, b):
            na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
            return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0

        live_msgs = {(m["conversation_id"], m["message_id"]): m for m in await self.list_pending_messages("\0none", 10**9, 10**9)}
        hits = []
        for e in self.embeddings.values():
            if e["model"] != model or (team_id and e.get("team_id") != team_id):
                continue
            if e["kind"] == "message":
                m = live_msgs.get((e["ref"]["conversation_id"], e["ref"]["message_id"]))
                if not m or m["version"] != e["ref"]["version"]:
                    continue  # edited (stale vector), deleted, or no longer a live channel message
                snippet = (m.get("subject") + "\n" if m.get("subject") else "") + (m.get("text") or "")
            else:
                chunks = self.doc_chunks.get(e["ref"]["document_id"], [])
                c = next((c for c in chunks if c["chunk_index"] == e["ref"]["chunk_index"]), None)
                d = self.documents.get(e["ref"]["document_id"])
                if not c or not d or d["deleted_at"] is not None:
                    continue
                snippet = c["text"]
            hits.append({"kind": e["kind"], "ref": e["ref"], "team_id": e.get("team_id"), "score": round(cos(vector, e["embedding"]), 4),
                         "snippet": snippet[:300]})
        return sorted(hits, key=lambda h: -h["score"])[:k]

    async def count_embeddings(self, model: str) -> int:
        return sum(1 for e in self.embeddings.values() if e["model"] == model)
