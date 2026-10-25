"""Runs only when TEST_DATABASE_URL points at a disposable Postgres. Skipped otherwise."""
import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

CONV, MSG = "19:chanGeneral@thread.tacv2", "1700000000001"


async def test_full_sequence_against_postgres(load):
    import asyncpg
    from migrate import MIGRATIONS
    from skim.bot import ingest
    from skim.config import Settings
    from skim.storage.postgres import PostgresStorage

    from conftest import wipe_guard
    dsn = os.environ["TEST_DATABASE_URL"]
    wipe_guard(dsn)
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    await conn.execute("drop schema public cascade; create schema public;")
    await conn.execute((MIGRATIONS / "001_init.sql").read_text())
    await conn.execute((MIGRATIONS / "002_welcome_sent.sql").read_text())
    await conn.execute((MIGRATIONS / "003_phase2.sql").read_text())
    await conn.execute((MIGRATIONS / "004_job_locks.sql").read_text())
    await conn.close()

    settings = Settings.from_env({"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true",
                                  "STORAGE": "memory", "PAIRING_SECRET": "s"})
    s = PostgresStorage(dsn)
    await s.start()
    try:
        for n in ["install_add", "channel_post", "channel_post", "message_edit", "message_edit_2",
                  "message_delete", "channel_post_with_file", "reaction_added", "unknown_type"]:
            await ingest(s, settings, load(n))
        assert len(await s.list_raw(100, None)) == 8  # one duplicate dropped
        got = await s.get_message(CONV, MSG)
        assert got["message"]["current_version"] == 4 and got["message"]["deleted_at"] is not None
        assert [v["event"] for v in got["versions"]] == ["created", "edited", "edited", "deleted"]
        assert len(got["reactions"]) == 1
        assert (await s.list_teams())[0]["pairing_code"]
        from datetime import datetime, timezone
        now, team = datetime.now(timezone.utc), "19:teamabc123@thread.tacv2"
        assert await s.claim_welcome(team, now) and not await s.claim_welcome(team, now)
        await s.mark_team_installed(team, "ABC123", None, now)  # retry on an active install: no reset
        assert not await s.claim_welcome(team, now)
        await s.mark_team_removed(team, now)
        await s.mark_team_installed(team, "ABC123", None, now)  # fresh install: reset
        assert await s.claim_welcome(team, now)
        files = await s.get_message(CONV, "1700000000009")
        assert len(files["attachments"]) == 1
    finally:
        await s.close()
