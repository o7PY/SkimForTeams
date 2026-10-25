"""Phase 3 storage for PostgresStorage (twin of ai_memory.py)."""
from __future__ import annotations

from .ai_memory import EVENT_FIELDS

_EVENT_COLS = ("team_id", "type", "title", "is_deadline", "starts_at", "ends_at", "event_date", "time_known",
               "time_defaulted", "venue", "details", "status", "confidence", "needs_review")

_PENDING_DOCS_SQL = """
select d.*, t.installed_by_aad
from documents d
join teams t on t.id = d.team_id and t.removed_at is null
left join document_texts x on x.document_id = d.id
where d.deleted_at is null and not d.is_folder
  and lower(substring(d.name from '[^.]*$')) in ('pdf', 'docx', 'pptx')
  and (x.document_id is null or x.ctag is distinct from d.ctag)
order by d.modified_at asc nulls last, d.id asc
limit $1"""

_PENDING_SQL = """
select m.conversation_id, m.message_id, m.current_version as version, m.text, m.subject, m.created_at,
       m.last_edited_at, m.from_aad_id, m.from_name, m.reply_to_id, m.mentions, c.team_id, t.installed_by_aad,
       p.text as parent_text, p.from_name as parent_from_name
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
  and (a.message_id is null or (a.status = 'failed' and a.attempts < $2))
order by coalesce(m.created_at, m.last_edited_at) asc, m.message_id asc
limit $3"""


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
                if source["kind"] == "message":
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

    async def ai_status(self, prompt_version: str, max_attempts: int) -> dict:
        assert self.pool
        by_status = {r["status"]: r["n"] for r in await self.pool.fetch(
            "select status, count(*)::int n from message_analysis group by 1")}
        failures = {(r["error_class"] or "unknown"): r["n"] for r in await self.pool.fetch(
            "select error_class, count(*)::int n from message_analysis where status='failed' group by 1")}
        pending = len(await self.list_pending_messages(prompt_version, max_attempts, 10**6))
        ev = await self.pool.fetchrow("select count(*)::int n, count(*) filter (where needs_review)::int r from events")
        return {"analyses_by_status": by_status, "failures_by_error_class": failures, "messages_pending": pending,
                "events": ev["n"], "events_needing_review": ev["r"]}

    # ---- documents (FR-306)
    async def list_pending_documents(self, limit: int) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(_PENDING_DOCS_SQL, limit)]

    async def save_document_text(self, document_id: int, ctag, status: str, reason, page_count, char_count,
                                 chunks: list[dict]) -> None:
        """One transaction: the document_texts row, and the chunk set replaced (older ctags' chunks are deleted)."""
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("delete from embeddings where kind='doc_chunk' and ref->>'document_id' = $1", str(document_id))
            await conn.execute("delete from doc_chunks where document_id=$1", document_id)
            await conn.execute(
                """insert into document_texts (document_id, ctag, status, reason, page_count, char_count)
                   values ($1,$2,$3,$4,$5,$6)
                   on conflict (document_id) do update set ctag=excluded.ctag, status=excluded.status,
                     reason=excluded.reason, page_count=excluded.page_count, char_count=excluded.char_count,
                     extracted_at=now()""", document_id, ctag, status, reason, page_count, char_count)
            if chunks:
                await conn.executemany(
                    "insert into doc_chunks (document_id, ctag, chunk_index, page_from, page_to, text) values ($1,$2,$3,$4,$5,$6)",
                    [(document_id, ctag, c["chunk_index"], c.get("page_from"), c.get("page_to"), c["text"]) for c in chunks])

    async def list_doc_chunks(self, document_id: int) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(
            "select * from doc_chunks where document_id=$1 order by chunk_index", document_id)]

    # ---- embeddings (FR-307)
    async def list_unembedded(self, model: str, limit: int) -> list[dict]:
        assert self.pool
        msgs = await self.pool.fetch(_UNEMBEDDED_MESSAGES_SQL, model, limit)
        out = [{**dict(r), "item": "message"} for r in msgs]
        if len(out) < limit:
            chunks = await self.pool.fetch(_UNEMBEDDED_CHUNKS_SQL, model, limit - len(out))
            out += [{**dict(r), "item": "doc_chunk"} for r in chunks]
        return out

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


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


_UNEMBEDDED_MESSAGES_SQL = """
select m.conversation_id, m.message_id, m.current_version as version, m.text, m.subject, m.from_name, m.mentions, c.team_id
from messages m
join conversations c on c.id = m.conversation_id and c.team_id is not null and c.conversation_type = 'channel'
join teams t on t.id = c.team_id and t.removed_at is null
left join channels ch on ch.id = m.conversation_id
where m.deleted_at is null and coalesce(ch.membership_type, 'standard') = 'standard'
  and (coalesce(m.text, '') <> '' or coalesce(m.subject, '') <> '')
  and not exists (select 1 from embeddings e where e.ref_key =
        'msg:' || m.conversation_id || ':' || m.message_id || ':v' || m.current_version || ':' || $1)
order by coalesce(m.created_at, m.last_edited_at) asc, m.message_id asc
limit $2"""

_UNEMBEDDED_CHUNKS_SQL = """
select k.id, k.document_id, k.chunk_index, k.text, d.team_id, d.created_by_name, d.modified_by_name
from doc_chunks k
join documents d on d.id = k.document_id and d.deleted_at is null
join teams t on t.id = d.team_id and t.removed_at is null
join document_texts x on x.document_id = d.id and x.status = 'ok' and x.ctag is not distinct from k.ctag
where not exists (select 1 from embeddings e where e.ref_key = 'chunk:' || k.id || ':' || $1)
order by k.id asc
limit $2"""

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
