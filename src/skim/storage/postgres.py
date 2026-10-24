"""asyncpg Storage for Supabase Postgres."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
import logging
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from ..normalize import MessageEvent
from ..normalize_graph import fill_empty

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

    async def applied_migrations(self) -> set[str] | None:
        assert self.pool
        if not await self.pool.fetchval("select to_regclass('public.schema_migrations') is not null"):
            return set()
        return {r["version"] for r in await self.pool.fetch("select version from schema_migrations")}

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
                 needs_initial_sync = case when installed_at is null or removed_at is not null then true else needs_initial_sync end,
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

    # ------------------------------------------------------------------ Phase 2
    _TEAM_FIELDS = {"needs_initial_sync", "last_discovered_at", "last_files_sync_at", "last_backfill_at"}
    _CHANNEL_FIELDS = {"display_name", "membership_type", "files_drive_id", "files_folder_item_id",
                       "files_folder_path", "last_synced_at", "sync_error"}
    _DOC_COLS = ("drive_id", "item_id", "team_id", "channel_id", "name", "parent_item_id", "path", "is_folder",
                 "size_bytes", "mime_type", "web_url", "web_url_norm", "etag", "ctag", "created_at", "created_by_aad",
                 "created_by_name", "modified_at", "modified_by_aad", "modified_by_name", "deleted_at", "graph_raw_id")

    async def _update(self, table: str, key_col: str, key: Any, fields: dict, allowed: set[str]) -> None:
        assert self.pool
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {table}.{sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{c}=${i + 2}" for i, c in enumerate(fields))
        await self.pool.execute(f"update {table} set {sets} where {key_col}=$1", key, *fields.values())

    async def insert_graph_raw(self, row: dict) -> tuple[int, bool]:
        assert self.pool
        rid = await self.pool.fetchval(
            """insert into graph_raw (kind, endpoint, item_key, dedupe_key, payload) values ($1,$2,$3,$4,$5)
               on conflict (dedupe_key) do nothing returning id""",
            row["kind"], row["endpoint"], row.get("item_key"), row["dedupe_key"], row["payload"])
        if rid is not None:
            return rid, True
        return await self.pool.fetchval("select id from graph_raw where dedupe_key=$1", row["dedupe_key"]), False

    async def list_sync_teams(self) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(
            "select * from teams where aad_group_id is not null and removed_at is null order by installed_at nulls last, id")]

    async def update_team(self, team_id: str, **fields: Any) -> None:
        await self._update("teams", "id", team_id, fields, self._TEAM_FIELDS)

    async def upsert_channel(self, ch: dict) -> None:
        assert self.pool
        await self.pool.execute(
            """insert into channels (id, team_id, display_name, membership_type, files_drive_id,
                                     files_folder_item_id, files_folder_path)
               values ($1,$2,$3,$4,$5,$6,$7)
               on conflict (id) do update set
                 team_id = excluded.team_id,
                 display_name = coalesce(excluded.display_name, channels.display_name),
                 membership_type = coalesce(excluded.membership_type, channels.membership_type),
                 files_drive_id = coalesce(excluded.files_drive_id, channels.files_drive_id),
                 files_folder_item_id = coalesce(excluded.files_folder_item_id, channels.files_folder_item_id),
                 files_folder_path = coalesce(excluded.files_folder_path, channels.files_folder_path)""",
            ch["id"], ch["team_id"], ch.get("display_name"), ch.get("membership_type"), ch.get("files_drive_id"),
            ch.get("files_folder_item_id"), ch.get("files_folder_path"))

    async def update_channel(self, channel_id: str, **fields: Any) -> None:
        await self._update("channels", "id", channel_id, fields, self._CHANNEL_FIELDS)

    async def list_channels(self, team_id: str | None = None) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            "select * from channels where ($1::text is null or team_id=$1) order by team_id, display_name, id", team_id)
        return [dict(r) for r in rows]

    async def get_document(self, drive_id: str, item_id: str) -> dict | None:
        assert self.pool
        r = await self.pool.fetchrow("select * from documents where drive_id=$1 and item_id=$2", drive_id, item_id)
        return dict(r) if r else None

    async def get_document_by_id(self, document_id: int) -> dict | None:
        assert self.pool
        r = await self.pool.fetchrow("select * from documents where id=$1", document_id)
        return dict(r) if r else None

    async def upsert_document(self, doc: dict) -> int:
        """`last_seen_at` may be supplied (the sync's own clock); default is the DB's now()."""
        assert self.pool
        cols = self._DOC_COLS
        values = [doc.get(c) for c in cols] + [doc.get("last_seen_at")]
        values[cols.index("is_folder")] = bool(doc.get("is_folder"))
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("drive_id", "item_id"))
        marks = ", ".join(f"${i + 1}" for i in range(len(cols))) + f", coalesce(${len(cols) + 1}, now())"
        return await self.pool.fetchval(
            f"""insert into documents ({", ".join(cols)}, last_seen_at) values ({marks})
                on conflict (drive_id, item_id) do update set {updates}, last_seen_at = excluded.last_seen_at
                returning id""", *values)

    async def list_documents(self, team_id=None, channel_id=None, include_deleted=False, limit=100) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """select * from documents
               where ($1::text is null or team_id=$1) and ($2::text is null or channel_id=$2)
                 and ($3 or deleted_at is null)
               order by channel_id, path limit $4""", team_id, channel_id, include_deleted, limit)
        return [dict(r) for r in rows]

    async def list_child_documents(self, drive_id: str, parent_item_id: str) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(
            "select * from documents where drive_id=$1 and parent_item_id=$2 order by id", drive_id, parent_item_id)]

    async def soft_delete_unseen(self, channel_id: str, seen_since: datetime, at: datetime) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """update documents set deleted_at=$3
               where channel_id=$1 and deleted_at is null and last_seen_at < $2 returning *""",
            channel_id, seen_since, at)
        return [dict(r) for r in rows]

    async def add_document_event(self, ev: dict) -> int:
        assert self.pool
        return await self.pool.fetchval(
            """insert into document_events (document_id, event, at, old_name, new_name, old_path, new_path, graph_raw_id)
               values ($1,$2,$3,$4,$5,$6,$7,$8) returning id""",
            ev["document_id"], ev["event"], ev["at"], ev.get("old_name"), ev.get("new_name"),
            ev.get("old_path"), ev.get("new_path"), ev.get("graph_raw_id"))

    async def list_document_events(self, document_id: int) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(
            "select * from document_events where document_id=$1 order by at, id", document_id)]

    async def get_sync_state(self, key: str) -> dict | None:
        assert self.pool
        r = await self.pool.fetchrow("select * from sync_state where key=$1", key)
        return dict(r) if r else None

    async def put_sync_state(self, key, *, delta_link=None, cursor=None, last_status=None, last_error=None) -> None:
        assert self.pool
        await self.pool.execute(
            """insert into sync_state (key, delta_link, cursor, last_status, last_error) values ($1,$2,$3,$4,$5)
               on conflict (key) do update set delta_link=excluded.delta_link, cursor=excluded.cursor,
                 last_status=excluded.last_status, last_error=excluded.last_error, updated_at=now()""",
            key, delta_link, cursor, last_status, last_error)

    async def delete_sync_state(self, key: str) -> None:
        assert self.pool
        await self.pool.execute("delete from sync_state where key=$1", key)

    async def list_sync_state(self) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch("select * from sync_state order by key")]

    async def start_job_run(self, job: str) -> int:
        assert self.pool
        return await self.pool.fetchval("insert into job_runs (job) values ($1) returning id", job)

    async def finish_job_run(self, run_id: int, status: str, stats: dict, error: str | None = None) -> None:
        assert self.pool
        await self.pool.execute(
            "update job_runs set finished_at=now(), status=$2, stats=$3, error=$4 where id=$1", run_id, status, stats, error)

    async def latest_job_runs(self) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(
            "select distinct on (job) * from job_runs order by job, started_at desc, id desc")]

    @asynccontextmanager
    async def job_lock(self, name: str, lease_seconds: float = 300.0):
        """Lease row in `job_locks`: one atomic upsert that only wins if the row is missing or expired.

        No connection or transaction is held while the job runs, so it is safe behind Supabase's transaction
        pooler (:6543). Released by deleting our own row; a crashed holder is superseded once the lease expires."""
        assert self.pool
        holder = uuid.uuid4().hex
        got = await self.pool.fetchval(
            """insert into job_locks (name, holder, expires_at) values ($1, $2, now() + make_interval(secs => $3))
               on conflict (name) do update set holder = excluded.holder, expires_at = excluded.expires_at
               where job_locks.expires_at <= now()
               returning holder""", name, holder, float(lease_seconds))
        try:
            yield got == holder
        finally:
            if got == holder:
                await self.pool.execute("delete from job_locks where name=$1 and holder=$2", name, holder)

    async def backfill_message(self, message, attachments, reactions) -> str:
        assert self.pool
        cid, mid = message["conversation_id"], message["message_id"]
        async with self.pool.acquire() as conn, conn.transaction():
            inserted = await conn.fetchval(
                """insert into messages (conversation_id, message_id, reply_to_id, from_aad_id, from_name, text, html,
                     importance, mentions, subject, created_at, last_edited_at, deleted_at, source, backfilled_at)
                   values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,'graph',now())
                   on conflict do nothing returning true""",
                cid, mid, message["reply_to_id"], message["from_aad_id"], message["from_name"], message["text"],
                message["html"], message["importance"], message["mentions"], message["subject"],
                message["created_at"], message["last_edited_at"], message["deleted_at"])
            if inserted:
                await conn.execute(
                    """insert into message_versions (conversation_id, message_id, version, text, html, event, at)
                       values ($1,$2,1,$3,$4,'created',coalesce($5, now()))""",
                    cid, mid, message["text"], message["html"], message["created_at"])
                result = "created"
            else:
                before = dict(await conn.fetchrow(
                    "select * from messages where conversation_id=$1 and message_id=$2 for update", cid, mid))
                fills = fill_empty(before, message)
                result = "unchanged"
                if fills:
                    sets = ", ".join(f"{k}=${i + 3}" for i, k in enumerate(fills))
                    await conn.execute(f"update messages set {sets} where conversation_id=$1 and message_id=$2",
                                       cid, mid, *fills.values())
                    result = "filled"
            have_att = await conn.fetchval(
                "select exists(select 1 from attachments where conversation_id=$1 and message_id=$2)", cid, mid)
            have_rx = await conn.fetchval(
                "select exists(select 1 from reactions where conversation_id=$1 and message_id=$2)", cid, mid)
            if attachments and not have_att:
                for r in attachments:
                    await conn.execute(
                        """insert into attachments (conversation_id, message_id, content_type, name, content_url, payload)
                           values ($1,$2,$3,$4,$5,$6)""", cid, mid, r["content_type"], r["name"], r["content_url"], r["payload"])
                result = result if result == "created" else "filled"
            if reactions and not have_rx:
                for r in reactions:
                    await conn.execute(
                        """insert into reactions (conversation_id, message_id, user_aad_id, user_name, reaction_type, action, at)
                           values ($1,$2,$3,$4,$5,$6,$7)""", cid, mid, r["user_aad_id"], r["user_name"],
                        r["reaction_type"], r["action"], r["at"])
                result = result if result == "created" else "filled"
            if result == "filled":
                await conn.execute("update messages set backfilled_at=now() where conversation_id=$1 and message_id=$2", cid, mid)
            return result

    async def add_attachment_link(self, attachment_id: int, document_id: int, method: str) -> None:
        assert self.pool
        await self.pool.execute(
            """insert into attachment_document_links (attachment_id, document_id, match_method) values ($1,$2,$3)
               on conflict do nothing""", attachment_id, document_id, method)

    async def list_document_links(self, document_id: int) -> list[dict]:
        assert self.pool
        return [dict(r) for r in await self.pool.fetch(
            """select l.match_method, a.* from attachment_document_links l join attachments a on a.id=l.attachment_id
               where l.document_id=$1 order by a.id""", document_id)]
