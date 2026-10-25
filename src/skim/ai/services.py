"""Assemble the AI pieces once per process (providers -> router -> pipeline) and hand them to the job runner."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from .config import AIConfig
from .pipeline import Pipeline
from .providers.gemini import GeminiProvider
from .providers.groq import GroqProvider
from .providers.nim import NimProvider
from .providers.router import Router

log = logging.getLogger("skim.ai")


@dataclass
class AIServices:
    cfg: AIConfig
    router: Router
    pipeline: Pipeline

    @classmethod
    def build(cls, cfg: AIConfig, storage: Any, providers: list | None = None) -> "AIServices":
        if providers is None:
            providers = [GroqProvider(cfg.groq_api_key, cfg.groq_model), GeminiProvider(cfg.gemini_api_key, cfg.gemini_model)]
            if cfg.nim_api_key and cfg.nim_model:  # optional last-resort backup
                providers.append(NimProvider(cfg.nim_api_key, cfg.nim_model))
        router = Router(providers, min_interval=cfg.min_call_interval, max_retry_after=cfg.max_retry_after,
                        rate_retries=cfg.rate_retries)
        return cls(cfg, router, Pipeline(storage, router, cfg))

    @classmethod
    def from_env(cls, storage: Any, env: Mapping[str, str] | None = None) -> "AIServices | None":
        """None (and one log line) when AI is off or a key is missing: Phases 1-2 then behave exactly as before."""
        cfg = AIConfig.from_env(env)
        return cls.build(cfg, storage) if cfg.enabled else None

    async def close(self) -> None:
        await self.router.close()
