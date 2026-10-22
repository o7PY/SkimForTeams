"""asyncpg Storage for Supabase Postgres."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import asyncpg

from ..normalize import MessageEvent

log = logging.getLogger("skim.storage")


async def _init_conn(conn: asyncpg.Connection) -> None:
    # Store/return jsonb as Python objects, not strings.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


def pool_kwargs(dsn: str) -> dict:
    """Supabase's transaction-mode pooler (port 6543) can't use prepared statements."""
    kw: dict[str, Any] = {"min_size": 1, "max_size": 5, "init": _init_conn}
    if ":6543" in dsn:
        kw["statement_cache_size"] = 0
    return kw


class PostgresStorage:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        self.pool = await asyncpg.create_pool(self.dsn, **pool_kwargs(self.dsn))

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def ping(self) -> bool:
        try:
            assert self.pool
            await self.pool.fetchval("select 1")
            return True
        except Exception:
            return False

    async def insert_raw(self, row: dict) -> tuple[int, bool]:
        assert self.pool
        rid = await self.pool.fetchval(
            """insert into raw_activities
                 (dedupe_key, activity_type, activity_id, event_type, conversation_id, tenant_id, payload)
               values ($1,$2,$3,$4,$5,$6,$7)
               on conflict (dedupe_key) do nothing returning id""",
            row["dedupe_key"], row["activity_type"], row["activity_id"], row["event_type"],
            row["conversation_id"], row["tenant_id"], row["payload"],
        )
        if rid is not None:
            return rid, True
        rid = await self.pool.fetchval("select id from raw_activities where dedupe_key=$1", row["dedupe_key"])
        return rid, False

    async def upsert_team(self, t: dict) -> None:
        assert self.pool
        await self.pool.execute(
            """insert into teams (id, aad_group_id, name, tenant_id) values ($1,$2,$3,$4)
               on conflict (id) do update set
                 aad_group_id = coalesce(excluded.aad_group_id, teams.aad_group_id),
                 name = coalesce(excluded.name, teams.name),
                 tenant_id = coalesce(excluded.tenant_id, teams.tenant_id)""",
            t["id"], t.get("aad_group_id"), t.get("name"), t.get("tenant_id"),
        )

    async def mark_team_installed(self, team_id, pairing_code, by_aad, at) -> None:
        assert self.pool
        await self.upsert_team({"id": team_id})
        # A new install (never installed, or removed since) resets the welcome claim; a retry or
        # add-upgrade on an active install leaves it alone.
        await self.pool.execute(
            """update teams set
                 welcome_sent_at  = case when installed_at is null or removed_at is not null then null else welcome_sent_at end,
                 installed_by_aad = case when installed_at is null or removed_at is not null then $4 else installed_by_aad end,
                 installed_at     = case when installed_at is null or removed_at is not null then $3 else installed_at end,
                 removed_at       = null,
                 pairing_code     = $2
               where id = $1""",
            team_id, pairing_code, at, by_aad,
        )

    async def claim_welcome(self, team_id, at) -> bool:
        assert self.pool
        return await self.pool.fetchval(
            "update teams set welcome_sent_at=$2 where id=$1 and welcome_sent_at is null returning true",
            team_id, at) is True

    async def mark_team_removed(self, team_id, at) -> None:
        assert self.pool
        await self.upsert_team({"id": team_id})
        await self.pool.execute("update teams set removed_at=$2 where id=$1", team_id, at)

    async def upsert_conversation(self, c: dict) -> None:
        assert self.pool
        await self.pool.execute(
            """insert into conversations (id, conversation_type, team_id, channel_id, name, tenant_id)
               values ($1,$2,$3,$4,$5,$6)
               on conflict (id) do update set
                 conversation_type = coalesce(excluded.conversation_type, conversations.conversation_type),
                 team_id = coalesce(excluded.team_id, conversations.team_id),
                 channel_id = coalesce(excluded.channel_id, conversations.channel_id),
                 name = coalesce(excluded.name, conversations.name),
                 tenant_id = coalesce(excluded.tenant_id, conversations.tenant_id)""",
            c["id"], c.get("conversation_type"), c.get("team_id"), c.get("channel_id"), c.get("name"),
            c.get("tenant_id"),
        )

    async def apply_message_event(self, ev: MessageEvent, raw_id: int) -> bool:
        assert self.pool
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """insert into messages (conversation_id, message_id, reply_to_id, from_aad_id, from_name,
                     text, html, importance, mentions, created_at, last_edited_at, deleted_at, raw_activity_id)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                   on conflict do nothing""",
                ev.conversation_id, ev.message_id, ev.reply_to_id, ev.from_aad_id, ev.from_name,
                ev.text, ev.html, ev.importance, ev.mentions,
                ev.at if ev.event == "created" else None,
                ev.at if ev.event in ("edited", "undeleted") else None,
                ev.at if ev.event == "deleted" else None,
                raw_id,
            )
            # Lock the row for the rest of the transaction.
            msg = await conn.fetchrow(
                "select text, html, current_version, raw_activity_id from messages "
                "where conversation_id=$1 and message_id=$2 for update",
                ev.conversation_id, ev.message_id,
            )
            existing_versions = await conn.fetchval(
                "select count(*) from message_versions where conversation_id=$1 and message_id=$2",
                ev.conversation_id, ev.message_id,
            )
            if existing_versions == 0:
                version, known = 1, ev.event == "created"
                v_text, v_html = ev.text, ev.html
            elif ev.event == "created":
                return True
            else:
                version, known = msg["current_version"] + 1, True
                v_text, v_html = (msg["text"], msg["html"]) if ev.event == "deleted" else (ev.text, ev.html)
                if ev.event == "deleted":
                    await conn.execute(
                        "update messages set deleted_at=$3, current_version=$4 where conversation_id=$1 and message_id=$2",
                        ev.conversation_id, ev.message_id, ev.at, version)
                else:
                    await conn.execute(
                        """update messages set text=$3, html=$4, mentions=$5, last_edited_at=$6, current_version=$7,
                             deleted_at = case when $8 then null else deleted_at end
                           where conversation_id=$1 and message_id=$2""",
                        ev.conversation_id, ev.message_id, ev.text, ev.html, ev.mentions, ev.at, version,
                        ev.event == "undeleted")
            await conn.execute(
                """insert into message_versions (conversation_id, message_id, version, text, html, event, at,
                     raw_activity_id) values ($1,$2,$3,$4,$5,$6,$7,$8) on conflict do nothing""",
                ev.conversation_id, ev.message_id, version, v_text, v_html, ev.event, ev.at, raw_id,
            )
            return known

    async def add_attachments(self, rows: list[dict], raw_id: int) -> None:
        assert self.pool
        for r in rows:
            await self.pool.execute(
                """insert into attachments (conversation_id, message_id, content_type, name, content_url,
                     payload, raw_activity_id)
                   select $1::text, $2::text, $3::text, $4::text, $5::text, $6::jsonb, $7::bigint
                   where not exists (select 1 from attachments where conversation_id=$1::text
                                     and message_id=$2::text and payload=$6::jsonb)""",
                r["conversation_id"], r["message_id"], r["content_type"], r["name"], r["content_url"],
                r["payload"], raw_id,
            )

    async def add_reactions(self, rows: list[dict], raw_id: int) -> None:
        assert self.pool
        for r in rows:
            await self.pool.execute(
                """insert into reactions (conversation_id, message_id, user_aad_id, user_name, reaction_type,
                     action, at, raw_activity_id) values ($1,$2,$3,$4,$5,$6,$7,$8)""",
                r["conversation_id"], r["message_id"], r["user_aad_id"], r["user_name"], r["reaction_type"],
                r["action"], r["at"], raw_id,
            )

    # --- read side ---
    async def list_raw(self, limit: int, type: str | None) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            "select * from raw_activities where ($2::text is null or activity_type=$2) order by id desc limit $1",
            limit, type or None)
        return [dict(r) for r in rows]

    async def list_messages(self, limit, team_id, conversation_id) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """select m.*, (select count(*) from message_versions v
                            where v.conversation_id=m.conversation_id and v.message_id=m.message_id) as version_count,
                      (select count(*) from attachments a
                            where a.conversation_id=m.conversation_id and a.message_id=m.message_id) as attachment_count,
                      (m.deleted_at is not null) as deleted
               from messages m left join conversations c on c.id = m.conversation_id
               where ($2::text is null or c.team_id=$2) and ($3::text is null or m.conversation_id=$3)
               order by coalesce(m.created_at, m.last_edited_at, m.deleted_at) desc nulls last limit $1""",
            limit, team_id or None, conversation_id or None)
        return [dict(r) for r in rows]

    async def get_message(self, conversation_id, message_id):
        assert self.pool
        m = await self.pool.fetchrow("select * from messages where conversation_id=$1 and message_id=$2",
                                     conversation_id, message_id)
        if not m:
            return None
        args = (conversation_id, message_id)
        return {
            "message": dict(m),
            "versions": [dict(r) for r in await self.pool.fetch(
                "select * from message_versions where conversation_id=$1 and message_id=$2 order by version", *args)],
            "attachments": [dict(r) for r in await self.pool.fetch(
                "select * from attachments where conversation_id=$1 and message_id=$2 order by id", *args)],
            "reactions": [dict(r) for r in await self.pool.fetch(
                "select * from reactions where conversation_id=$1 and message_id=$2 order by id", *args)],
        }

    async def list_teams(self) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch("select * from teams order by installed_at desc nulls last")]
