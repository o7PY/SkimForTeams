"""POST /jobs/* (FR-207). Registered only when JOBS_TOKEN is set and Graph credentials exist, so otherwise every
/jobs path is a plain 404 (P2-D-108). Bearer check is constant-time."""
from __future__ import annotations

import hmac

from aiohttp import web

from .jobs.runner import JobRunner, STEPS


def register(app: web.Application, runner: JobRunner, token: str) -> None:
    expected = f"Bearer {token}".encode()

    def make(job: str):
        async def handler(request: web.Request) -> web.Response:
            if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), expected):
                return web.json_response({"error": "unauthorized"}, status=401)
            since = request.query.get("since_days")
            if since is not None and not (since.isdigit() and int(since) > 0):
                return web.json_response({"error": "since_days must be a positive integer"}, status=400)
            result = await runner.run(job, request.query.get("team_id") or None, int(since) if since else None,
                                      request.query.get("force", "").lower() in ("1", "true", "yes"))
            return web.json_response(result, status=500 if result["status"] == "error" else 200)  # JSON either way
        return handler

    for job in STEPS:
        app.router.add_post(f"/jobs/{job}", make(job))
