import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(name="load")
def load_fixture():
    return load


async def _pg_store():
    """A PostgresStorage on a freshly migrated disposable database (TEST_DATABASE_URL), else skip."""
    import os
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
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
