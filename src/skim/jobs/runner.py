"""Job runner: one job at a time, time-boxed, recorded in job_runs (P2-D-107/109/110, FR-208).

A single lease lock (table job_locks, P2-D-130) covers every job kind (stricter than "one of each kind": sync-files
and run-all must not touch the same sync_state concurrently - P2-D-126). A caller that finds it held gets "busy".
"""
from __future__ import annotations

import json
import logging
import time
from typing import Awaitable, Callable

from ..graph.auth import GraphAuthError
from ..graph.client import GraphClient, GraphError
from ..storage.base import Storage
from .common import Budget, JobContext
from .discover import discover_team
from .files_sync import delta_sync_channel, full_sync_channel

log = logging.getLogger("skim.jobs")
LOCK = "jobs"
LEASE_MARGIN_SECONDS = 120.0  # lease = time budget + margin (an in-flight request plus retries may overrun)

Step = Callable[[JobContext, str | None], Awaitable[str]]  # returns "ok" | "partial"


def select_teams(teams: list[dict], team_id: str | None) -> list[dict]:
    """Optional filter by the bot's team id or the AAD group id; teams still needing their initial sync go first."""
    if team_id:
        teams = [t for t in teams if team_id in (t["id"], t.get("aad_group_id"))]
    return sorted(teams, key=lambda t: not t.get("needs_initial_sync"))


def error_text(e: Exception) -> str:
    """Short, secret-free description of a failure for sync_error / job_runs.error."""
    code = getattr(e, "code", None)
    status = getattr(e, "status", None)
    return f"{type(e).__name__}" + (f" {status}" if status else "") + (f" {code}" if code else "")


# ---------------------------------------------------------------------------- steps
async def step_discover(ctx: JobContext, team_id: str | None) -> str:
    for team in select_teams(await ctx.storage.list_sync_teams(), team_id):
        await discover_team(ctx, team)
    return "ok"


async def step_sync_files(ctx: JobContext, team_id: str | None) -> str:
    """Per channel: delta if it has state, else a full sync. A failing channel is recorded and skipped."""
    partial = False
    for team in select_teams(await ctx.storage.list_sync_teams(), team_id):
        team_ok = True
        for ch in await ctx.storage.list_channels(team["id"]):
            if not ch["files_drive_id"] or not ch["files_folder_item_id"]:
                team_ok = False  # discovery could not resolve it; sync_error says why
                continue
            if ctx.budget.expired():
                return "partial"
            try:
                status = await delta_sync_channel(ctx, ch)
                if status == "needs_full":
                    status = await full_sync_channel(ctx, ch)
            except (GraphError, GraphAuthError) as e:
                await ctx.storage.update_channel(ch["id"], sync_error=f"files sync failed: {error_text(e)}")
                ctx.stats["channel_errors"] += 1
                team_ok = False
                continue
            if status == "partial":
                partial = True
                team_ok = False
        if team_ok:
            await ctx.storage.update_team(team["id"], last_files_sync_at=ctx.clock())
    return "partial" if partial else "ok"


async def step_run_all(ctx: JobContext, team_id: str | None) -> str:
    """discover -> sync-files (-> backfill -> link attachments: added in T8/T9)."""
    results = [await step_discover(ctx, team_id)]
    results.append(await step_sync_files(ctx, team_id))
    for extra in EXTRA_RUN_ALL_STEPS:
        results.append(await extra(ctx, team_id))
    return "partial" if "partial" in results else "ok"


EXTRA_RUN_ALL_STEPS: list[Step] = []
STEPS: dict[str, Step] = {"discover": step_discover, "sync-files": step_sync_files, "run-all": step_run_all}


# ---------------------------------------------------------------------------- runner
class JobRunner:
    def __init__(self, storage: Storage, graph: GraphClient, budget_seconds: float,
                 clock: Callable[[], float] = time.monotonic):
        self.storage, self.graph, self.budget_seconds, self._clock = storage, graph, budget_seconds, clock

    async def run(self, job: str, team_id: str | None = None) -> dict:
        """Never raises: a failure becomes {status:"error", job_run_id, error_class} (and a failed job_runs row
        when the database still allows one) instead of a bare 500."""
        started = self._clock()
        try:
            result = await self._run(job, team_id, started)
        except Exception as e:  # infrastructure failure (lock table missing, DB down, ...): not a step failure
            log.exception("job %s could not run", job)
            result = {"status": "error", "job_run_id": None, "stats": {}, "next": False,
                      "error": error_text(e), "error_class": type(e).__name__}
            result["job_run_id"] = await self._record_failure(job, result)
        self._log(job, result, started)
        return result

    async def _record_failure(self, job: str, result: dict) -> int | None:
        try:
            run_id = await self.storage.start_job_run(job)
            await self.storage.finish_job_run(run_id, "error", {}, result["error"])
            return run_id
        except Exception:  # the database itself is the problem; the JSON response and the log still say so
            return None

    async def _run(self, job: str, team_id: str | None, started: float) -> dict:
        step = STEPS[job]
        async with self.storage.job_lock(LOCK, self.budget_seconds + LEASE_MARGIN_SECONDS) as got:
            run_id = await self.storage.start_job_run(job)
            if not got:
                await self.storage.finish_job_run(run_id, "busy", {})
                return {"status": "busy", "job_run_id": run_id, "stats": {}, "next": False}
            ctx = JobContext(self.storage, self.graph, Budget(self.budget_seconds, self._clock))
            before = dict(self.graph.stats)
            error: str | None = None
            error_class: str | None = None
            try:
                status = await step(ctx, team_id)
            except Exception as e:  # never leak the exception text (could carry URLs); type/status/code only
                log.exception("job %s failed", job)
                status, error, error_class = "error", error_text(e), type(e).__name__
            stats = {**dict(ctx.stats), **{f"graph_{k}": v - before[k] for k, v in self.graph.stats.items()}}
            await self.storage.finish_job_run(run_id, status, stats, error)
        result = {"status": status, "job_run_id": run_id, "stats": stats, "next": status == "partial"}
        if error:
            result.update(error=error, error_class=error_class)
        return result

    def _log(self, job: str, result: dict, started: float) -> None:
        log.info(json.dumps({"event": "job_run", "job": job, "status": result["status"],
                             "duration_s": round(self._clock() - started, 2), "stats": result["stats"]}))
