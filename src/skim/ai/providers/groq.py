"""Groq chat completions (OpenAI-compatible). Strict json_schema first; json_object if the model rejects schemas."""
from __future__ import annotations

import asyncio
import json

import logging

import aiohttp

from .base import ProviderError, ProviderReply, classify_status

log = logging.getLogger("skim.ai")
URL = "https://api.groq.com/openai/v1/chat/completions"


class GroqProvider:
    name = "groq"
    _reject_words = ("schema",)  # a 400 mentioning one of these means "structured output not supported"

    def __init__(self, api_key: str, model: str, timeout: float = 45.0, url: str = URL,
                 session: aiohttp.ClientSession | None = None):
        self._key, self.model, self._timeout, self._url = api_key, model, timeout, url
        self._session, self._own = session, session is None
        self._schema_mode = True  # flips to False once the model rejects json_schema

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    def _body(self, system: str, user: str, schema: dict, repair) -> dict:
        messages = [{"role": "system", "content": system}]
        if not self._schema_mode:
            messages[0]["content"] += "\n\nReply with ONE JSON object matching this JSON schema:\n" + json.dumps(schema)
        messages.append({"role": "user", "content": user})
        if repair:
            messages += [{"role": "assistant", "content": repair[0][:4000]},
                         {"role": "user", "content": "That reply was invalid: " + repair[1][:500]
                          + " Reply again with ONLY the corrected JSON."}]
        body: dict = {"model": self.model, "temperature": 0, "messages": messages, "max_completion_tokens": 3000,
                      "response_format": {"type": "json_schema", "json_schema": {"name": "extraction", "strict": True,
                                                                                "schema": schema}}
                      if self._schema_mode else {"type": "json_object"}}
        if self.model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"
        return body

    async def complete_json(self, system, user, schema, repair=None) -> ProviderReply:
        for attempt in (1, 2):
            try:
                async with (await self._sess()).post(
                        self._url, json=self._body(system, user, schema, repair),
                        headers={"Authorization": f"Bearer {self._key}"},
                        timeout=aiohttp.ClientTimeout(total=self._timeout)) as r:
                    data = await r.json(content_type=None)
                    if r.status == 400 and self._schema_mode and attempt == 1 and any(w in json.dumps(data).lower() for w in self._reject_words):
                        self._schema_mode = False  # e.g. "does not support response format json_schema"
                        log.warning(json.dumps({"event": "ai_schema_mode_off", "provider": self.name, "model": self.model,
                                                "error_code": str((data.get("error") or {}).get("code") or (data.get("error") or {}).get("type") or "")[:60]}))
                        continue
                    if r.status >= 400:
                        hint = ((data or {}).get("error") or {}).get("code") or ""
                        raise classify_status(r.status, r.headers.get("retry-after"), str(hint))
                    choice = data["choices"][0]
                    return ProviderReply(choice["message"].get("content") or "", (data.get("usage") or {}).get("total_tokens"))
            except asyncio.TimeoutError:
                raise ProviderError("timeout") from None
            except aiohttp.ClientError as e:
                raise ProviderError("network", detail=type(e).__name__) from None
            except (KeyError, IndexError, TypeError, ValueError):
                raise ProviderError("server", detail="malformed response") from None
        raise ProviderError("bad_request", 400, detail="schema rejected")

    async def close(self) -> None:
        if self._session is not None and self._own:
            await self._session.close()
