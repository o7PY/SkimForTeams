-- PHASE6A: "Ask the course" rate limit (20 questions per user per hour, 60 per day). One row per accepted question: the time
-- only. No question or answer text is ever stored. Locked down like every other table (P4-D-136): RLS on, no policies, so the
-- Data API sees nothing; only the backend's own database role reads and writes it.
create table if not exists chat_usage (
  id       bigserial primary key,
  user_id  uuid not null references app_users(id) on delete cascade,
  at       timestamptz not null default now()
);
create index if not exists chat_usage_user_at_idx on chat_usage (user_id, at desc);
alter table chat_usage enable row level security;
