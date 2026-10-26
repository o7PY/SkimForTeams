-- Phase 4: student app tables (PHASE4.md section 5). Every table enables RLS in this file (P4-D-136).
-- No foreign keys into auth.users; user rows hang off app_users.id (= Supabase sub) and cascade on delete (P4-D-134).

create table if not exists app_users (
  id            uuid primary key,
  email         text,
  name          text,
  created_at    timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  settings      jsonb not null default '{}'::jsonb   -- sparse overrides; defaults are merged in code
);
alter table app_users enable row level security;

create table if not exists subscriptions (
  user_id    uuid not null references app_users(id) on delete cascade,
  team_id    text not null references teams(id),
  joined_at  timestamptz not null default now(),
  muted      boolean not null default false,
  left_at    timestamptz,
  primary key (user_id, team_id)
);
create index if not exists subscriptions_team_idx on subscriptions (team_id);
alter table subscriptions enable row level security;

create table if not exists join_attempts (
  id       bigserial primary key,
  user_id  uuid not null references app_users(id) on delete cascade,
  at       timestamptz not null default now(),
  ok       boolean not null
);
create index if not exists join_attempts_user_idx on join_attempts (user_id, at desc);
alter table join_attempts enable row level security;

-- messages are keyed by (conversation_id, message_id), so a message feed item carries both (P4-DIFF-001).
create table if not exists feed_items (
  id                 bigserial primary key,
  team_id            text not null references teams(id),
  kind               text not null check (kind in ('message', 'document', 'change')),
  conversation_id    text,
  message_id         text,
  document_event_id  bigint references document_events(id),
  event_change_id    bigint references event_changes(id),
  occurred_at        timestamptz not null,
  created_at         timestamptz not null default now(),
  hidden             boolean not null default false,
  check ((kind = 'message'  and conversation_id is not null and message_id is not null
                            and document_event_id is null and event_change_id is null)
      or (kind = 'document' and document_event_id is not null and message_id is null and event_change_id is null)
      or (kind = 'change'   and event_change_id is not null and message_id is null and document_event_id is null))
);
create unique index if not exists feed_items_message_uq on feed_items (conversation_id, message_id) where kind = 'message';
create unique index if not exists feed_items_docevent_uq on feed_items (document_event_id) where kind = 'document';
create unique index if not exists feed_items_change_uq on feed_items (event_change_id) where kind = 'change';
create index if not exists feed_items_team_time_idx on feed_items (team_id, occurred_at desc, id desc);
alter table feed_items enable row level security;

create table if not exists feed_item_events (
  item_id   bigint not null references feed_items(id) on delete cascade,
  event_id  bigint not null references events(id),
  primary key (item_id, event_id)
);
create index if not exists feed_item_events_event_idx on feed_item_events (event_id);
alter table feed_item_events enable row level security;

-- state is null only for a snoozed item that was never swiped (P4-DIFF-002).
create table if not exists user_item_state (
  user_id        uuid not null references app_users(id) on delete cascade,
  item_id        bigint not null references feed_items(id) on delete cascade,
  state          text check (state in ('dismissed', 'opened')),
  acted_at       timestamptz not null default now(),
  snoozed_until  timestamptz,
  folded_into    bigint references feed_items(id) on delete set null,
  primary key (user_id, item_id),
  check (state is not null or snoozed_until is not null)
);
create index if not exists user_item_state_acted_idx on user_item_state (user_id, acted_at desc);
alter table user_item_state enable row level security;

create table if not exists stars (
  user_id          uuid not null references app_users(id) on delete cascade,
  item_id          bigint not null references feed_items(id) on delete cascade,
  expires_at       timestamptz,
  until_event_over boolean not null default false,
  created_at       timestamptz not null default now(),
  primary key (user_id, item_id)
);
alter table stars enable row level security;

create table if not exists reminders (
  id              uuid primary key,
  user_id         uuid not null references app_users(id) on delete cascade,
  item_id         bigint references feed_items(id) on delete cascade,
  event_id        bigint references events(id),
  kind            text not null check (kind in ('recommended_day_before', 'recommended_same_day', 'manual',
                                                'auto_day_before', 'auto_same_day')),
  offset_minutes  int,
  fire_at         timestamptz,
  deleted_at      timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists reminders_user_idx on reminders (user_id);
create index if not exists reminders_event_idx on reminders (event_id);
alter table reminders enable row level security;

create table if not exists devices (
  id               bigserial primary key,
  user_id          uuid not null references app_users(id) on delete cascade,
  expo_push_token  text not null unique,
  platform         text not null,
  created_at       timestamptz not null default now(),
  last_seen_at     timestamptz not null default now(),
  disabled_at      timestamptz
);
create index if not exists devices_user_idx on devices (user_id);
alter table devices enable row level security;

create table if not exists push_outbox (
  id               bigserial primary key,
  user_id          uuid not null references app_users(id) on delete cascade,
  event_change_id  bigint not null references event_changes(id),
  title            text not null,
  body             text not null,
  data             jsonb not null default '{}'::jsonb,
  not_before       timestamptz not null,
  status           text not null default 'pending' check (status in ('pending', 'sent', 'failed', 'skipped')),
  ticket_id        text,
  receipt_status   text,
  error            text,
  created_at       timestamptz not null default now(),
  sent_at          timestamptz,
  unique (user_id, event_change_id)
);
create index if not exists push_outbox_due_idx on push_outbox (status, not_before);
alter table push_outbox enable row level security;
