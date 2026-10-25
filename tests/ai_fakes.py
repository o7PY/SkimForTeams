"""Fake LLM providers for tests (no network)."""
from __future__ import annotations

import json

from skim.ai.providers.base import ProviderError, ProviderReply


class FakeProvider:
    """Replies come from a list of str | ProviderError | callable(system, user) -> str; the last one repeats."""

    def __init__(self, name="groq", replies=None, model="fake-model"):
        self.name, self.model, self.replies, self.calls = name, model, list(replies or []), []

    async def complete_json(self, system, user, schema, repair=None):
        self.calls.append({"system": system, "user": user, "repair": repair})
        r = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if callable(r):
            r = r(system, user)
        if isinstance(r, ProviderError):
            raise r
        return ProviderReply(r if isinstance(r, str) else json.dumps(r), 10)

    async def close(self):
        pass


def extraction(category="chitchat", items=None, confidence="high"):
    return {"category": category, "confidence": confidence, "items": items or []}


def item(action="create", type="quiz", title="Quiz 1", when=None, **kw):
    base = {"action": action, "type": type, "title": title, "is_deadline": type in ("assignment", "project"),
            "refers_to_existing": action != "create", "reference_hint": None,
            "when": {"kind": "none", "date": None, "time": None, "end_time": None, "relative": None, "text": ""},
            "venue": None, "details": None, "confidence": "high"}
    base.update(kw)
    if when:
        base["when"] = {**base["when"], **when}
    return base
