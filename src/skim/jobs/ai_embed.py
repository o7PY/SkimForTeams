"""ai-embed step (FR-307): embed new/edited messages and new doc chunks. Stats are numbers only."""
from __future__ import annotations

import json
import logging

from ..ai.embeddings import STORED_DIMS, chunk_ref, message_ref, message_text, names_of
from ..ai.providers.base import ProviderError
from .common import JobContext

log = logging.getLogger("skim.ai")
RUN_BATCH = 20


async def ai_embed(ctx: JobContext, team_id: str | None) -> str:
    emb = ctx.ai.embedder
    cfg = ctx.ai.cfg
    if emb is None or not emb.usable:
        ctx.stats["ai_embed_disabled"] += 1
        return "ok"
    status, budget_left = "ok", cfg.max_embeds_per_run
    pending = [i for i in await ctx.storage.list_unembedded(emb.model, budget_left) if team_id in (None, i["team_id"])]
    for start in range(0, len(pending), RUN_BATCH):
        if ctx.budget.expired():
            return "partial"
        batch = pending[start:start + RUN_BATCH]
        texts = [message_text(i) if i["item"] == "message" else i["text"] for i in batch]
        try:
            vecs = await emb.embed_documents(texts, [names_of(i) for i in batch])
        except ProviderError as e:
            ctx.stats["ai_embed_failed"] += len(batch)
            ctx.stats[f"ai_embed_error_{e.kind}"] += 1
            log.warning(json.dumps({"event": "ai_embed_failed", "provider": "gemini", "error_class": e.kind, "status": e.status}))
            return "partial"  # leave the rest pending; the next run retries (quota / outage)
        rows = []
        for i, v in zip(batch, vecs):
            kind, ref, key = message_ref(i) if i["item"] == "message" else chunk_ref(i)
            rows.append({"kind": kind, "ref": ref, "ref_key": key, "model": emb.model, "dims": STORED_DIMS,
                         "embedding": v, "team_id": i["team_id"]})
        ctx.stats["ai_embedded"] += await ctx.storage.save_embeddings(rows)
        if emb.interval:
            await emb.sleep(emb.interval)
    return status
