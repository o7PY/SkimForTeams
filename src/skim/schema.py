"""Startup/health check: are all of the code's migrations/*.sql recorded in schema_migrations?

Statuses: "ok", "behind" (the DB lacks migrations the code ships), "unknown" (migration files not found or the
check itself failed), "n/a" (storage without a schema, e.g. memory)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("skim.schema")


def migrations_dir() -> Path | None:
    candidates = [os.environ.get("MIGRATIONS_DIR"), "migrations", str(Path(__file__).resolve().parents[2] / "migrations")]
    for c in candidates:
        if c and Path(c).is_dir():
            return Path(c)
    return None


def expected_migrations() -> set[str] | None:
    d = migrations_dir()
    return {p.name for p in d.glob("*.sql")} if d else None


async def schema_status(storage, expected: set[str] | None = None) -> tuple[str, list[str]]:
    """Returns (status, missing migration names)."""
    expected = expected_migrations() if expected is None else expected
    try:
        applied = await storage.applied_migrations()
    except Exception:
        log.exception("schema check failed")
        return "unknown", []
    if applied is None:
        return "n/a", []
    if expected is None:
        return "unknown", []
    missing = sorted(expected - applied)
    return ("behind", missing) if missing else ("ok", [])
