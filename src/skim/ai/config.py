"""AI_* settings (PHASE3 §7). Separate from Settings so Phases 1-2 never depend on it; model names live only here."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Mapping

log = logging.getLogger("skim.ai")
_TRUE = {"1", "true", "yes", "on"}


def _isnum(v) -> bool:
    try:
        return v in (None, "") or float(v) >= 0
    except ValueError:
        return False


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    groq_api_key: str | None = field(default=None, repr=False)
    gemini_api_key: str | None = field(default=None, repr=False)
    groq_model: str = ""
    nim_api_key: str | None = field(default=None, repr=False)
    nim_model: str = ""   # optional third provider (NVIDIA NIM); used only when both NIM_API_KEY and NIM_MODEL are set
    gemini_model: str = "gemini-2.5-flash-lite"
    embed_model: str = "gemini-embedding-001"
    embed_dims: int = 768
    prompt_version: str = "p3-v1"
    max_messages_per_run: int = 200
    max_docs_per_run: int = 10
    max_embeds_per_run: int = 500
    max_attempts: int = 3
    min_call_interval: float = 2.0   # seconds between calls to one provider (free-tier tokens/minute)
    max_retry_after: float = 20.0
    rate_retries: int = 1
    doc_max_bytes: int = 20_000_000
    doc_max_pages: int = 50
    timezone: str = "Asia/Kolkata"
    staff_aad_ids: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "AIConfig":
        """AI_ENABLED=false, or a missing key/model, switches Phase 3 off with ONE log line (never raises)."""
        env = os.environ if env is None else env

        def num(name, default):
            try:
                return max(1, int(env.get(name) or default))
            except ValueError:
                return default

        if (env.get("AI_ENABLED") or "true").strip().lower() not in _TRUE:
            log.info("AI disabled: AI_ENABLED is not true")
            return cls()
        missing = [k for k in ("GROQ_API_KEY", "GEMINI_API_KEY", "GROQ_MODEL") if not env.get(k)]
        if missing:
            log.warning("AI disabled: missing %s", ", ".join(missing))  # names only, never values
            return cls()
        return cls(
            enabled=True, groq_api_key=env["GROQ_API_KEY"], gemini_api_key=env["GEMINI_API_KEY"],
            groq_model=env["GROQ_MODEL"], nim_api_key=env.get("NIM_API_KEY") or None, nim_model=env.get("NIM_MODEL") or "", gemini_model=env.get("GEMINI_MODEL") or cls.gemini_model,
            embed_model=env.get("EMBED_MODEL") or cls.embed_model, embed_dims=num("EMBED_DIMS", 768),
            prompt_version=env.get("PROMPT_VERSION") or cls.prompt_version,
            max_messages_per_run=num("AI_MAX_MESSAGES_PER_RUN", 200), max_docs_per_run=num("AI_MAX_DOCS_PER_RUN", 10),
            max_embeds_per_run=num("AI_MAX_EMBEDS_PER_RUN", 500), max_attempts=num("AI_MAX_ATTEMPTS", 3),
            min_call_interval=float(env.get("AI_MIN_CALL_INTERVAL") or 2.0) if _isnum(env.get("AI_MIN_CALL_INTERVAL")) else 2.0,
            doc_max_bytes=num("DOC_MAX_BYTES", 20_000_000), doc_max_pages=num("DOC_MAX_PAGES", 50),
            timezone=env.get("COURSE_TIMEZONE") or "Asia/Kolkata",
            staff_aad_ids=frozenset(x.strip() for x in (env.get("STAFF_AAD_IDS") or "").split(",") if x.strip()))
