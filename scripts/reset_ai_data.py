"""Reset the AI-derived data of ONE team so the next run-all rebuilds it from the stored messages and files.

    set -a; . ./.env; set +a                      # DATABASE_URL (the session pooler, :5432, is fine)
    python scripts/reset_ai_data.py --team-id <bot team id or AAD group id>          # DRY RUN: only counts
    python scripts/reset_ai_data.py --team-id <...> --yes                            # really delete

Deletes, for that team only: events, event_changes, message_event_links, message_analysis, document_texts.
KEEPS: messages, message versions, documents, doc_chunks, embeddings, every other team, all Phase 1/2 data.
Takes the same job lock as run-all, so it refuses to run while a job is running. Then call run-all until "next": false.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "src")]

from skim.storage.postgres import PostgresStorage  # noqa: E402


async def resolve_team(storage, wanted: str) -> dict | None:
    matches = [t for t in await storage.list_teams() if wanted in (t["id"], t.get("aad_group_id"))]
    return matches[0] if len(matches) == 1 else None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-id", required=True)
    ap.add_argument("--yes", action="store_true", help="really delete (default is a dry run)")
    a = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    storage = PostgresStorage(dsn)
    await storage.start()
    try:
        team = await resolve_team(storage, a.team_id)
        if team is None:
            print(f"no single team matches {a.team_id!r} (use the bot team id or the AAD group id)", file=sys.stderr)
            return 2
        async with storage.job_lock("jobs", 300) as got:
            if not got:
                print("a job is running right now (run-all holds the lock); try again in a minute", file=sys.stderr)
                return 3
            counts = await storage.reset_ai_data(team["id"], dry_run=not a.yes)
        print(f"team: {team.get('name') or '(unnamed)'} ({team['id']})")
        print(("DELETED: " if a.yes else "would delete (dry run, add --yes): ") + ", ".join(f"{k}={v}" for k, v in counts.items()))
        print("kept: messages, documents, doc_chunks, embeddings, all other teams")
        if a.yes:
            print("next: POST /jobs/run-all repeatedly until it returns \"next\": false")
    finally:
        await storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
