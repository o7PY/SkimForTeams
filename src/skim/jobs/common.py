"""Shared plumbing for jobs: time budget, raw Graph storage, run context."""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..graph.client import GraphClient
from ..normalize_graph import payload_hash, strip_download_urls
from ..storage.base import Storage


class Budget:
    """Wall-clock budget for one job run (P2-D-109)."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self._clock, self._deadline = clock, clock() + seconds

    def expired(self) -> bool:
        return self._clock() >= self._deadline


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class JobContext:
    storage: Storage
    graph: GraphClient
    budget: Budget
    stats: Counter = field(default_factory=Counter)
    clock: Callable[[], datetime] = utcnow
    options: dict = field(default_factory=dict)  # since_days, max_messages, force_backfill


async def store_raw(storage: Storage, kind: str, endpoint: str, item_key: str | None, payload: Any) -> int:
    """P2-D-111: store the Graph payload (minus download URLs) before normalising. Identical payloads dedupe."""
    clean = strip_download_urls(payload)
    rid, _ = await storage.insert_graph_raw({
        "kind": kind, "endpoint": endpoint, "item_key": item_key,
        "dedupe_key": payload_hash(kind, clean), "payload": clean})
    return rid
