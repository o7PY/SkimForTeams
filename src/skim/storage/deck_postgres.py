"""Phase 4 read side for PostgresStorage: the deck's candidates and the rows a card is built from (twin of deck_memory.py).
Business rules (backlog, folding, ordering) live in skim/api/deck.py; storage only fetches, scoped to the user."""
from __future__ import annotations

from datetime import datetime

from .app_postgres import _d

_CANDIDATES = """
select f.id, f.team_id, f.kind, f.occurred_at, st.snoozed_until, (st.item_id is not null) as returned,
       (f.occurred_at >= sub.joined_at - ($3 * interval '1 day')) as in_window,
       coalesce((select array_agg(fe.event_id order by fe.event_id) from feed_item_events fe where fe.item_id = f.id), '{}') as event_ids
from feed_items f
join subscriptions sub on sub.team_id = f.team_id and sub.user_id = $1 and sub.left_at is null
left join user_item_state st on st.user_id = $1 and st.item_id = f.id
where not f.hidden
  and (st.item_id is null or (st.state is null and st.snoozed_until <= $2))
  and (st.item_id is not null
       or f.occurred_at >= sub.joined_at - ($3 * interval '1 day')
       or exists (select 1 from feed_item_events fe where fe.item_id = f.id))
order by (st.item_id is not null) desc, st.snoozed_until desc nulls last, f.occurred_at desc, f.id desc
limit $4"""


class PostgresDeckMixin:
    async def deck_candidates(self, user_id: str, now: datetime, backlog_days: int, limit: int) -> list[dict]:
        """Unswiped cards in the user's active teams (plus cards whose snooze ended or that were put back), newest first.
        in_window = inside the join backlog; a card outside it stays only if it has a linked event (the caller checks it is upcoming)."""
        assert self.pool
        return [_d(r) for r in await self.pool.fetch(_CANDIDATES, user_id, now, backlog_days, limit)]

    async def accessible_item_ids(self, user_id: str, ids: list[int]) -> set[int]:
        assert self.pool
        rows = await self.pool.fetch(
            """select f.id from feed_items f join subscriptions s on s.team_id = f.team_id and s.user_id = $1 and s.left_at is null
               where f.id = any($2::bigint[])""", user_id, ids)
        return {r["id"] for r in rows}

    async def get_feed_items(self, ids: list[int]) -> dict[int, dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """select f.*, coalesce((select array_agg(fe.event_id order by fe.event_id) from feed_item_events fe
                                      where fe.item_id = f.id), '{}') as event_ids
               from feed_items f where f.id = any($1::bigint[])""", ids)
        return {r["id"]: dict(r) for r in rows}

    async def get_events_by_ids(self, ids: list[int]) -> dict[int, dict]:
        assert self.pool
        return {r["id"]: dict(r) for r in await self.pool.fetch("select * from events where id = any($1::bigint[])", ids)}

    async def get_messages(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """select m.conversation_id, m.message_id, m.reply_to_id, m.from_aad_id, m.from_name, m.text, m.subject,
                      m.created_at, m.last_edited_at, m.deleted_at, m.current_version
               from messages m join unnest($1::text[], $2::text[]) as p(c, i) on m.conversation_id = p.c and m.message_id = p.i""",
            [p[0] for p in pairs], [p[1] for p in pairs])
        return {(r["conversation_id"], r["message_id"]): dict(r) for r in rows}

    async def get_message_files(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], list[dict]]:
        """File chips of messages: name, the attachment's own url, and the linked Files-tab document if there is one."""
        assert self.pool
        rows = await self.pool.fetch(
            """select a.conversation_id, a.message_id, a.name, a.content_url, d.id as document_id, d.name as document_name,
                      d.web_url as document_url
               from attachments a join unnest($1::text[], $2::text[]) as p(c, i) on a.conversation_id = p.c and a.message_id = p.i
               left join attachment_document_links adl on adl.attachment_id = a.id
               left join documents d on d.id = adl.document_id
               where a.content_type = 'reference' or d.id is not null
               order by a.id""", [p[0] for p in pairs], [p[1] for p in pairs])
        out: dict[tuple[str, str], list[dict]] = {}
        for r in rows:
            out.setdefault((r["conversation_id"], r["message_id"]), []).append(dict(r))
        return out

    async def get_document_cards(self, document_event_ids: list[int]) -> dict[int, dict]:
        assert self.pool
        rows = await self.pool.fetch(
            """select de.id, de.document_id, de.event, de.at, de.old_name, de.new_name, de.old_path, de.new_path,
                      d.team_id, d.name, d.path, d.web_url, d.created_by_aad, d.created_by_name, d.modified_by_aad,
                      d.modified_by_name, t.installed_by_aad
               from document_events de join documents d on d.id = de.document_id join teams t on t.id = d.team_id
               where de.id = any($1::bigint[])""", document_event_ids)
        return {r["id"]: dict(r) for r in rows}

    async def get_event_changes_by_ids(self, ids: list[int]) -> dict[int, dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from event_changes where id = any($1::bigint[])", ids)
        return {r["id"]: dict(r) for r in rows}

    async def list_event_history(self, event_ids: list[int]) -> dict[int, list[dict]]:
        """Every change of the events, newest first (the card detail's history)."""
        assert self.pool
        out: dict[int, list[dict]] = {}
        for r in await self.pool.fetch("select * from event_changes where event_id = any($1::bigint[]) order by at desc, id desc", event_ids):
            out.setdefault(r["event_id"], []).append(dict(r))
        return out

    async def get_stars(self, user_id: str, item_ids: list[int]) -> dict[int, dict]:
        assert self.pool
        rows = await self.pool.fetch("select * from stars where user_id = $1 and item_id = any($2::bigint[])", user_id, item_ids)
        return {r["item_id"]: _d(r) for r in rows}

    async def list_history(self, user_id: str, limit: int) -> list[dict]:
        """The user's last swiped cards (folded originals are not listed on their own), newest swipe first."""
        assert self.pool
        rows = await self.pool.fetch(
            """select st.item_id, st.state, st.acted_at from user_item_state st
               join feed_items f on f.id = st.item_id
               join subscriptions s on s.user_id = st.user_id and s.team_id = f.team_id and s.left_at is null
               where st.user_id = $1 and st.state is not null and st.folded_into is null
               order by st.acted_at desc, st.item_id desc limit $2""", user_id, limit)
        return [_d(r) for r in rows]

    async def folded_children(self, user_id: str, parent_ids: list[int]) -> dict[int, list[int]]:
        assert self.pool
        out: dict[int, list[int]] = {}
        for r in await self.pool.fetch(
                "select folded_into, item_id from user_item_state where user_id = $1 and folded_into = any($2::bigint[]) "
                "order by item_id", user_id, parent_ids):
            out.setdefault(r["folded_into"], []).append(r["item_id"])
        return out

    async def list_events_for_user(self, user_id: str, since_date) -> dict[int, dict]:
        """Dated events of the user's active teams on or after `since_date` (what reminders are computed from)."""
        assert self.pool
        rows = await self.pool.fetch(
            """select e.* from events e join subscriptions s on s.team_id = e.team_id and s.user_id = $1 and s.left_at is null
               where e.event_date >= $2::date""", user_id, since_date)
        return {r["id"]: dict(r) for r in rows}

    async def latest_items_for_events(self, user_id: str, event_ids: list[int]) -> dict[int, int]:
        """{event id: the newest visible card the user can open that is linked to it} (what a reminder opens)."""
        assert self.pool
        rows = await self.pool.fetch(
            """select distinct on (fe.event_id) fe.event_id, f.id from feed_item_events fe
               join feed_items f on f.id = fe.item_id and not f.hidden
               join subscriptions s on s.team_id = f.team_id and s.user_id = $1 and s.left_at is null
               where fe.event_id = any($2::bigint[]) order by fe.event_id, f.occurred_at desc, f.id desc""", user_id, event_ids)
        return {r["event_id"]: r["id"] for r in rows}

    # --- change alerts (push-alerts job) ---
    async def max_event_change_id(self) -> int:
        assert self.pool
        return await self.pool.fetchval("select coalesce(max(id), 0) from event_changes")

    async def list_changes_after(self, after_id: int, types: tuple[str, ...], limit: int) -> list[dict]:
        """New event changes (oldest first) of live teams, with the event's team."""
        assert self.pool
        rows = await self.pool.fetch(
            """select ec.id, ec.event_id, ec.change_type, ec.old_values, ec.new_values, ec.confidence, ec.at, e.team_id
               from event_changes ec join events e on e.id = ec.event_id
               join teams t on t.id = e.team_id and t.removed_at is null
               where ec.id > $1 and ec.change_type = any($2::text[]) order by ec.id limit $3""", after_id, list(types), limit)
        return [dict(r) for r in rows]

    async def alert_audience(self, team_id: str, event_id: int) -> list[dict]:
        """Subscribers of the team who have not muted it and have not left, each with their settings, their stars on cards
        about the event, and whether they hold a reminder for it (P4-D-122)."""
        assert self.pool
        users = await self.pool.fetch(
            """select u.id as user_id, u.settings from subscriptions s join app_users u on u.id = s.user_id
               where s.team_id = $1 and s.left_at is null and not s.muted order by u.id""", team_id)
        if not users:
            return []
        ids = [str(u["user_id"]) for u in users]
        stars: dict[str, list[dict]] = {}
        for r in await self.pool.fetch(
                """select st.user_id, st.expires_at, st.until_event_over from stars st
                   join feed_item_events fe on fe.item_id = st.item_id
                   where fe.event_id = $1 and st.user_id = any($2::uuid[])""", event_id, ids):
            stars.setdefault(str(r["user_id"]), []).append({"expires_at": r["expires_at"], "until_event_over": r["until_event_over"]})
        reminded = {str(r["user_id"]) for r in await self.pool.fetch(
            """select distinct r.user_id from reminders r
               where r.deleted_at is null and r.user_id = any($2::uuid[])
                 and (r.event_id = $1 or r.item_id in (select item_id from feed_item_events where event_id = $1))""", event_id, ids)}
        return [{"user_id": str(u["user_id"]), "settings": u["settings"] or {}, "stars": stars.get(str(u["user_id"]), []),
                 "has_reminder": str(u["user_id"]) in reminded} for u in users]

    async def feed_item_for_change(self, change_id: int) -> int | None:
        assert self.pool
        return await self.pool.fetchval("select id from feed_items where kind = 'change' and event_change_id = $1", change_id)

    async def deck_changed_since(self, user_id: str, since: datetime) -> bool:
        """Has a card appeared in the user's teams since `since`? (the cheap background-sync check). `created_at` is the DATABASE
        clock and `since` came from the app server's: a 2-minute margin means skew can only cause a harmless extra refetch,
        never a missed card (the app moves `since` forward after every sync)."""
        assert self.pool
        return await self.pool.fetchval(
            """select exists (select 1 from feed_items f join subscriptions s on s.team_id = f.team_id
                              and s.user_id = $1 and s.left_at is null
                              where f.created_at > $2::timestamptz - interval '2 minutes' and not f.hidden)""", user_id, since)
