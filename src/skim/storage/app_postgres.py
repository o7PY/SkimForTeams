"""Phase 4 storage for PostgresStorage (twin of app_memory.py). Student-app tables only; see migration 008."""
from __future__ import annotations

from datetime import datetime
from typing import Any

REMINDER_FIELDS = {"item_id", "event_id", "kind", "offset_minutes", "fire_at"}
FEED_COLS = ("team_id", "kind", "conversation_id", "message_id", "document_event_id", "event_change_id",
             "occurred_at", "hidden")


def _d(r) -> dict | None:
    """A row as a dict with uuid values as plain strings (the API and the memory twin use strings)."""
    if r is None:
        return None
    return {k: (str(v) if type(v).__name__ == "UUID" else v) for k, v in dict(r).items()}


class PostgresAppMixin:
    # --- users ---
    async def upsert_app_user(self, user_id: str, email: str | None, name: str | None, at: datetime) -> dict:
        assert self.pool
        r = await self.pool.fetchrow(
            """insert into app_users (id, email, name, created_at, last_seen_at) values ($1,$2,$3,$4,$4)
               on conflict (id) do update set email = coalesce(excluded.email, app_users.email),
                 name = coalesce(excluded.name, app_users.name), last_seen_at = excluded.last_seen_at
               returning *""", user_id, email, name, at)
        return _d(r)

    async def get_app_user(self, user_id: str) -> dict | None:
        assert self.pool
        return _d(await self.pool.fetchrow("select * from app_users where id=$1", user_id))

    async def set_user_settings(self, user_id: str, settings: dict) -> None:
        assert self.pool
        await self.pool.execute("update app_users set settings=$2 where id=$1", user_id, settings)

    async def delete_app_user(self, user_id: str) -> bool:
        """Remove every row for the user (all child tables cascade). True if the user existed."""
        assert self.pool
        return (await self.pool.execute("delete from app_users where id=$1", user_id)) == "DELETE 1"

    async def auth_user_exists(self, user_id: str) -> bool:
        """Is this user still in Supabase auth? True when there is no auth schema (local/test databases)."""
        assert self.pool
        if not await self.pool.fetchval("select to_regclass('auth.users') is not null"):
            return True
        return await self.pool.fetchval("select exists (select 1 from auth.users where id=$1)", user_id)

    async def delete_auth_user(self, user_id: str) -> bool:
        """Delete the Supabase auth user by SQL (P4-D-134, spike S5). False if there was none (or no auth schema)."""
        assert self.pool
        if not await self.pool.fetchval("select to_regclass('auth.users') is not null"):
            return False
        return (await self.pool.execute("delete from auth.users where id=$1", user_id)) == "DELETE 1"

    # --- joining ---
    async def get_team_by_code(self, code: str) -> dict | None:
        assert self.pool
        norm = "".join(code.split()).upper()
        return _d(await self.pool.fetchrow("select * from teams where upper(pairing_code)=$1", norm))

    async def record_join_attempt(self, user_id: str, at: datetime, ok: bool) -> None:
        assert self.pool
        await self.pool.execute("insert into join_attempts (user_id, at, ok) values ($1,$2,$3)", user_id, at, ok)

    async def count_join_attempts(self, user_id: str, since: datetime) -> int:
        assert self.pool
        return await self.pool.fetchval("select count(*) from join_attempts where user_id=$1 and at >= $2", user_id, since)

    async def add_subscription(self, user_id: str, team_id: str, at: datetime) -> bool:
        """True if newly joined or re-joined after leaving; False if already an active member."""
        assert self.pool
        r = await self.pool.fetchrow(
            """insert into subscriptions (user_id, team_id, joined_at) values ($1,$2,$3)
               on conflict (user_id, team_id) do update set left_at = null, joined_at = excluded.joined_at, muted = false
                 where subscriptions.left_at is not null
               returning 1""", user_id, team_id, at)
        return r is not None

    async def list_subscriptions(self, user_id: str, include_left: bool = False) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """select s.user_id, s.team_id, s.joined_at, s.muted, s.left_at, t.name, t.removed_at
               from subscriptions s join teams t on t.id = s.team_id
               where s.user_id=$1 and ($2 or s.left_at is null) order by s.joined_at, s.team_id""", user_id, include_left)
        return [_d(r) for r in rows]

    async def set_subscription_muted(self, user_id: str, team_id: str, muted: bool) -> bool:
        assert self.pool
        return (await self.pool.execute("update subscriptions set muted=$3 where user_id=$1 and team_id=$2 and left_at is null",
                                        user_id, team_id, muted)) == "UPDATE 1"

    async def leave_subscription(self, user_id: str, team_id: str, at: datetime) -> bool:
        """Leave a team and delete the user's state, stars and reminders on that team's items. True if they were in it."""
        assert self.pool
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                r = await conn.execute("update subscriptions set left_at=$3 where user_id=$1 and team_id=$2 and left_at is null",
                                       user_id, team_id, at)
                if r != "UPDATE 1":
                    return False
                items = "select id from feed_items where team_id=$2"
                await conn.execute(f"delete from user_item_state where user_id=$1 and item_id in ({items})", user_id, team_id)
                await conn.execute(f"delete from stars where user_id=$1 and item_id in ({items})", user_id, team_id)
                await conn.execute(f"delete from reminders where user_id=$1 and item_id in ({items})", user_id, team_id)
        return True

    # --- feed items ---
    async def upsert_feed_item(self, item: dict, event_ids: list[int]) -> tuple[int, bool]:
        """Idempotent per source (message / document event / event change). Returns (id, created). Never un-hides a row
        or moves occurred_at; event links are only added."""
        assert self.pool
        kind = item["kind"]
        where = {"message": "conversation_id=$1 and message_id=$2", "document": "document_event_id=$1",
                 "change": "event_change_id=$1"}[kind]
        key = {"message": (item.get("conversation_id"), item.get("message_id")), "document": (item.get("document_event_id"),),
               "change": (item.get("event_change_id"),)}[kind]
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                cols = ",".join(FEED_COLS)
                marks = ",".join(f"${i + 1}" for i in range(len(FEED_COLS)))
                rid = await conn.fetchval(
                    f"insert into feed_items ({cols}) values ({marks}) on conflict do nothing returning id",
                    *[item.get(c, False if c == "hidden" else None) for c in FEED_COLS])
                created = rid is not None
                if rid is None:
                    rid = await conn.fetchval(f"select id from feed_items where kind='{kind}' and {where}", *key)
                for eid in event_ids:
                    await conn.execute("insert into feed_item_events (item_id, event_id) values ($1,$2) on conflict do nothing", rid, eid)
        return rid, created

    async def set_feed_item_hidden(self, item_id: int, hidden: bool) -> None:
        assert self.pool
        await self.pool.execute("update feed_items set hidden=$2 where id=$1", item_id, hidden)

    async def get_feed_item(self, item_id: int) -> dict | None:
        assert self.pool
        r = await self.pool.fetchrow("select * from feed_items where id=$1", item_id)
        if r is None:
            return None
        d = _d(r)
        d["event_ids"] = [x["event_id"] for x in await self.pool.fetch(
            "select event_id from feed_item_events where item_id=$1 order by event_id", item_id)]
        return d

    # --- per-user swipe state ---
    async def set_item_state(self, user_id: str, item_ids: list[int], state: str | None, at: datetime,
                             snoozed_until: datetime | None = None, folded_into: int | None = None) -> None:
        """Upsert state for each item. state=None is only valid with snoozed_until (a snoozed, never swiped card)."""
        assert self.pool
        for iid in item_ids:
            await self.pool.execute(
                """insert into user_item_state (user_id, item_id, state, acted_at, snoozed_until, folded_into)
                   values ($1,$2,$3,$4,$5,$6)
                   on conflict (user_id, item_id) do update set state=excluded.state, acted_at=excluded.acted_at,
                     snoozed_until=excluded.snoozed_until, folded_into=excluded.folded_into""",
                user_id, iid, state, at, snoozed_until, folded_into)

    async def clear_item_state(self, user_id: str, item_ids: list[int]) -> None:
        assert self.pool
        await self.pool.execute("delete from user_item_state where user_id=$1 and item_id = any($2::bigint[])", user_id, item_ids)

    async def get_item_states(self, user_id: str, item_ids: list[int]) -> dict[int, dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from user_item_state where user_id=$1 and item_id = any($2::bigint[])", user_id, item_ids)
        return {r["item_id"]: _d(r) for r in rows}

    # --- stars ---
    async def put_star(self, user_id: str, item_id: int, expires_at: datetime | None, until_event_over: bool,
                       at: datetime) -> None:
        assert self.pool
        await self.pool.execute(
            """insert into stars (user_id, item_id, expires_at, until_event_over, created_at) values ($1,$2,$3,$4,$5)
               on conflict (user_id, item_id) do update set expires_at=excluded.expires_at,
                 until_event_over=excluded.until_event_over""", user_id, item_id, expires_at, until_event_over, at)

    async def delete_star(self, user_id: str, item_id: int) -> bool:
        assert self.pool
        return (await self.pool.execute("delete from stars where user_id=$1 and item_id=$2", user_id, item_id)) == "DELETE 1"

    async def list_stars(self, user_id: str) -> list[dict]:
        """All of the user's stars, newest first. Expiry is applied by the reader (P4-D-114)."""
        assert self.pool
        rows = await self.pool.fetch("select * from stars where user_id=$1 order by created_at desc, item_id desc", user_id)
        return [_d(r) for r in rows]

    # --- reminders ---
    async def upsert_reminder(self, r: dict, at: datetime) -> dict | None:
        """Create or update by client uuid; only the owner's row can be updated (None if the id is someone else's). Clears a tombstone."""
        assert self.pool
        row = await self.pool.fetchrow(
            """insert into reminders (id, user_id, item_id, event_id, kind, offset_minutes, fire_at, created_at, updated_at)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$8)
               on conflict (id) do update set item_id=excluded.item_id, event_id=excluded.event_id, kind=excluded.kind,
                 offset_minutes=excluded.offset_minutes, fire_at=excluded.fire_at, deleted_at=null, updated_at=excluded.updated_at
                 where reminders.user_id = excluded.user_id
               returning *""",
            r["id"], r["user_id"], r.get("item_id"), r.get("event_id"), r["kind"], r.get("offset_minutes"), r.get("fire_at"), at)
        return _d(row)

    async def tombstone_reminder(self, user_id: str, reminder: dict, at: datetime) -> None:
        """Mark deleted (inserting a tombstone row first if it never existed, e.g. a virtual auto reminder)."""
        assert self.pool
        await self.pool.execute(
            """insert into reminders (id, user_id, item_id, event_id, kind, offset_minutes, fire_at, deleted_at, created_at, updated_at)
               values ($1,$2,$3,$4,$5,$6,$7,$8,$8,$8)
               on conflict (id) do update set deleted_at=excluded.deleted_at, updated_at=excluded.updated_at
                 where reminders.user_id = excluded.user_id""",
            reminder["id"], user_id, reminder.get("item_id"), reminder.get("event_id"), reminder["kind"],
            reminder.get("offset_minutes"), reminder.get("fire_at"), at)

    async def get_reminder(self, user_id: str, reminder_id: str) -> dict | None:
        assert self.pool
        return _d(await self.pool.fetchrow("select * from reminders where id=$1 and user_id=$2", reminder_id, user_id))

    async def list_reminders(self, user_id: str, include_deleted: bool = False) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch(
            "select * from reminders where user_id=$1 and ($2 or deleted_at is null) order by created_at, id", user_id, include_deleted)
        return [_d(r) for r in rows]

    # --- devices ---
    async def upsert_device(self, user_id: str, token: str, platform: str, at: datetime) -> None:
        """Register a token for this user. A token moves to the latest signed-in user (one phone, several accounts), but only
        the same user re-enables a disabled token: an owner change never clears disabled_at."""
        assert self.pool
        await self.pool.execute(
            """insert into devices (user_id, expo_push_token, platform, created_at, last_seen_at) values ($1,$2,$3,$4,$4)
               on conflict (expo_push_token) do update set user_id=excluded.user_id, platform=excluded.platform,
                 last_seen_at=excluded.last_seen_at,
                 disabled_at = case when devices.user_id = excluded.user_id then null else devices.disabled_at end""", user_id, token, platform, at)

    async def disable_device(self, token: str, at: datetime) -> None:
        assert self.pool
        await self.pool.execute("update devices set disabled_at=$2 where expo_push_token=$1 and disabled_at is null", token, at)

    async def delete_device(self, user_id: str, token: str) -> bool:
        assert self.pool
        return (await self.pool.execute("delete from devices where user_id=$1 and expo_push_token=$2", user_id, token)) == "DELETE 1"

    async def list_devices(self, user_id: str, active_only: bool = True) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from devices where user_id=$1 and ($2 = false or disabled_at is null) order by id",
                                     user_id, active_only)
        return [_d(r) for r in rows]

    # --- push outbox ---
    async def add_push(self, row: dict) -> bool:
        """Queue one alert; False if (user, event change) was already queued (never alert twice)."""
        assert self.pool
        r = await self.pool.fetchrow(
            """insert into push_outbox (user_id, event_change_id, title, body, data, not_before, status, error)
               values ($1,$2,$3,$4,$5,$6,$7,$8) on conflict (user_id, event_change_id) do nothing returning id""",
            row["user_id"], row["event_change_id"], row["title"], row["body"], row.get("data") or {}, row["not_before"],
            row.get("status", "pending"), row.get("error"))
        return r is not None

    async def list_due_push(self, now: datetime, limit: int) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from push_outbox where status='pending' and not_before <= $1 "
                                     "order by not_before, id limit $2", now, limit)
        return [_d(r) for r in rows]

    async def list_push_awaiting_receipt(self, sent_before: datetime, limit: int) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from push_outbox where status='sent' and ticket_id is not null "
                                     "and receipt_status is null and sent_at <= $1 order by id limit $2", sent_before, limit)
        return [_d(r) for r in rows]

    async def update_push(self, push_id: int, **fields: Any) -> None:
        assert self.pool
        allowed = {"status", "ticket_id", "receipt_status", "error", "sent_at"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(fields))
        await self.pool.execute(f"update push_outbox set {sets} where id=$1", push_id, *fields.values())

    async def list_push(self, user_id: str | None = None) -> list[dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from push_outbox where ($1::uuid is null or user_id=$1) order by id", user_id)
        return [_d(r) for r in rows]
