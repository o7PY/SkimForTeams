"""ai-documents step (FR-306): new/changed pdf, docx and pptx files -> text, chunks, events. Numbers only in stats and logs."""
from __future__ import annotations

import json
import logging
from collections import Counter

from ..ai import documents as docs
from ..ai.chunking import chunk_pages
from .common import JobContext

log = logging.getLogger("skim.ai")


async def ai_documents(ctx: JobContext, team_id: str | None, fetch=None) -> str:
    ai = ctx.ai
    cfg = ai.cfg
    fetch = fetch or (lambda d: docs.fetch_content(ctx.graph, d["drive_id"], d["item_id"], cfg.doc_max_bytes))
    before = Counter(ai.router.stats)
    status = "ok"
    for doc in await ctx.storage.list_pending_documents(cfg.max_docs_per_run):
        if team_id and team_id != doc["team_id"]:
            continue
        if ctx.budget.expired():
            status = "partial"
            break
        try:
            text_status, reason, extracted = "ok", None, None
            if doc.get("size_bytes") and doc["size_bytes"] > cfg.doc_max_bytes:
                raise docs.DocumentSkip("skipped", "too_large")
            extracted = docs.extract(doc.get("name"), await fetch(doc), cfg.doc_max_pages)
        except docs.DocumentSkip as s:
            text_status, reason = s.status, s.reason
        except docs.TransientFetchError as e:
            ctx.stats["ai_doc_fetch_transient"] += 1  # stays pending: retried by the next run
            log.warning(json.dumps({"event": "ai_doc_fetch_failed", "document_id": doc["id"], "error_class": str(e)[:40]}))
            continue
        chunks = chunk_pages(extracted.pages) if extracted else []
        if extracted and not chunks:
            text_status, reason = "skipped", "no_text"  # scanned images etc.; OCR is out of scope
        rows = [{"chunk_index": c.index, "page_from": c.page_from, "page_to": c.page_to, "text": c.text} for c in chunks]
        await ctx.storage.save_document_text(doc["id"], doc.get("ctag"), text_status, reason,
                                             extracted.page_count if extracted else None,
                                             sum(len(c.text) for c in chunks) if chunks else 0, rows)
        ctx.stats[f"ai_docs_{text_status}"] += 1
        ctx.stats["ai_doc_chunks"] += len(rows)
        for ch in rows:
            if ctx.budget.expired():
                status = "partial"
                break
            try:
                out = await ai.pipeline.process_document_chunk(doc, ch)
            except Exception as e:  # one bad chunk must not stop the rest
                ctx.stats["ai_exceptions"] += 1
                log.error(json.dumps({"event": "ai_doc_chunk_failed", "error_class": type(e).__name__, "document_id": doc["id"],
                                      "chunk_index": ch["chunk_index"]}))
                continue
            ctx.stats["ai_doc_events_created"] += out.created
            ctx.stats["ai_doc_events_changed"] += out.changed
            ctx.stats["ai_doc_events_restated"] += out.restated
            if out.status == "failed":
                ctx.stats["ai_doc_chunks_failed"] += 1
    for k, v in ai.router.stats.items():
        if v - before.get(k, 0):
            ctx.stats[f"ai_{k}"] += v - before.get(k, 0)
    return status
