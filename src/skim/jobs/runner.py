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
from .ai_documents import ai_documents
from .ai_embed import ai_embed
from .ai_process import ai_process
from .enrich import enrich_team
from .refill import refill_empty
from .backfill import backfill_team, state_key
from .discover import discover_team
from .files_sync import delta_sync_channel, full_sync_channel
from .link_attachments import link_attachments

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
    """discover -> sync-files -> backfill (initial-sync teams only) -> link attachments (T9)."""
    results = [await step_discover(ctx, team_id)]
    results.append(await step_sync_files(ctx, team_id))
    for extra in EXTRA_RUN_ALL_STEPS:
        results.append(await extra(ctx, team_id))
    return "partial" if "partial" in results else "ok"


async def step_backfill(ctx: JobContext, team_id: str | None, force: bool = True) -> str:
    """Message history. /jobs/backfill is explicit (force: every selected team); run-all only does teams that still
    need their initial sync (P2-D-117/128)."""
    partial = False
    for team in select_teams(await ctx.storage.list_sync_teams(), team_id):
        if not force and not team.get("needs_initial_sync"):
            continue
        if ctx.options.get("force"):  # start over: drop saved cursors (a changed since_days only applies to a fresh one)
            for ch in await ctx.storage.list_channels(team["id"]):
                await ctx.storage.delete_sync_state(state_key(ch["id"]))
        if ctx.budget.expired():
            return "partial"
        status = await backfill_team(ctx, team, ctx.options["since_days"], ctx.options["max_messages"], error_text)
        partial = partial or status == "partial"
    return "partial" if partial else "ok"


async def step_backfill_initial(ctx: JobContext, team_id: str | None) -> str:
    return await step_backfill(ctx, team_id, force=False)


async def step_refill_empty(ctx: JobContext, team_id: str | None) -> str:
    partial = False
    for team in select_teams(await ctx.storage.list_sync_teams(), team_id):
        if ctx.budget.expired():
            return "partial"
        partial = partial or await refill_empty(ctx, team) == "partial"
    return "partial" if partial else "ok"


async def step_enrich_messages(ctx: JobContext, team_id: str | None) -> str:
    partial = False
    for team in select_teams(await ctx.storage.list_sync_teams(), team_id):
        if ctx.budget.expired():
            return "partial"
        partial = partial or await enrich_team(ctx, team, ctx.options["since_days"], error_text) == "partial"
    return "partial" if partial else "ok"


async def step_fix_text(ctx: JobContext, team_id: str | None) -> str:
    """P2-D-144: idempotent clean-up of message text that still carries HTML (no Graph calls)."""
    ctx.stats["texts_fixed"] += await ctx.storage.fix_markup_texts(1000)
    return "ok"


async def step_link_attachments(ctx: JobContext, team_id: str | None) -> str:
    teams = select_teams(await ctx.storage.list_sync_teams(), team_id) if team_id else [None]
    for team in teams:
        await link_attachments(ctx, team["id"] if team else None)
    return "ok"


async def step_ai_process(ctx: JobContext, team_id: str | None) -> str:
    if ctx.ai is None:
        ctx.stats["ai_disabled"] += 1
        return "ok"
    return await ai_process(ctx, team_id)


async def step_ai_documents(ctx: JobContext, team_id: str | None) -> str:
    if ctx.ai is None:
        ctx.stats["ai_disabled"] += 1
        return "ok"
    return await ai_documents(ctx, team_id)


async def step_ai_embed(ctx: JobContext, team_id: str | None) -> str:
    if ctx.ai is None:
        ctx.stats["ai_disabled"] += 1
        return "ok"
    return await ai_embed(ctx, team_id)


async def step_ai_reprocess(ctx: JobContext, team_id: str | None) -> str:
    """Re-run messages under another prompt version; old analyses stay and events are NOT touched (a replay of old
    messages would otherwise undo later changes)."""
    pv = ctx.options.get("prompt_version")
    if ctx.ai is not None and pv == ctx.ai.cfg.prompt_version:
        # The live version decides what is still pending for ai-process; analysing it here WITHOUT applying events
        # would mark messages done and their events would never be created.
        ctx.stats["ai_reprocess_rejected_live_version"] += 1
        return "ok"
    if ctx.ai is None or not pv:
        ctx.stats["ai_disabled" if ctx.ai is None else "ai_missing_prompt_version"] += 1
        return "ok"
    from dataclasses import replace
    pipeline = replace(ctx.ai.pipeline, prompt_version=pv)
    return await ai_process(ctx, team_id, pipeline=pipeline, apply_events=False)


async def step_ai_all(ctx: JobContext, team_id: str | None) -> str:
    """Phase 3 inside run-all (P3-D-017/018). Nothing at all happens when AI is off. An AI failure never undoes or hides the
    Phase 2 steps that already ran, and a crashing AI step never makes run-all "partial" (P3-DIFF-015): `next` is true only
    when a step stopped with work it has not tried yet (time budget or per-run limit); failures are counted in the stats and
    in /debug/ai-status instead, and failing items stop being retried after AI_MAX_ATTEMPTS."""
    if ctx.ai is None:
        return "ok"
    results = []
    for step in AI_STEPS:
        try:
            results.append(await step(ctx, team_id))
        except Exception as e:
            log.exception("ai step failed")
            ctx.stats["ai_step_errors"] += 1
            ctx.stats[f"ai_step_error_{type(e).__name__}"] += 1
    return "partial" if "partial" in results else "ok"


AI_STEPS: list[Step] = [step_ai_process, step_ai_documents, step_ai_embed]
EXTRA_RUN_ALL_STEPS: list[Step] = [step_fix_text, step_backfill_initial, step_enrich_messages, step_refill_empty, step_link_attachments, step_ai_all]
STEPS: dict[str, Step] = {"discover": step_discover, "sync-files": step_sync_files, "backfill": step_backfill,
                          "fix-text": step_fix_text,
                          "enrich-messages": step_enrich_messages,
                          "refill-empty": step_refill_empty,
                          "link-attachments": step_link_attachments, "ai-process": step_ai_process,
                          "ai-documents": step_ai_documents,
                          "ai-embed": step_ai_embed,
                          "ai-reprocess": step_ai_reprocess, "run-all": step_run_all}


# ---------------------------------------------------------------------------- runner
class JobRunner:
    def __init__(self, storage: Storage, graph: GraphClient, budget_seconds: float,
                 clock: Callable[[], float] = time.monotonic, backfill_since_days: int = 180,
                 backfill_max_messages: int = 2000, ai=None):
        self.storage, self.graph, self.budget_seconds, self._clock = storage, graph, budget_seconds, clock
        self.backfill_since_days, self.backfill_max_messages = backfill_since_days, backfill_max_messages
        self.ai = ai

    async def run(self, job: str, team_id: str | None = None, since_days: int | None = None,
                  force: bool = False, prompt_version: str | None = None) -> dict:
        """Never raises: a failure becomes {status:"error", job_run_id, error_class} (and a failed job_runs row
        when the database still allows one) instead of a bare 500."""
        started = self._clock()
        try:
            result = await self._run(job, team_id, started, since_days, force, prompt_version)
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

    async def _run(self, job: str, team_id: str | None, started: float, since_days: int | None,
                   force: bool = False, prompt_version: str | None = None) -> dict:
        step = STEPS[job]
        async with self.storage.job_lock(LOCK, self.budget_seconds + LEASE_MARGIN_SECONDS) as got:
            run_id = await self.storage.start_job_run(job)
            if not got:
                await self.storage.finish_job_run(run_id, "busy", {})
                return {"status": "busy", "job_run_id": run_id, "stats": {}, "next": False}
            ctx = JobContext(self.storage, self.graph, Budget(self.budget_seconds, self._clock),
                             options={"since_days": since_days or self.backfill_since_days,
                                      "max_messages": self.backfill_max_messages, "force": force,
                                      "prompt_version": prompt_version}, ai=self.ai)
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
