import logging

from aiohttp.test_utils import TestClient, TestServer

from skim.app import create_app
from skim.config import Settings
from skim.schema import expected_migrations, schema_status
from skim.storage.memory import MemoryStorage

ENV = {"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED": "true", "STORAGE": "memory", "PAIRING_SECRET": "s"}


class Applied(MemoryStorage):
    def __init__(self, applied):
        super().__init__()
        self.applied = applied

    async def applied_migrations(self):
        return self.applied


def test_expected_migrations_are_the_files_in_the_repo():
    assert {"001_init.sql", "003_phase2.sql", "004_job_locks.sql"} <= expected_migrations()


async def test_status_ok_behind_and_unknown():
    exp = {"001.sql", "002.sql"}
    assert await schema_status(Applied({"001.sql", "002.sql", "extra.sql"}), exp) == ("ok", [])
    assert await schema_status(Applied({"001.sql"}), exp) == ("behind", ["002.sql"])
    assert await schema_status(Applied(set()), exp) == ("behind", ["001.sql", "002.sql"])  # no schema_migrations table
    assert await schema_status(MemoryStorage(), exp) == ("n/a", [])
    assert (await schema_status(Applied({"001.sql"}), None))[0] in ("ok", "behind")  # real dir found in the repo


async def test_health_reports_behind_and_startup_logs_loudly(caplog):
    store = Applied({"001_init.sql"})
    client = TestClient(TestServer(create_app(Settings.from_env(ENV), store)))
    with caplog.at_level(logging.ERROR, logger="skim.schema"):
        pass
    with caplog.at_level(logging.ERROR):
        await client.start_server()
    try:
        body = await (await client.get("/health")).json()
        assert body["ok"] is True and body["db"] is True and body["db_schema"] == "behind"
        msg = next(r.getMessage() for r in caplog.records if "SCHEMA IS BEHIND" in r.getMessage())
        assert "004_job_locks.sql" in msg and "scripts/migrate.py" in msg
        store.applied = set(expected_migrations())
        assert (await (await client.get("/health")).json())["db_schema"] == "ok"  # flips without a restart
    finally:
        await client.close()


async def test_postgres_applied_migrations_empty_without_table_then_listed(pg_store):
    assert await pg_store.applied_migrations() == set()  # test schema is built without schema_migrations
    await pg_store.pool.execute("create table schema_migrations (version text primary key, applied_at timestamptz default now())")
    await pg_store.pool.execute("insert into schema_migrations (version) values ('001_init.sql')")
    assert await pg_store.applied_migrations() == {"001_init.sql"}
    assert (await schema_status(pg_store))[0] == "behind"
