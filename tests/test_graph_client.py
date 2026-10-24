import json
import logging

import pytest

from fake_graph import FakeGraph
from skim.graph.auth import GraphAuthError, StaticTokenProvider
from skim.graph.client import GraphClient, GraphError, GraphGone, GraphNotFound, GraphServerError, GraphThrottled


def make(base, **kw):
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    c = GraphClient(StaticTokenProvider("SECRET-TOKEN"), base_url=base, sleep=fake_sleep, jitter=lambda: 1.0, **kw)
    return c, sleeps


async def test_get_sends_bearer_and_returns_json():
    fake = FakeGraph().add("/teams/t/channels", {"value": [{"id": "c1"}]})
    async with fake.serve() as base:
        c, _ = make(base)
        assert (await c.get("/teams/t/channels"))["value"][0]["id"] == "c1"
        await c.close()
    assert fake.requests[0][2] == "Bearer SECRET-TOKEN"


async def test_429_honours_retry_after_then_succeeds():
    fake = FakeGraph().add("/x", [(429, {"error": {"code": "TooManyRequests"}}, {"Retry-After": "3"}), (200, {"ok": 1})])
    async with fake.serve() as base:
        c, sleeps = make(base)
        assert await c.get("/x") == {"ok": 1}
    assert sleeps == [3.0] and c.stats["throttled"] == 1 and c.stats["retries"] == 1


async def test_retry_after_longer_than_limit_raises_throttled_without_sleeping():
    fake = FakeGraph().add("/x", {"error": {"code": "TooManyRequests"}}, status=429, headers={"Retry-After": "900"})
    async with fake.serve() as base:
        c, sleeps = make(base)
        with pytest.raises(GraphThrottled) as e:
            await c.get("/x")
    assert e.value.retry_after == 900 and sleeps == []


async def test_5xx_backs_off_exponentially_then_succeeds():
    fake = FakeGraph().add("/x", [(502, {}), (503, {}), (200, {"ok": 1})])
    async with fake.serve() as base:
        c, sleeps = make(base)
        assert await c.get("/x") == {"ok": 1}
    assert sleeps == [0.5, 1.0]  # jitter fixed at 1.0 -> factor 1


async def test_5xx_gives_up_after_max_retries():
    fake = FakeGraph().add("/x", {"error": {"code": "serviceNotAvailable"}}, status=500)
    async with fake.serve() as base:
        c, sleeps = make(base, max_retries=2)
        with pytest.raises(GraphServerError):
            await c.get("/x")
    assert fake.count("/x") == 3 and len(sleeps) == 2


async def test_429_exhausted_raises_throttled():
    fake = FakeGraph().add("/x", {}, status=429)
    async with fake.serve() as base:
        c, _ = make(base, max_retries=1)
        with pytest.raises(GraphThrottled):
            await c.get("/x")


@pytest.mark.parametrize("status,exc,code", [(401, GraphAuthError, "InvalidAuthenticationToken"),
                                              (403, GraphAuthError, "accessDenied"),
                                              (404, GraphNotFound, "itemNotFound"),
                                              (410, GraphGone, "resyncRequired"),
                                              (400, GraphError, "badRequest")])
async def test_error_mapping_carries_status_and_graph_code_and_does_not_retry(status, exc, code):
    fake = FakeGraph().add("/x", {"error": {"code": code, "message": "m"}}, status=status)
    async with fake.serve() as base:
        c, sleeps = make(base)
        with pytest.raises(exc) as e:
            await c.get("/x")
    assert e.value.status == status and e.value.code == code and e.value.endpoint == "/x"
    assert sleeps == [] and fake.count("/x") == 1


async def test_pages_follow_next_link_and_expose_delta_link():
    fake = FakeGraph()
    fake.add("/d/delta", {"value": [{"id": 1}], "@odata.nextLink": "{base}/d/delta?token=P2"})
    fake.add("/d/delta?token=P2", {"value": [{"id": 2}, {"id": 3}], "@odata.deltaLink": "{base}/d/delta?token=DONE"})
    async with fake.serve() as base:
        c, _ = make(base)
        pages = [p async for p in c.pages("/d/delta")]
        ids = [i["id"] async for i in c.items("/d/delta")]
    assert [len(p["value"]) for p in pages] == [1, 2] and ids == [1, 2, 3]
    assert pages[-1]["@odata.deltaLink"].endswith("token=DONE")


async def test_next_link_to_another_host_is_refused_and_token_not_sent():
    fake = FakeGraph().add("/x", {"value": [], "@odata.nextLink": "https://evil.example/steal"})
    async with fake.serve() as base:
        c, _ = make(base)
        with pytest.raises(GraphError):
            [p async for p in c.pages("/x")]
    assert len(fake.requests) == 1


@pytest.mark.parametrize("path", ["/drives/d/items/i/content", "/drives/d/items/i/content/",
                                  "/x?foo=1&downloadUrl=1"])
async def test_file_content_is_never_requested(path):
    c, _ = make("http://127.0.0.1:1")
    with pytest.raises(ValueError):
        await c.get(path)


async def test_concurrency_is_limited():
    import asyncio
    state = {"now": 0, "peak": 0}
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    async def h(_):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.05)
        state["now"] -= 1
        return web.json_response({})

    app = web.Application()
    app.router.add_get("/x", h)
    srv = TestServer(app)
    await srv.start_server()
    try:
        c, _ = make(f"http://{srv.host}:{srv.port}", max_concurrency=2)
        await asyncio.gather(*(c.get("/x") for _ in range(6)))
        await c.close()
    finally:
        await srv.close()
    assert state["peak"] == 2


async def test_logs_never_contain_token_or_query(caplog):
    fake = FakeGraph().add("/x", {"error": {"code": "accessDenied", "message": "nope"}}, status=403)
    caplog.set_level(logging.DEBUG)
    async with fake.serve() as base:
        c, _ = make(base)
        with pytest.raises(GraphAuthError):
            await c.get("/x", params={"$select": "secretfield"})
    ours = [r.getMessage() for r in caplog.records if r.name == "skim.graph"]  # (the fake server logs its own access line)
    assert ours and all("SECRET-TOKEN" not in m and "secretfield" not in m for m in ours)
    rec = json.loads(ours[0])
    assert rec == {"event": "graph_error", "endpoint": "/x", "status": 403, "code": "accessDenied"}


async def test_msal_provider_failure_is_graph_auth_error(monkeypatch):
    from skim.graph.auth import MsalTokenProvider
    p = MsalTokenProvider.__new__(MsalTokenProvider)

    class App:
        def acquire_token_for_client(self, scopes):
            return {"error": "invalid_client", "error_description": "AADSTS7000215: bad secret"}

    p._app = App()
    with pytest.raises(GraphAuthError) as e:
        await p.get_token()
    assert e.value.code == "invalid_client"
