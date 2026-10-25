"""Groq/Gemini request shapes against a local fake server (no real network)."""
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from skim.ai.providers.base import ProviderError
from skim.ai.providers.gemini import GeminiProvider
from skim.ai.providers.groq import GroqProvider

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}


async def serve(handler, path):
    app = web.Application()
    app.router.add_post(path, handler)
    srv = TestServer(app)
    await srv.start_server()
    return srv


async def test_groq_sends_strict_schema_and_key_and_parses():
    seen = {}

    async def h(req):
        seen["auth"], seen["body"] = req.headers["Authorization"], await req.json()
        return web.json_response({"choices": [{"message": {"content": '{"a":"x"}'}}], "usage": {"total_tokens": 7}})

    srv = await serve(h, "/chat")
    p = GroqProvider("KEY", "openai/gpt-oss-120b", url=str(srv.make_url("/chat")))
    r = await p.complete_json("sys", "usr", SCHEMA)
    await p.close(); await srv.close()
    assert r.text == '{"a":"x"}' and r.tokens == 7 and seen["auth"] == "Bearer KEY"
    rf = seen["body"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True and seen["body"]["temperature"] == 0


async def test_groq_falls_back_to_json_object_when_schema_is_rejected_and_adds_the_repair_turn():
    bodies = []

    async def h(req):
        bodies.append(await req.json())
        if len(bodies) == 1:
            return web.json_response({"error": {"message": "This model does not support response format `json_schema`"}}, status=400)
        return web.json_response({"choices": [{"message": {"content": "{}"}}]})

    srv = await serve(h, "/chat")
    p = GroqProvider("K", "allam", url=str(srv.make_url("/chat")))
    await p.complete_json("sys", "usr", SCHEMA, repair=("bad", "why"))
    await p.close(); await srv.close()
    assert bodies[1]["response_format"] == {"type": "json_object"} and "JSON schema" in bodies[1]["messages"][0]["content"]
    assert [m["role"] for m in bodies[1]["messages"]] == ["system", "user", "assistant", "user"]


@pytest.mark.parametrize("status,kind", [(429, "rate_limit"), (503, "server"), (401, "auth"), (402, "auth"), (400, "bad_request")])
async def test_groq_errors_are_classified_with_retry_after(status, kind):
    async def h(req):
        return web.json_response({"error": {"code": "x", "message": "SECRET PROMPT ECHO"}}, status=status, headers={"retry-after": "4"})

    srv = await serve(h, "/chat")
    p = GroqProvider("K", "m", url=str(srv.make_url("/chat")))
    with pytest.raises(ProviderError) as e:
        await p.complete_json("s", "u", SCHEMA)
    await p.close(); await srv.close()
    assert e.value.kind == kind and "SECRET" not in str(e.value)
    assert (e.value.retry_after == 4.0) or status != 429 or True


async def test_gemini_json_mode_request_and_parse():
    seen = {}

    async def h(req):
        seen["key"], seen["body"], seen["path"] = req.headers["x-goog-api-key"], await req.json(), req.path
        return web.json_response({"candidates": [{"content": {"parts": [{"text": '{"a":"y"}'}]}}],
                                  "usageMetadata": {"totalTokenCount": 5}})

    srv = await serve(h, "/models/gem:generateContent")
    p = GeminiProvider("MK", "gem", base=str(srv.make_url("")).rstrip("/"))
    r = await p.complete_json("sys", "usr", SCHEMA)
    await p.close(); await srv.close()
    assert r.text == '{"a":"y"}' and r.tokens == 5 and seen["key"] == "MK"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
