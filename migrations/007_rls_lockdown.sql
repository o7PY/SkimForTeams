-- P4-D-136: the student app ships the Supabase publishable key, so PostgREST must not be able to read any table.
-- Enable RLS (with no policies = deny all for anon/authenticated) on EVERY table in public. The backend connects as
-- the postgres role, which bypasses RLS. Dynamic so it also covers schema_migrations and any table added since.
-- Every later migration must enable RLS on the tables it creates (checked by scripts/check_rls.py and tests).
do $$
declare r record;
begin
  for r in
    select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity
  loop
    execute format('alter table public.%I enable row level security', r.relname);
  end loop;
end $$;
