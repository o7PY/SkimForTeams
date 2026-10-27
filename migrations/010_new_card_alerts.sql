-- New-card alerts: a push for a new post or file, not only for a date change. push_outbox rows were always about an event change
-- (event_change_id NOT NULL); a new card has no event change, it has a feed item. A row now has exactly one of the two, each
-- queued at most once per user. Existing rows (all event changes) satisfy the new rule as they are.
alter table push_outbox alter column event_change_id drop not null;
alter table push_outbox add column if not exists feed_item_id bigint references feed_items(id) on delete cascade;
create unique index if not exists push_outbox_user_item_uq on push_outbox (user_id, feed_item_id) where feed_item_id is not null;
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'push_outbox_one_source') then
    alter table push_outbox add constraint push_outbox_one_source check ((event_change_id is not null) <> (feed_item_id is not null));
  end if;
end $$;
