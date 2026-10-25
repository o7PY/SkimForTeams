"""NVIDIA NIM (build.nvidia.com hosted API, OpenAI-compatible chat completions). Optional backup provider.

Structured output goes through NIM's `nvext.guided_json`, with the schema also spelled out in the system prompt
(models without guided decoding still follow it, and the router validates + repairs either way). If NIM rejects
`nvext`, the provider drops it for the rest of the process and relies on the prompt alone."""
from __future__ import annotations

import json

import aiohttp

from .groq import GroqProvider

URL = "https://integrate.api.nvidia.com/v1/chat/completions"


class NimProvider(GroqProvider):
    name = "nim"
    _reject_words = ("nvext", "guided", "response_format", "schema")

    def __init__(self, api_key: str, model: str, timeout: float = 60.0, url: str = URL,
                 session: aiohttp.ClientSession | None = None):
        super().__init__(api_key, model, timeout, url, session)

    def _body(self, system: str, user: str, schema: dict, repair) -> dict:
        messages = [{"role": "system", "content": system + "\n\nReply with ONE JSON object and nothing else, matching "
                     "this JSON schema:\n" + json.dumps(schema)}, {"role": "user", "content": user}]
        if repair:
            messages += [{"role": "assistant", "content": repair[0][:4000]},
                         {"role": "user", "content": "That reply was invalid: " + repair[1][:500]
                          + " Reply again with ONLY the corrected JSON."}]
        body: dict = {"model": self.model, "temperature": 0, "max_tokens": 3000, "messages": messages}
        if self._schema_mode:
            body["nvext"] = {"guided_json": schema}
        return body
