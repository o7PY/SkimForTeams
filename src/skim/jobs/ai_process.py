"""ai-process step (FR-303): new/edited channel messages -> events. Stats are numbers only (FR-311, §8)."""
from __future__ import annotations

import json
import logging
from collections import Counter

from ..ai.pipeline import Pipeline
from .common import JobContext

log = logging.getLogger("skim.ai")


async def ai_process(ctx: JobContext, team_id: str | None, pipeline: Pipeline | None = None, apply_events: bool = True) -> str:
    ai = ctx.ai
    pipeline = pipeline or ai.pipeline
    cfg = ai.cfg
    before = Counter(ai.router.stats)
    pending = await ctx.storage.list_pending_messages(pipeline.prompt_version, cfg.max_attempts, cfg.max_messages_per_run)
    status = "ok"
    for msg in pending:
        if team_id and team_id not in (msg["team_id"],):
            continue
        if ctx.budget.expired():
            status = "partial"
            break
        try:
            out = await pipeline.process(msg, apply_events=apply_events)
        except Exception as e:  # a bug or DB hiccup on one message must not stop the rest
            ctx.stats["ai_exceptions"] += 1
            log.error(json.dumps({"event": "ai_item_failed", "error_class": type(e).__name__,
                                  "conversation_id": msg["conversation_id"], "message_id": msg["message_id"]}))
            await _record_pipeline_error(ctx, pipeline, msg)
            continue
        ctx.stats["ai_messages"] += 1
        ctx.stats["ai_events_created"] += out.created
        ctx.stats["ai_events_changed"] += out.changed
        ctx.stats["ai_events_restated"] += out.restated
        ctx.stats["ai_items_ignored_student"] += out.ignored_items
        if out.status == "failed":
            ctx.stats["ai_failed"] += 1
            log.warning(json.dumps({"event": "ai_item_failed", "error_class": out.error_class,
                                    "conversation_id": msg["conversation_id"], "message_id": msg["message_id"]}))
    if apply_events:
        ctx.stats["ai_events_flagged_deleted"] += await ctx.storage.flag_events_of_deleted_messages()
    for k, v in ai.router.stats.items():
        if v - before.get(k, 0):
            ctx.stats[f"ai_{k}"] += v - before.get(k, 0)
    return status


async def _record_pipeline_error(ctx: JobContext, pipeline: Pipeline, msg: dict) -> None:
    """Count the failed attempt so a deterministic bug stops being retried after AI_MAX_ATTEMPTS."""
    try:
        await ctx.storage.commit_message_analysis(
            {"conversation_id": msg["conversation_id"], "message_id": msg["message_id"], "message_version": msg["version"],
             "prompt_version": pipeline.prompt_version, "status": "failed", "error_class": "pipeline_error"}, [],
            {"kind": "message", "team_id": msg["team_id"], "at": msg.get("created_at"),
             "ref": {"conversation_id": msg["conversation_id"], "message_id": msg["message_id"]}})
    except Exception:
        pass
