"""Embeddings (FR-307): redacted text -> 768-d vectors (Gemini), stored in pgvector. Search embeds the REDACTED query."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from .pipeline import _names
from .providers.base import ProviderError
from .redact import Redactor

log = logging.getLogger("skim.ai")
STORED_DIMS = 768  # migration 005: vector(768)
BATCH = 20
MAX_CHARS = 6000  # per text sent for embedding


def message_text(m: dict) -> str:
    return "\n".join(x for x in (m.get("subject"), m.get("text")) if x)


@dataclass
class Embedder:
    provider: object            # needs .embed(model, texts, dims, task)
    model: str
    dims: int = STORED_DIMS
    interval: float = 0.0       # seconds between batches (free-tier pacing)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

    @property
    def usable(self) -> bool:
        return self.dims == STORED_DIMS

    async def embed_documents(self, texts: list[list[str] | str], names: list[list[str]]) -> list[list[float]]:
        """texts[i] is redacted with names[i] (that item's author / mentioned names) before it leaves the process."""
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            batch = [Redactor(n).redact(t)[:MAX_CHARS] for t, n in zip(texts[i:i + BATCH], names[i:i + BATCH])]
            if i and self.interval:
                await self.sleep(self.interval)
            vecs, _ = await self.provider.embed(self.model, batch, self.dims, "RETRIEVAL_DOCUMENT")
            out += vecs
        return out

    async def embed_query(self, q: str) -> list[float]:
        vecs, _ = await self.provider.embed(self.model, [Redactor([]).redact(q)[:MAX_CHARS]], self.dims, "RETRIEVAL_QUERY")
        return vecs[0]


def message_ref(m: dict) -> tuple[str, dict, str]:
    ref = {"conversation_id": m["conversation_id"], "message_id": m["message_id"], "version": m["version"]}
    return "message", ref, f"msg:{m['conversation_id']}:{m['message_id']}:v{m['version']}"


def chunk_ref(c: dict) -> tuple[str, dict, str]:
    return "doc_chunk", {"document_id": c["document_id"], "chunk_index": c["chunk_index"], "chunk_id": c["id"]}, f"chunk:{c['id']}"


def names_of(item: dict) -> list[str]:
    if "message_id" in item:
        return _names(item)
    return [n for n in (item.get("created_by_name"), item.get("modified_by_name")) if n]
