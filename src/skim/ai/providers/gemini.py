"""Gemini generateContent in JSON mode (fallback provider) + embeddings (T10 adds embed())."""
from __future__ import annotations

import asyncio
import json

import aiohttp

from .base import ProviderError, ProviderReply, classify_status

BASE = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 45.0, base: str = BASE,
                 session: aiohttp.ClientSession | None = None):
        self._key, self.model, self._timeout, self._base = api_key, model, timeout, base
        self._session, self._own = session, session is None

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _post(self, path: str, body: dict) -> tuple[dict, dict]:
        try:
            async with (await self._sess()).post(f"{self._base}/{path}", json=body, headers={"x-goog-api-key": self._key},
                                                 timeout=aiohttp.ClientTimeout(total=self._timeout)) as r:
                data = await r.json(content_type=None)
                if r.status >= 400:
                    status = ((data or {}).get("error") or {}).get("status") or ""
                    raise classify_status(r.status, r.headers.get("retry-after"), str(status))
                return data, dict(r.headers)
        except asyncio.TimeoutError:
            raise ProviderError("timeout") from None
        except aiohttp.ClientError as e:
            raise ProviderError("network", detail=type(e).__name__) from None

    async def complete_json(self, system, user, schema, repair=None) -> ProviderReply:
        contents = [{"role": "user", "parts": [{"text": user}]}]
        if repair:
            contents += [{"role": "model", "parts": [{"text": repair[0][:4000]}]},
                         {"role": "user", "parts": [{"text": "That reply was invalid: " + repair[1][:500]
                                                     + " Reply again with ONLY the corrected JSON."}]}]
        body = {"systemInstruction": {"parts": [{"text": system + "\n\nReply with ONE JSON object matching this JSON schema:\n"
                                                 + json.dumps(schema)}]},
                "contents": contents,
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        data, _ = await self._post(f"models/{self.model}:generateContent", body)
        try:
            return ProviderReply(data["candidates"][0]["content"]["parts"][0]["text"],
                                 (data.get("usageMetadata") or {}).get("totalTokenCount"))
        except (KeyError, IndexError, TypeError):
            raise ProviderError("server", detail="no candidate") from None

    async def close(self) -> None:
        if self._session is not None and self._own:
            await self._session.close()
