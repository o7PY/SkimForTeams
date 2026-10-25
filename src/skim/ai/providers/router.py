"""Provider router (P3-D-002, P3-D-005, FR-301): primary, then fallback; one repair retry on invalid output;
Retry-After respected; per-run stats. Never logs prompts, outputs or keys."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from ..schemas import strict_json_schema
from .base import LLMProvider, ProviderError

log = logging.getLogger("skim.ai")
T = TypeVar("T", bound=BaseModel)
MAX_RETRY_AFTER_SECONDS = 20.0  # longer waits are not worth blocking a 240 s job for: go to the fallback


class AllProvidersFailed(Exception):
    def __init__(self, error_class: str):
        super().__init__(error_class)
        self.error_class = error_class


@dataclass
class Completion:
    value: BaseModel
    provider: str
    model: str
    raw: str


class Router:
    def __init__(self, providers: list[LLMProvider], sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 min_interval: float = 0.0, max_retry_after: float = MAX_RETRY_AFTER_SECONDS, rate_retries: int = 1,
                 clock: Callable[[], float] = time.monotonic):
        """min_interval: minimum seconds between calls to the same provider (free-tier tokens-per-minute pacing).
        max_retry_after / rate_retries: how long and how often to wait out a 429 before giving up on a provider."""
        self.providers, self._sleep, self._clock = providers, sleep, clock
        self.min_interval, self.max_retry_after, self.rate_retries = min_interval, max_retry_after, rate_retries
        self._last_call: dict[str, float] = {}
        self.stats: Counter = Counter()

    async def complete(self, model_cls: type[T], system: str, user: str) -> Completion:
        schema = strict_json_schema(model_cls)
        last = "no_providers"
        for i, p in enumerate(self.providers):
            if i:
                self.stats["fallbacks"] += 1
            try:
                return await self._with(p, model_cls, schema, system, user)
            except _Invalid:
                last = "invalid_output"
                log.warning(json.dumps({"event": "ai_provider_failed", "provider": p.name, "model": p.model,
                                        "error_class": "invalid_output", "status": None}))
            except ProviderError as e:
                last = e.kind
                log.warning(json.dumps({"event": "ai_provider_failed", "provider": p.name, "model": p.model,
                                        "error_class": e.kind, "status": e.status,
                                        "retry_after": e.retry_after}))
            self.stats[f"failed_{p.name}"] += 1
        self.stats["all_failed"] += 1
        raise AllProvidersFailed(last)

    async def _with(self, p: LLMProvider, model_cls, schema, system, user) -> Completion:
        reply = await self._call(p, system, user, schema, None)
        try:
            return Completion(_parse(model_cls, reply.text), p.name, p.model, reply.text)
        except ValueError as e:
            self.stats["repairs"] += 1
            reply2 = await self._call(p, system, user, schema, (reply.text, str(e)))
            try:
                return Completion(_parse(model_cls, reply2.text), p.name, p.model, reply2.text)
            except ValueError:
                raise _Invalid() from None

    async def _pace(self, p: LLMProvider) -> None:
        if self.min_interval:
            wait = self._last_call.get(p.name, -1e9) + self.min_interval - self._clock()
            if wait > 0:
                await self._sleep(wait)
        self._last_call[p.name] = self._clock()

    async def _call(self, p: LLMProvider, system, user, schema, repair):
        for attempt in range(self.rate_retries + 1):
            await self._pace(p)
            self.stats["calls"] += 1
            self.stats[f"calls_{p.name}"] += 1
            try:
                reply = await p.complete_json(system, user, schema, repair)
                break
            except ProviderError as e:
                wait = e.retry_after if e.retry_after is not None else 5.0 * (attempt + 1)
                if e.kind != "rate_limit" or attempt >= self.rate_retries or wait > self.max_retry_after:
                    raise
                self.stats["rate_limited"] += 1
                await self._sleep(wait)  # respect Retry-After
        if reply.tokens:
            self.stats["tokens"] += reply.tokens
        return reply

    async def close(self) -> None:
        for p in self.providers:
            await p.close()


class _Invalid(Exception):
    pass


def _parse(model_cls: type[T], text: str) -> T:
    """JSON text -> validated model. ValueError (incl. pydantic's) on anything wrong."""
    a, b = text.find("{"), text.rfind("}")
    if 0 < a or (a == 0 and b < len(text.strip()) - 1):
        text = text[a:b + 1] if a >= 0 and b > a else text  # tolerate ```json fences / chatter around the object
    try:
        return model_cls.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as e:
        raise ValueError(str(e)[:400]) from None
