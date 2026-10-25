import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(name="load")
def load_fixture():
    return load


def wipe_guard(dsn: str) -> None:
    """The Postgres tests run `drop schema public cascade`. Refuse unless the database is clearly disposable: its name
    contains 'test', or the human set SKIM_TEST_DB_MAY_BE_WIPED=yes for this run. Never point this at production."""
    import os
    from urllib.parse import urlsplit
    name = urlsplit(dsn).path.strip("/").lower()
    if "test" not in name and os.environ.get("SKIM_TEST_DB_MAY_BE_WIPED") != "yes":
        raise pytest.UsageError(
            f"TEST_DATABASE_URL points at database {name!r}: these tests DROP the whole public schema. Use a throwaway "
            "database whose name contains 'test', or set SKIM_TEST_DB_MAY_BE_WIPED=yes if you are certain it is disposable.")


async def _pg_store():
    """A PostgresStorage on a freshly migrated disposable database (TEST_DATABASE_URL), else skip."""
    import os
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    wipe_guard(dsn)
    import asyncpg
    from migrate import MIGRATIONS
    from skim.storage.postgres import PostgresStorage

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    await conn.execute("drop schema public cascade; create schema public;")
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await conn.execute(path.read_text())
    await conn.execute((MIGRATIONS / "003_phase2.sql").read_text())  # applies cleanly a second time
    await conn.execute((MIGRATIONS / "004_job_locks.sql").read_text())
    await conn.execute((MIGRATIONS / "005_phase3.sql").read_text())  # applies cleanly a second time
    await conn.close()
    s = PostgresStorage(dsn)
    await s.start()
    yield s
    await s.close()


@pytest.fixture
async def pg_store():
    async for s in _pg_store():
        yield s


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    """The storage contract runs against both implementations (postgres only when TEST_DATABASE_URL is set)."""
    if request.param == "memory":
        from skim.storage.memory import MemoryStorage
        yield MemoryStorage()
    else:
        async for s in _pg_store():
            yield s


@pytest.fixture(autouse=True)
def _no_ai_from_the_developers_environment(monkeypatch):
    """Tests never reach a real LLM, whatever keys the developer has exported."""
    for name in ("AI_ENABLED", "GROQ_API_KEY", "GROQ_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL", "PROMPT_VERSION"):
        monkeypatch.delenv(name, raising=False)
