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


class ScriptedProvider(FakeProvider):
    """Answers by the text found inside <message>...</message> (so tests can script a whole conversation)."""

    def __init__(self, script: dict, name="groq", model="scripted"):
        super().__init__(name, [], model)
        self.script = script

    async def complete_json(self, system, user, schema, repair=None):
        import re
        from skim.ai.providers.base import ProviderReply
        self.calls.append({"system": system, "user": user, "repair": repair})
        if "<candidates>" in user:  # tie-break
            ans = self.script.get("__tiebreak__", "new")
            return ProviderReply(json.dumps({"match": ans}), 1)
        text = re.search(r"<(?:message|document)>(.*?)</(?:message|document)>", user, re.S).group(1)
        reply = self.script[text] if text in self.script else next(
            (v for k, v in self.script.items() if k and k in text), None) or self.script[text if "" not in self.script else ""]  # exact, else substring, else ""
        if isinstance(reply, ProviderErrorLike):
            raise reply.error
        return ProviderReply(json.dumps(reply), 10)


class ProviderErrorLike:
    def __init__(self, error):
        self.error = error
