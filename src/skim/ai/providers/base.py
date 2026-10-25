"""LLM provider protocol + the error vocabulary the router understands (FR-301)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderError(Exception):
    """kind: rate_limit | server | timeout | network | auth | bad_request. Never carries prompts or keys."""

    def __init__(self, kind: str, status: int | None = None, retry_after: float | None = None, detail: str = ""):
        super().__init__(f"{kind} {status or ''} {detail}".strip())
        self.kind, self.status, self.retry_after, self.detail = kind, status, retry_after, detail[:200]


@dataclass
class ProviderReply:
    text: str
    tokens: int | None = None


class LLMProvider(Protocol):
    name: str   # groq | gemini
    model: str

    async def complete_json(self, system: str, user: str, schema: dict,
                            repair: tuple[str, str] | None = None) -> ProviderReply:
        """Return the model's JSON text. `repair` = (previous bad output, validation error) for the one repair retry."""

    async def close(self) -> None: ...


def classify_status(status: int, retry_after: str | None, body_hint: str = "") -> ProviderError:
    try:
        ra = float(retry_after) if retry_after else None
    except ValueError:
        ra = None
    kind = ("rate_limit" if status == 429 else "auth" if status in (401, 403) else "server" if status >= 500
            else "bad_request")
    if status == 402:
        kind = "auth"  # billing/credits: not retryable on this provider
    return ProviderError(kind, status, ra, body_hint)
