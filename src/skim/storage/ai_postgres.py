"""Phase 3 storage for PostgresStorage (twin of ai_memory.py)."""
from __future__ import annotations

from .ai_memory import EVENT_FIELDS

_EVENT_COLS = ("team_id", "type", "title", "is_deadline", "starts_at", "ends_at", "event_date", "time_known",
               "time_defaulted", "venue", "details", "status", "confidence", "needs_review")

_PENDING_SQL = """
select m.conversation_id, m.message_id, m.current_version as version, m.text, m.subject, m.created_at,
       m.last_edited_at, m.from_aad_id, m.from_name, m.reply_to_id, m.mentions, c.team_id, t.installed_by_aad,
       p.text as parent_text
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

    async def commit_message_analysis(self, analysis: dict, ops: list[dict], source: dict) -> list[dict]:
        """One transaction: the analysis row, event creates/updates, their change rows and the message links."""
        assert self.pool
        a = analysis
        results = []
        async with self.pool.acquire() as conn, conn.transaction():
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
