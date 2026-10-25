import json

import pytest
from ai_fakes import FakeProvider, extraction

from skim.ai.providers.base import ProviderError
from skim.ai.providers.router import AllProvidersFailed, Router
from skim.ai.schemas import MessageExtraction

GOOD = extraction("chitchat")


async def nosleep(_):
    pass


def router(*providers):
    return Router(list(providers), sleep=nosleep)


async def test_primary_success_does_not_touch_the_fallback():
    a, b = FakeProvider("groq", [GOOD]), FakeProvider("gemini", [GOOD])
    c = await router(a, b).complete(MessageExtraction, "sys", "user")
    assert c.provider == "groq" and c.value.category == "chitchat" and not b.calls


async def test_repair_retry_gets_the_bad_output_and_the_error():
    a = FakeProvider("groq", ["not json", GOOD])
    r = router(a)
    c = await r.complete(MessageExtraction, "s", "u")
    assert c.provider == "groq" and r.stats["repairs"] == 1 and len(a.calls) == 2
    assert a.calls[1]["repair"][0] == "not json" and a.calls[1]["repair"][1]


async def test_schema_violation_is_repaired_then_falls_back():
    bad = {"category": "nonsense", "confidence": "high", "items": []}
    a, b = FakeProvider("groq", [bad, bad]), FakeProvider("gemini", [GOOD])
    r = router(a, b)
    c = await r.complete(MessageExtraction, "s", "u")
    assert c.provider == "gemini" and len(a.calls) == 2 and r.stats["fallbacks"] == 1


@pytest.mark.parametrize("kind", ["server", "timeout", "network", "auth", "bad_request"])
async def test_provider_errors_fall_back(kind):
    a, b = FakeProvider("groq", [ProviderError(kind, 500)]), FakeProvider("gemini", [GOOD])
    c = await router(a, b).complete(MessageExtraction, "s", "u")
    assert c.provider == "gemini" and len(a.calls) == 1


async def test_rate_limit_waits_retry_after_then_retries_once():
    slept = []

    async def sleep(s):
        slept.append(s)

    a = FakeProvider("groq", [ProviderError("rate_limit", 429, retry_after=3.0), GOOD])
    r = Router([a], sleep=sleep)
    c = await r.complete(MessageExtraction, "s", "u")
    assert c.provider == "groq" and slept == [3.0] and r.stats["rate_limited"] == 1


async def test_long_retry_after_goes_straight_to_the_fallback():
    a = FakeProvider("groq", [ProviderError("rate_limit", 429, retry_after=600.0)])
    b = FakeProvider("gemini", [GOOD])
    c = await router(a, b).complete(MessageExtraction, "s", "u")
    assert c.provider == "gemini" and len(a.calls) == 1


async def test_all_failed_reports_an_error_class():
    a, b = FakeProvider("groq", [ProviderError("server", 503)]), FakeProvider("gemini", ["garbage"])
    r = router(a, b)
    with pytest.raises(AllProvidersFailed) as e:
        await r.complete(MessageExtraction, "s", "u")
    assert e.value.error_class == "invalid_output" and r.stats["all_failed"] == 1


async def test_stats_are_numbers_only():
    r = router(FakeProvider("groq", [GOOD]))
    await r.complete(MessageExtraction, "secret prompt text", "secret user text")
    assert all(isinstance(v, int) for v in r.stats.values())
    assert "secret" not in json.dumps(dict(r.stats))
