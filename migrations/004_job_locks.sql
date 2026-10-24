-- Lease-based job lock (replaces pg_try_advisory_xact_lock; works behind the Supabase :6543 transaction pooler).
create table if not exists job_locks (
  name       text primary key,
  holder     text not null,
  expires_at timestamptz not null
);
