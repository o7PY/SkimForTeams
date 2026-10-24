"""Smoke test of a running Skim service (FR-212). Read-only unless JOBS_TOKEN is set (then it also runs the jobs).

    BASE_URL=https://<service> DEBUG_TOKEN=... [JOBS_TOKEN=...] python scripts/smoke_remote.py [--jobs]

Checks /health (including db_schema), the Phase 1 and Phase 2 /debug endpoints, and with --jobs and JOBS_TOKEN
`POST /jobs/run-all` (twice: the second run must not create anything). Exit code 0 only if every check passes.
Prints no tokens and no message/file content, only counts and statuses."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import aiohttp


async def run(base: str, debug_token: str | None, jobs_token: str | None, jobs: bool = False,
              session: aiohttp.ClientSession | None = None) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    own = session is None
    session = session or aiohttp.ClientSession()
    base = base.rstrip("/")

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    async def call(method: str, path: str, token: str | None = None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        async with session.request(method, base + path, headers=headers, **kw) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:
                body = None
            return r.status, body

    try:
        st, body = await call("GET", "/health")
        check("health", st == 200 and body and body.get("ok") and body.get("db"), f"status={st}")
        check("health.db_schema", body and body.get("db_schema") in ("ok", "n/a"), f"db_schema={body and body.get('db_schema')}")
        if debug_token:
            check("debug rejects a wrong token", (await call("GET", "/debug/teams", "wrong"))[0] == 403)
            for path in ("/debug/teams", "/debug/channels", "/debug/documents?limit=5", "/debug/sync-status"):
                st, body = await call("GET", path, debug_token)
                check(f"GET {path.split('?')[0]}", st == 200 and isinstance(body, (list, dict)), f"status={st}")
            st, docs = await call("GET", "/debug/documents?limit=1", debug_token)
            if st == 200 and docs:
                st, body = await call("GET", f"/debug/documents/{docs[0]['id']}", debug_token)
                check("GET /debug/documents/{id}", st == 200 and {"document", "events", "attachments"} <= set(body or {}), f"status={st}")
            st, body = await call("GET", "/debug/documents/999999999", debug_token)
            check("unknown document -> 404", st == 404)
        else:
            check("debug endpoints (skipped: DEBUG_TOKEN not set)", True)
        if jobs and jobs_token:
            check("jobs reject a wrong token", (await call("POST", "/jobs/run-all", "wrong"))[0] == 401)
            for n in (1, 2):
                st, body = await call("POST", "/jobs/run-all", jobs_token)
                ok = st == 200 and body and body.get("status") in ("ok", "partial")
                check(f"POST /jobs/run-all #{n}", ok, f"http={st} status={body and body.get('status')} "
                      f"error_class={body and body.get('error_class')}")
                if n == 2 and ok and body.get("status") == "ok":
                    created = (body.get("stats") or {}).get("created", 0)
                    check("second run-all creates nothing", created == 0, f"created={created}")
        elif jobs:
            check("jobs (skipped: JOBS_TOKEN not set)", True)
    finally:
        if own:
            await session.close()
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", action="store_true", help="also POST /jobs/run-all (needs JOBS_TOKEN); it changes data")
    args = ap.parse_args()
    base = os.environ.get("BASE_URL")
    if not base:
        print("BASE_URL is not set", file=sys.stderr)
        return 2
    results = asyncio.run(run(base, os.environ.get("DEBUG_TOKEN"), os.environ.get("JOBS_TOKEN"), args.jobs))
    for name, ok, detail in results:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
