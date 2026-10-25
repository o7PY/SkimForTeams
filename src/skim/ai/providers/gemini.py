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

    async def embed(self, model: str, texts: list[str], dims: int, task: str) -> tuple[list[list[float]], int | None]:
        """Batch-embed REDACTED texts. task: RETRIEVAL_DOCUMENT | RETRIEVAL_QUERY. Returns (vectors, tokens or None).
        Raises ProviderError; a wrong-sized or short answer is a 'server' error (nothing is stored from it)."""
        body = {"requests": [{"model": f"models/{model}", "content": {"parts": [{"text": t}]},
                              "outputDimensionality": dims, "taskType": task} for t in texts]}
        data, _ = await self._post(f"models/{model}:batchEmbedContents", body)
        try:
            vecs = [[float(x) for x in e["values"]] for e in data["embeddings"]]
        except (KeyError, TypeError, ValueError):
            raise ProviderError("server", detail="malformed embeddings") from None
        if len(vecs) != len(texts) or any(len(v) != dims for v in vecs):
            raise ProviderError("server", detail="unexpected embedding shape")
        return vecs, None

    async def close(self) -> None:
        if self._session is not None and self._own:
            await self._session.close()
