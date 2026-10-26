"""List public tables with row level security OFF (P4-D-136). Exit 1 if any. Needs DATABASE_URL."""
import asyncio
import os
import sys

import asyncpg

UNPROTECTED_SQL = (
    "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
    "where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity order by 1"
)


async def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        bad = [r["relname"] for r in await conn.fetch(UNPROTECTED_SQL)]
    finally:
        await conn.close()
    for name in bad:
        print(f"RLS OFF  public.{name}")
    print(f"{len(bad)} unprotected table(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
