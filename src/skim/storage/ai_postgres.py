"""Phase 3 storage for PostgresStorage (twin of ai_memory.py)."""
from __future__ import annotations

from .ai_memory import EVENT_FIELDS

_EVENT_COLS = ("team_id", "type", "title", "is_deadline", "starts_at", "ends_at", "event_date", "time_known",
               "time_defaulted", "venue", "details", "status", "confidence", "needs_review")

_PENDING_DOCS_BODY = """
from documents d
join teams t on t.id = d.team_id and t.removed_at is null
left join document_texts x on x.document_id = d.id
where d.deleted_at is null and not d.is_folder
  and lower(substring(d.name from '[^.]*$')) in ('pdf', 'docx', 'pptx')
  and (x.document_id is null or x.ctag is distinct from d.ctag or (x.status = 'error' and x.attempts < $1))"""
_PENDING_DOCS_SQL = ("select d.*, t.installed_by_aad" + _PENDING_DOCS_BODY
                     + "\norder by d.modified_at asc nulls last, d.id asc\nlimit $2")

_PENDING_BODY = """
from messages m
join conversations c on c.id = m.conversation_id and c.team_id is not null and c.conversation_type = 'channel'
join teams t on t.id = c.team_id and t.removed_at is null
left join channels ch on ch.id = m.conversation_id
left join messages p on p.conversation_id = m.conversation_id and p.message_id = m.reply_to_id
     and p.deleted_at is null
left join message_analysis a on a.conversation_id = m.conversation_id and a.message_id = m.message_id
     and a.message_version = m.current_version and a.prompt_version = $1
where m.deleted_at is null
  and coalesce(ch.membership_type, 'standard') = 'standard'
  and (coalesce(m.text, '') <> '' or coalesce(m.subject, '') <> '')
  and (a.message_id is null or (a.status = 'failed' and a.attempts < $2))"""
_PENDING_SQL = ("""select m.conversation_id, m.message_id, m.current_version as version, m.text, m.subject, m.created_at,
       m.last_edited_at, m.from_aad_id, m.from_name, m.reply_to_id, m.mentions, c.team_id, t.installed_by_aad,
       p.text as parent_text, p.from_name as parent_from_name""" + _PENDING_BODY
                + "\norder by coalesce(m.created_at, m.last_edited_at) asc, m.message_id asc\nlimit $3")


class PostgresAIMixin:
    async def list_pending_messages(self, prompt_version: str, max_attempts: int, limit: int) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(_PENDING_SQL, prompt_version, max_attempts, limit)]

    async def list_open_events(self, team_id: str, limit: int = 100) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from events where team_id=$1 and status <> 'done' "
                                     "order by updated_at desc, id desc limit $2", team_id, limit)
        return [dict(r) for r in rows]

    async def list_message_links(self, conversation_id: str, message_id: str) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from message_event_links where conversation_id=$1 and message_id=$2",
                                     conversation_id, message_id)
        return [dict(r) for r in rows]

    @staticmethod
    async def _save_analysis(conn, a: dict) -> None:
        await conn.execute(
            """insert into message_analysis (conversation_id, message_id, message_version, prompt_version, status,
                 category, confidence, provider, model, output, author_role, needs_review, error_class, attempts)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,1)
               on conflict (conversation_id, message_id, message_version, prompt_version) do update set
                 status=excluded.status, category=excluded.category, confidence=excluded.confidence,
                 provider=excluded.provider, model=excluded.model, output=excluded.output,
                 author_role=excluded.author_role, needs_review=excluded.needs_review,
                 error_class=excluded.error_class, attempts=message_analysis.attempts + 1, processed_at=now()""",
            a["conversation_id"], a["message_id"], a["message_version"], a["prompt_version"], a["status"],
            a.get("category"), a.get("confidence"), a.get("provider"), a.get("model"), a.get("output"),
            a.get("author_role"), bool(a.get("needs_review")), a.get("error_class"))

    async def commit_message_analysis(self, analysis: dict, ops: list[dict], source: dict) -> list[dict]:
        """One transaction: the analysis row, event creates/updates, their change rows and the message links."""
        assert self.pool
        a = analysis
        results = []
        async with self.pool.acquire() as conn, conn.transaction():
            if a is not None:  # None for document-sourced events
                await self._save_analysis(conn, a)
            for op in ops:
                if op["kind"] == "create":
                    ev = op["event"]
                    eid = await conn.fetchval(
                        f"""insert into events ({", ".join(_EVENT_COLS)}, source_kind, source_ref)
                            values ({", ".join(f"${i}" for i in range(1, len(_EVENT_COLS) + 3))}) returning id""",
                        *[ev.get(c) for c in _EVENT_COLS], source["kind"], source["ref"])
                    if ev["team_id"] != source["team_id"]:
                        raise ValueError("event team differs from the source team")
                else:
                    eid = op["event_id"]
                    fields = op["fields"]
                    unknown = set(fields) - EVENT_FIELDS
                    assert not unknown, unknown
                    # An event may only be touched by a source of its own team (P3-D-013), whatever the caller says.
                    if not await conn.fetchval("select 1 from events where id=$1 and team_id=$2", eid, source["team_id"]):
                        raise ValueError("event does not belong to the source's team")
                    if fields:
                        sets = ", ".join(f"{k}=${i}" for i, k in enumerate(fields, 3))
                        await conn.execute(f"update events set {sets}, updated_at=now() where id=$1 and team_id=$2",
                                           eid, source["team_id"], *fields.values())
                for ch in op["changes"]:
                    await conn.execute(
                        """insert into event_changes (event_id, change_type, old_values, new_values, confidence,
                             source_kind, source_ref, at) values ($1,$2,$3,$4,$5,$6,$7,$8)""",
                        eid, ch["change_type"], ch.get("old_values"), ch.get("new_values"), ch["confidence"],
                        source["kind"], source["ref"], source["at"])
                if source["kind"] == "message" and op.get("link", True):
                    await conn.execute(
                        """insert into message_event_links (conversation_id, message_id, event_id, relation)
                           values ($1,$2,$3,$4)
                           on conflict (conversation_id, message_id, event_id) do update set relation =
                             case when message_event_links.relation = 'created' then 'created' else excluded.relation end""",
                        source["ref"]["conversation_id"], source["ref"]["message_id"], eid, op["relation"])
                results.append({"event_id": eid, "relation": op["relation"]})
        return results

    async def list_events(self, team_id=None, status=None, type=None, needs_review=None, limit=100) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """select * from events where ($1::text is null or team_id=$1) and ($2::text is null or status=$2)
               and ($3::text is null or type=$3) and ($4::boolean is null or needs_review=$4)
               order by event_date nulls last, id limit $5""", team_id, status, type, needs_review, limit)
        return [dict(r) for r in rows]

    async def get_event(self, event_id: int) -> dict | None:
        assert self.pool
        e = await self.pool.fetchrow("select * from events where id=$1", event_id)
        if e is None:
            return None
        changes = await self.pool.fetch("select * from event_changes where event_id=$1 order by at, id", event_id)
        links = await self.pool.fetch("select * from message_event_links where event_id=$1", event_id)
        return {"event": dict(e), "changes": [dict(c) for c in changes], "messages": [dict(x) for x in links]}

    async def list_message_analyses(self, conversation_id: str, message_id: str) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from message_analysis where conversation_id=$1 and message_id=$2 "
                                     "order by message_version, prompt_version", conversation_id, message_id)
        return [dict(r) for r in rows]

    async def flag_events_of_deleted_messages(self) -> int:
        assert self.pool
        res = await self.pool.execute(
            """update events e set needs_review = true, updated_at = now()
               where e.source_kind = 'message' and not e.needs_review
                 and exists (select 1 from messages m where m.conversation_id = e.source_ref->>'conversation_id'
                             and m.message_id = e.source_ref->>'message_id' and m.deleted_at is not null)
                 and not exists (select 1 from message_event_links l join messages m2
                                 on m2.conversation_id = l.conversation_id and m2.message_id = l.message_id
                                 where l.event_id = e.id and m2.deleted_at is null)""")
        return int(res.split()[-1])

    async def ai_status(self, prompt_version: str, max_attempts: int, embed_model: str | None = None) -> dict:
        assert self.pool
        p = self.pool
        by_status = {r["status"]: r["n"] for r in await p.fetch(
            "select status, count(*)::int n from message_analysis group by 1")}
        failures = {(r["error_class"] or "unknown"): r["n"] for r in await p.fetch(
            "select error_class, count(*)::int n from message_analysis where status='failed' and prompt_version=$1 group by 1",
            prompt_version)}
        msgs_pending = await p.fetchval("select count(*)::int " + _PENDING_BODY, prompt_version, max_attempts)
        gave_up_msgs = await p.fetchval(
            "select count(*)::int from message_analysis a join messages m on m.conversation_id=a.conversation_id "
            "and m.message_id=a.message_id and m.current_version=a.message_version "
            "where a.status='failed' and a.prompt_version=$1 and a.attempts >= $2 and m.deleted_at is null", prompt_version, max_attempts)
        docs_pending = await p.fetchval("select count(*)::int " + _PENDING_DOCS_BODY, max_attempts)
        doc_fail = {(r["reason"] or "unknown"): r["n"] for r in await p.fetch(
            "select reason, count(*)::int n from document_texts where status in ('error', 'failed') group by 1")}
        doc_gave_up = await p.fetchval("select count(*)::int from document_texts where status='error' and attempts >= $1", max_attempts)
        emb_pending = await self._count_unembedded(embed_model, max_attempts) if embed_model else None
        emb_fail = {(r["error_class"] or "unknown"): r["n"] for r in await p.fetch(
            "select error_class, count(*)::int n from embed_failures group by 1")}
        emb_gave_up = await p.fetchval("select count(*)::int from embed_failures where attempts >= $1", max_attempts)
        ev = await p.fetchrow("select count(*)::int n, count(*) filter (where needs_review)::int r from events")
        return {"analyses_by_status": by_status, "failures_by_error_class": failures, "messages_pending": msgs_pending,
                "events": ev["n"], "events_needing_review": ev["r"],
                "pending": {"messages": msgs_pending, "documents": docs_pending, "embeddings": emb_pending},
                "gave_up": {"messages": gave_up_msgs, "documents": doc_gave_up, "embeddings": emb_gave_up},
                "failed_by_error_class": {"messages": failures, "documents": doc_fail, "embeddings": emb_fail}}

    async def _count_unembedded(self, model: str, max_attempts: int) -> int:
        a = await self.pool.fetchval("select count(*)::int " + _UNEMBEDDED_MESSAGES_BODY, model, max_attempts)
        b = await self.pool.fetchval("select count(*)::int " + _UNEMBEDDED_CHUNKS_BODY, model, max_attempts)
        return a + b

    # ---- documents (FR-306)
    async def list_pending_documents(self, limit: int, max_attempts: int = 3) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(_PENDING_DOCS_SQL, max_attempts, limit)]

    async def record_document_error(self, document_id: int, ctag, error_class: str) -> int:
        """An unexpected failure while processing a file counts one attempt (a changed ctag starts again at 1)."""
        assert self.pool
        return await self.pool.fetchval(
            """insert into document_texts (document_id, ctag, status, reason, attempts) values ($1,$2,'error',$3,1)
               on conflict (document_id) do update set
                 attempts = case when document_texts.ctag is not distinct from excluded.ctag then document_texts.attempts + 1 else 1 end,
                 ctag = excluded.ctag, status = 'error', reason = excluded.reason, extracted_at = now()
               returning attempts""", document_id, ctag, error_class[:80])

    async def save_document_text(self, document_id: int, ctag, status: str, reason, page_count, char_count,
                                 chunks: list[dict]) -> None:
        """One transaction: the document_texts row, and the chunk set replaced (older ctags' chunks are deleted)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("delete from embeddings where kind='doc_chunk' and ref->>'document_id' = $1", str(document_id))
            await conn.execute("delete from doc_chunks where document_id=$1", document_id)
            await conn.execute(
                """insert into document_texts (document_id, ctag, status, reason, page_count, char_count, attempts)
                   values ($1,$2,$3,$4,$5,$6,0)
                   on conflict (document_id) do update set ctag=excluded.ctag, status=excluded.status,
                     reason=excluded.reason, page_count=excluded.page_count, char_count=excluded.char_count,
                     attempts=0, extracted_at=now()""", document_id, ctag, status, reason, page_count, char_count)
            if chunks:
                await conn.executemany(
                    "insert into doc_chunks (document_id, ctag, chunk_index, page_from, page_to, text) values ($1,$2,$3,$4,$5,$6)",
                    [(document_id, ctag, c["chunk_index"], c.get("page_from"), c.get("page_to"), c["text"]) for c in chunks])

    async def list_doc_chunks(self, document_id: int) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(
            "select * from doc_chunks where document_id=$1 order by chunk_index", document_id)]

    # ---- embeddings (FR-307)
    async def list_unembedded(self, model: str, limit: int, max_attempts: int = 3) -> list[dict]:
        assert self.pool
        msgs = await self.pool.fetch(_UNEMBEDDED_MESSAGES_SQL, model, max_attempts, limit)
        out = [{**dict(r), "item": "message"} for r in msgs]
        if len(out) < limit:
            chunks = await self.pool.fetch(_UNEMBEDDED_CHUNKS_SQL, model, max_attempts, limit - len(out))
            out += [{**dict(r), "item": "doc_chunk"} for r in chunks]
        return out

    async def record_embed_failures(self, rows: list[dict]) -> None:
        """rows: {ref_key (without model), model, error_class}. One attempt each; the item is skipped after AI_MAX_ATTEMPTS."""
        assert self.pool
        for r in rows:
            await self.pool.execute(
                """insert into embed_failures (ref_key, model, attempts, error_class) values ($1,$2,1,$3)
                   on conflict (ref_key) do update set attempts = embed_failures.attempts + 1,
                     error_class = excluded.error_class, updated_at = now()""",
                f"{r['ref_key']}:{r['model']}", r["model"], r["error_class"][:80])

    async def save_embeddings(self, rows: list[dict]) -> int:
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            n = 0
            for r in rows:
                tag = await conn.execute(
                    """insert into embeddings (kind, ref, ref_key, model, dims, embedding)
                       values ($1,$2,$3,$4,$5,$6::text::vector) on conflict (ref_key) do nothing""",
                    r["kind"], r["ref"], f"{r['ref_key']}:{r['model']}", r["model"], r["dims"], _vec(r["embedding"]))
                n += tag.endswith(" 1")
            return n

    async def search_embeddings(self, vector: list[float], model: str, team_id: str | None, k: int) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(_SEARCH_SQL, _vec(vector), model, team_id, k)
        return [{"kind": r["kind"], "ref": r["ref"], "team_id": r["team_id"], "score": round(float(r["score"]), 4),
                 "snippet": (r["snippet"] or "")[:300]} for r in rows]

    async def count_embeddings(self, model: str) -> int:
        assert self.pool
        return await self.pool.fetchval("select count(*) from embeddings where model=$1", model)

    # ---- reset (P3-DIFF-018)
    async def reset_ai_data(self, team_id: str, dry_run: bool = True) -> dict:
        """Delete everything the AI derived for ONE team, in one transaction (see ai_memory.reset_ai_data)."""
        assert self.pool
        ev = "select id from events where team_id=$1"
        async with self.pool.acquire() as conn, conn.transaction():
            counts = {
                "events": await conn.fetchval("select count(*)::int from events where team_id=$1", team_id),
                "event_changes": await conn.fetchval(f"select count(*)::int from event_changes where event_id in ({ev})", team_id),
                "message_event_links": await conn.fetchval(f"select count(*)::int from message_event_links where event_id in ({ev})", team_id),
                "message_analysis": await conn.fetchval(
                    "select count(*)::int from message_analysis a join conversations c on c.id=a.conversation_id where c.team_id=$1", team_id),
                "document_texts": await conn.fetchval(
                    "select count(*)::int from document_texts x join documents d on d.id=x.document_id where d.team_id=$1", team_id)}
            if not dry_run:
                # Phase 4 rows that point at the events/changes about to go (P4: reminders anchored to an event are
                # dropped, change cards go with their user state; message/document cards stay and are re-linked).
                chg = f"select id from event_changes where event_id in ({ev})"
                await conn.execute(f"delete from push_outbox where event_change_id in ({chg})", team_id)
                await conn.execute(f"delete from feed_items where kind='change' and event_change_id in ({chg})", team_id)
                await conn.execute(f"delete from feed_item_events where event_id in ({ev})", team_id)
                await conn.execute(f"delete from reminders where event_id in ({ev})", team_id)
                await conn.execute(f"delete from message_event_links where event_id in ({ev})", team_id)
                await conn.execute(f"delete from event_changes where event_id in ({ev})", team_id)
                await conn.execute("delete from events where team_id=$1", team_id)
                await conn.execute("delete from message_analysis a using conversations c where c.id=a.conversation_id and c.team_id=$1", team_id)
                await conn.execute("delete from document_texts x using documents d where d.id=x.document_id and d.team_id=$1", team_id)
        return counts


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


_UNEMBEDDED_MESSAGES_BODY = """
from messages m
join conversations c on c.id = m.conversation_id and c.team_id is not null and c.conversation_type = 'channel'
join teams t on t.id = c.team_id and t.removed_at is null
left join channels ch on ch.id = m.conversation_id
where m.deleted_at is null and coalesce(ch.membership_type, 'standard') = 'standard'
  and (coalesce(m.text, '') <> '' or coalesce(m.subject, '') <> '')
  and not exists (select 1 from embeddings e where e.ref_key =
        'msg:' || m.conversation_id || ':' || m.message_id || ':v' || m.current_version || ':' || $1)
  and not exists (select 1 from embed_failures f where f.attempts >= $2 and f.ref_key =
        'msg:' || m.conversation_id || ':' || m.message_id || ':v' || m.current_version || ':' || $1)"""
_UNEMBEDDED_MESSAGES_SQL = ("select m.conversation_id, m.message_id, m.current_version as version, m.text, m.subject, m.from_name, "
                            "m.mentions, c.team_id" + _UNEMBEDDED_MESSAGES_BODY
                            + "\norder by coalesce(m.created_at, m.last_edited_at) asc, m.message_id asc\nlimit $3")

_UNEMBEDDED_CHUNKS_BODY = """
from doc_chunks k
join documents d on d.id = k.document_id and d.deleted_at is null
join teams t on t.id = d.team_id and t.removed_at is null
join document_texts x on x.document_id = d.id and x.status = 'ok' and x.ctag is not distinct from k.ctag
where not exists (select 1 from embeddings e where e.ref_key = 'chunk:' || k.id || ':' || $1)
  and not exists (select 1 from embed_failures f where f.attempts >= $2 and f.ref_key = 'chunk:' || k.id || ':' || $1)"""
_UNEMBEDDED_CHUNKS_SQL = ("select k.id, k.document_id, k.chunk_index, k.text, d.team_id, d.created_by_name, d.modified_by_name"
                          + _UNEMBEDDED_CHUNKS_BODY + "\norder by k.id asc\nlimit $3")

# Only CURRENT versions of live messages and chunks of live documents of active teams; optional team filter.
_SEARCH_SQL = """
select * from (
  select 'message' as kind, e.ref, c.team_id, coalesce(m.subject || E'\\n', '') || coalesce(m.text, '') as snippet,
         1 - (e.embedding <=> $1::text::vector) as score
  from embeddings e
  join messages m on m.conversation_id = e.ref->>'conversation_id' and m.message_id = e.ref->>'message_id'
       and m.current_version = (e.ref->>'version')::int and m.deleted_at is null
  join conversations c on c.id = m.conversation_id and c.team_id is not null and c.conversation_type = 'channel'
  join teams t on t.id = c.team_id and t.removed_at is null
  left join channels ch on ch.id = m.conversation_id
  where e.kind = 'message' and e.model = $2 and ($3::text is null or c.team_id = $3)
    and coalesce(ch.membership_type, 'standard') = 'standard'
  union all
  select 'doc_chunk', e.ref, d.team_id, k.text, 1 - (e.embedding <=> $1::text::vector)
  from embeddings e
  join doc_chunks k on k.id = (e.ref->>'chunk_id')::bigint
  join documents d on d.id = k.document_id and d.deleted_at is null
  join teams t on t.id = d.team_id and t.removed_at is null
  where e.kind = 'doc_chunk' and e.model = $2 and ($3::text is null or d.team_id = $3)
) s order by score desc limit $4"""
