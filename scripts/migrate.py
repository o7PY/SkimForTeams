"""Apply migrations/*.sql in order against DATABASE_URL (FR-14). Safe to re-run."""
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


async def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1
    # statement_cache_size=0 keeps this working through Supabase's transaction pooler (port 6543).
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        await conn.execute(
            "create table if not exists schema_migrations ("
            "version text primary key, applied_at timestamptz not null default now())"
        )
        done = {r["version"] for r in await conn.fetch("select version from schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                print(f"skip   {path.name}")
                continue
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute("insert into schema_migrations (version) values ($1)", path.name)
            print(f"apply  {path.name}")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
