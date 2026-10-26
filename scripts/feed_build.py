"""Build the student-app cards (feed_items) from ALL existing history, once (PHASE4.md section 6.4 --backfill).

    set -a; . ./.env; set +a                      # DATABASE_URL (the session pooler, :5432, is fine); STAFF_AAD_IDS too
    python scripts/feed_build.py --backfill              # every message, file event and event change, whatever its age
    python scripts/feed_build.py --backfill --team-id <bot team id or AAD group id>

The normal feed-build step inside run-all only looks back FEED_LOOKBACK_DAYS (30). This does the same work with no
look-back, so a fresh deploy gets cards for existing data. It is idempotent: running it twice adds nothing the second time.
Takes the same job lock as run-all, so it refuses to run while a job is running. Prints the stats.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "src")]

from skim.jobs.feed_build import build_feed  # noqa: E402
from skim.storage.postgres import PostgresStorage  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", required=True, help="scan all history (required, to make the intent explicit)")
    ap.add_argument("--team-id", help="only this team (bot team id or AAD group id)")
    a = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    staff = frozenset(x.strip() for x in (os.environ.get("STAFF_AAD_IDS") or "").split(",") if x.strip())
    storage = PostgresStorage(dsn)
    await storage.start()
    try:
        team_ids = None
        if a.team_id:
            matches = [t["id"] for t in await storage.list_teams() if a.team_id in (t["id"], t.get("aad_group_id"))]
            if len(matches) != 1:
                print(f"no single team matches {a.team_id!r}", file=sys.stderr)
                return 2
            team_ids = set(matches)
        async with storage.job_lock("jobs", 300) as got:
            if not got:
                print("a job is running right now (run-all holds the lock); try again in a minute", file=sys.stderr)
                return 3
            stats: Counter = Counter()
            status = "partial"
            while status == "partial":  # each pass makes progress or stops
                before = sum(v for k, v in stats.items() if k.endswith("_cards"))
                status = await build_feed(storage, staff, datetime.now(timezone.utc), None, stats, team_ids)
                if sum(v for k, v in stats.items() if k.endswith("_cards")) == before:
                    break
        print(f"staff ids configured: {len(staff)}" + ("   (none: every non-installer author is 'unknown')" if not staff else ""))
        for k, v in sorted(stats.items()):
            print(f"  {k} = {v}")
        print(f"status: {status}")
    finally:
        await storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
