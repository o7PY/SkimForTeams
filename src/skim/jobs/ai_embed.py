"""ai-embed step (FR-307): embed new/edited messages and new doc chunks. Stats are numbers only.

P3-DIFF-015: a provider/quota/outage error stops the step quietly (items stay pending, next run retries, no attempt is burned
and next stays false). A rejected input (bad_request) is isolated: the batch is retried item by item and each bad item
counts one attempt in `embed_failures`, then is skipped after AI_MAX_ATTEMPTS. `partial` only when untried items remain."""
from __future__ import annotations

import json
import logging

from ..ai.embeddings import STORED_DIMS, chunk_ref, message_ref, message_text, names_of
from ..ai.providers.base import ProviderError
from .common import JobContext

log = logging.getLogger("skim.ai")
RUN_BATCH = 20


def _row(item: dict, vec: list[float], model: str) -> dict:
    kind, ref, key = message_ref(item) if item["item"] == "message" else chunk_ref(item)
    return {"kind": kind, "ref": ref, "ref_key": key, "model": model, "dims": STORED_DIMS, "embedding": vec,
            "team_id": item["team_id"]}


def _text(item: dict) -> str:
    return message_text(item) if item["item"] == "message" else item["text"]


async def ai_embed(ctx: JobContext, team_id: str | None) -> str:
    emb = ctx.ai.embedder
    cfg = ctx.ai.cfg
    if emb is None or not emb.usable:
        ctx.stats["ai_embed_disabled"] += 1
        return "ok"
    limit = cfg.max_embeds_per_run
    found = await ctx.storage.list_unembedded(emb.model, limit + 1, cfg.max_attempts)
    status = "partial" if len(found) > limit else "ok"
    pending = [i for i in found[:limit] if team_id in (None, i["team_id"])]
    for start in range(0, len(pending), RUN_BATCH):
        if ctx.budget.expired():
            return "partial"
        batch = pending[start:start + RUN_BATCH]
        try:
            vecs = await emb.embed_documents([_text(i) for i in batch], [names_of(i) for i in batch])
        except ProviderError as e:
            if e.kind != "bad_request":  # quota / outage / auth / timeout: not the items' fault; retry next run
                ctx.stats[f"ai_embed_error_{e.kind}"] += 1
                log.warning(json.dumps({"event": "ai_embed_failed", "provider": "gemini", "error_class": e.kind, "status": e.status}))
                return "ok"
            await _isolate(ctx, emb, batch)
            continue
        except Exception as e:  # a bug on this batch: isolate it too, never loop on it
            ctx.stats["ai_exceptions"] += 1
            log.error(json.dumps({"event": "ai_embed_batch_failed", "error_class": type(e).__name__}))
            await _isolate(ctx, emb, batch)
            continue
        ctx.stats["ai_embedded"] += await ctx.storage.save_embeddings([_row(i, v, emb.model) for i, v in zip(batch, vecs)])
        if emb.interval:
            await emb.sleep(emb.interval)
    return status


async def _isolate(ctx: JobContext, emb, batch: list[dict]) -> None:
    """Embed the batch's items one at a time so one rejected text cannot block the others."""
    good, bad = [], []
    for i in batch:
        try:
            (vec,) = await emb.embed_documents([_text(i)], [names_of(i)])
            good.append(_row(i, vec, emb.model))
        except ProviderError as e:
            if e.kind != "bad_request":
                ctx.stats[f"ai_embed_error_{e.kind}"] += 1
                break  # the provider itself is unhappy now: stop, the rest stays pending
            bad.append((i, "bad_request"))
        except Exception as e:
            bad.append((i, type(e).__name__))
        if emb.interval:
            await emb.sleep(emb.interval)
    if good:
        ctx.stats["ai_embedded"] += await ctx.storage.save_embeddings(good)
    if bad:
        rows = []
        for i, cls in bad:
            _, _, key = message_ref(i) if i["item"] == "message" else chunk_ref(i)
            rows.append({"ref_key": key, "model": emb.model, "error_class": cls})
        await ctx.storage.record_embed_failures(rows)
        ctx.stats["ai_embed_failed"] += len(bad)
