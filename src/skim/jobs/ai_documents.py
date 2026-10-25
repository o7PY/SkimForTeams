"""ai-documents step (FR-306): new/changed pdf, docx and pptx files -> text, chunks, events. Numbers only in stats and logs.

P3-DIFF-015: every file is isolated. Any unexpected failure counts ONE attempt on that file (status 'error', the error class
stored) and the loop moves on; after AI_MAX_ATTEMPTS the file is no longer pending until its ctag changes. A step is
`partial` (next=true) only when it stopped with files it has not tried yet (time budget or per-run limit)."""
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
    limit = cfg.max_docs_per_run
    pending = await ctx.storage.list_pending_documents(limit + 1, cfg.max_attempts)
    status = "partial" if len(pending) > limit else "ok"
    for doc in pending[:limit]:
        if team_id and team_id != doc["team_id"]:
            continue
        if ctx.budget.expired():
            status = "partial"
            break
        try:
            await _one(ctx, doc, fetch)
        except Exception as e:  # nothing about one file may stop the others or the run
            await _record_error(ctx, doc, e)
    for k, v in ai.router.stats.items():
        if v - before.get(k, 0):
            ctx.stats[f"ai_{k}"] += v - before.get(k, 0)
    return status


async def _record_error(ctx: JobContext, doc: dict, exc: Exception) -> None:
    ctx.stats["ai_doc_errors"] += 1
    try:
        attempts = await ctx.storage.record_document_error(doc["id"], doc.get("ctag"), type(exc).__name__)
    except Exception as e:  # the database itself is unwell; log and carry on with the next file
        ctx.stats["ai_record_errors"] += 1
        log.error(json.dumps({"event": "ai_record_failed", "error_class": type(e).__name__}))
        return
    log.warning(json.dumps({"event": "ai_doc_failed", "document_id": doc["id"], "error_class": type(exc).__name__,
                            "attempts": attempts}))


async def _one(ctx: JobContext, doc: dict, fetch) -> None:
    ai, cfg = ctx.ai, ctx.ai.cfg
    text_status, reason, extracted = "ok", None, None
    try:
        if doc.get("size_bytes") and doc["size_bytes"] > cfg.doc_max_bytes:
            raise docs.DocumentSkip("skipped", "too_large")
        extracted = docs.extract(doc.get("name"), await fetch(doc), cfg.doc_max_pages)
    except docs.DocumentSkip as s:
        text_status, reason = s.status, s.reason
    except docs.TransientFetchError as e:
        ctx.stats["ai_doc_fetch_transient"] += 1
        log.warning(json.dumps({"event": "ai_doc_fetch_failed", "document_id": doc["id"], "error_class": str(e)[:40]}))
        raise  # counts one attempt; retried by the next run until AI_MAX_ATTEMPTS
    if extracted and sum(len(p) for p in extracted.pages) > cfg.doc_max_chars:
        extracted, text_status, reason = None, "skipped", "too_much_text"  # bounds chunks, DB rows and LLM calls
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
            return  # the rest of this file's chunks wait for the next run only if the file changes; text is saved
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
