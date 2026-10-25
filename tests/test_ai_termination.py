"""P3-DIFF-015: run-all must finish. A failing item counts an attempt and stops after AI_MAX_ATTEMPTS; one bad item never
blocks the others; `next` is true only when work we have not tried yet remains; /debug/ai-status says what is failing."""
from dataclasses import replace
from datetime import datetime, timezone

from ai_fakes import ProviderErrorLike, extraction, item
from ai_harness import CFG
from test_ai_documents import doc_row, make_docx, setup as doc_setup
from test_ai_embed import FakeEmbedProvider, OPEN, post
from test_ai_jobs import AUTH, DEBUG, QUIZ, make
from test_jobs import CHAN, TEAM

from skim.ai.providers.base import ProviderError
from skim.ai.services import AIServices
from skim.jobs.ai_documents import ai_documents
from skim.jobs.common import Budget, JobContext
from skim.jobs.runner import step_ai_all


async def run_until_done(client, limit=10):
    """Call run-all like a client following `next`; returns every response body."""
    out = []
    for _ in range(limit):
        body = await (await client.post("/jobs/run-all", headers=AUTH)).json()
        out.append(body)
        if not body["next"]:
            return out
    raise AssertionError(f"run-all still says next=true after {limit} calls: {out[-1]['stats']}")


async def test_a_poisoned_document_cannot_keep_run_all_partial_and_other_documents_still_finish():
    store = await doc_setup(None)
    for n in ("bad.docx", "good.docx"):
        await store.upsert_document(doc_row(n))
    prov_reply = {"": extraction("announcement")}
    from test_ai_documents import make_ctx
    ctx, _ = make_ctx(store, prov_reply)
    calls = {"bad": 0}

    async def fetch(d):
        if d["name"] == "bad.docx":
            calls["bad"] += 1
            raise RuntimeError("boom")  # an unexpected bug while processing this one file
        return make_docx(["hello"])

    statuses = []
    for _ in range(5):
        statuses.append(await ai_documents(ctx, None, fetch=fetch))
    assert statuses == ["ok"] * 5  # never "partial"
    assert calls["bad"] == 3  # AI_MAX_ATTEMPTS, then given up
    t = {(await store.get_document("d", n))["id"]: n for n in ("bad.docx", "good.docx")}
    got = {t[i]: (x["status"], x["reason"], x["attempts"]) for i, x in store.document_texts.items()}
    assert got == {"bad.docx": ("error", "RuntimeError", 3), "good.docx": ("ok", None, 0)}
    assert await store.list_pending_documents(10, 3) == []
    # a changed file starts again
    await store.upsert_document(doc_row("bad.docx", ctag="c2"))
    assert [d["name"] for d in await store.list_pending_documents(10, 3)] == ["bad.docx"]


async def test_a_crashing_step_does_not_make_run_all_partial_and_later_steps_still_run():
    store = await doc_setup(None)
    from test_ai_documents import make_ctx
    ctx, _ = make_ctx(store, {"": extraction("announcement")})
    ran = []

    async def boom(c, t):
        ran.append("boom")
        raise RuntimeError("x")

    async def fine(c, t):
        ran.append("fine")
        return "ok"

    import skim.jobs.runner as r
    old = r.AI_STEPS
    r.AI_STEPS = [boom, fine]
    try:
        assert await step_ai_all(ctx, None) == "ok"
    finally:
        r.AI_STEPS = old
    assert ran == ["boom", "fine"] and ctx.stats["ai_step_errors"] == 1 and ctx.stats["ai_step_error_RuntimeError"] == 1


async def test_next_is_true_only_while_untried_work_remains():
    cfg = replace(CFG, max_messages_per_run=1)
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ, "": extraction("chitchat")}, cfg=cfg)
    try:
        await post(store, "m2", "thanks sir", when="2026-10-21 10:00")
        # run-all also back-fills the fake tenant's own messages; count everything the AI will have to look at
        await client.post("/jobs/backfill", headers=AUTH)
        total = (await store.ai_status(cfg.prompt_version, 3))["messages_pending"]
        assert total >= 2
        bodies = await run_until_done(client, limit=total + 3)
        flags = [b["next"] for b in bodies]
        assert flags == [True] * (len(flags) - 1) + [False] and len(flags) <= total + 1  # 1 message per run, then done
        assert (await store.ai_status(cfg.prompt_version, 3))["messages_pending"] == 0
        assert len(await store.list_events(team_id=TEAM)) == 1
        assert (await (await client.post("/jobs/run-all", headers=AUTH)).json())["next"] is False  # nothing left
    finally:
        await close()


async def test_a_message_that_always_fails_stops_after_max_attempts_and_the_others_complete():
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ, "poison": ProviderErrorLike(ProviderError("server", 503)),
                                                  "": extraction("chitchat")})
    try:
        await post(store, "bad", "poison", when="2026-10-21 10:00")
        bodies = await run_until_done(client)  # failures alone never keep next=true
        assert not any(b["next"] for b in bodies)
        for _ in range(4):
            await client.post("/jobs/run-all", headers=AUTH)
        (a,) = await store.list_message_analyses(CHAN, "bad")
        assert a["status"] == "failed" and a["attempts"] == 3 and a["error_class"] == "server"
        assert len(await store.list_events(team_id=TEAM)) == 1  # the good message was never blocked
    finally:
        await close()


async def test_an_embedding_the_provider_rejects_is_isolated_counted_and_given_up_on():
    class Picky(FakeEmbedProvider):
        async def embed(self, model, texts, dims, task):
            if any("POISON" in t for t in texts):
                raise ProviderError("bad_request", 400)
            return await super().embed(model, texts, dims, task)

    fe = Picky()
    client, store, provider, close = await make(OPEN, extra_providers=[fe])
    try:
        await post(store, "p", "POISON text", when="2026-10-21 10:00")
        await post(store, "g", "good text", when="2026-10-21 11:00")
        for _ in range(5):
            body = await (await client.post("/jobs/ai-embed", headers=AUTH)).json()
            assert body["next"] is False
        assert [e["ref"]["message_id"] for e in store.embeddings.values() if e["ref"]["message_id"] in ("p", "g")] == ["g"]
        (f,) = store.embed_failures.values()
        assert f["attempts"] == 3 and f["error_class"] == "bad_request"
        st = await (await client.get("/debug/ai-status", headers=DEBUG)).json()
        assert st["pending"]["embeddings"] == 0 and st["gave_up"]["embeddings"] == 1
        assert st["failed_by_error_class"]["embeddings"] == {"bad_request": 1}
    finally:
        await close()


async def test_ai_status_reports_pending_per_step_and_failures_by_error_class():
    client, store, provider, close = await make({"Quiz 1 on 23 Oct": QUIZ, "poison": ProviderErrorLike(ProviderError("timeout")),
                                                  "": extraction("chitchat")})
    try:
        await post(store, "bad", "poison", when="2026-10-21 10:00")
        await store.upsert_document(doc_row("x.docx", team_id=TEAM, channel_id=CHAN))
        st0 = await (await client.get("/debug/ai-status", headers=DEBUG)).json()
        assert st0["pending"]["messages"] == 2 and st0["pending"]["documents"] == 1
        await client.post("/jobs/ai-process", headers=AUTH)
        st = await (await client.get("/debug/ai-status", headers=DEBUG)).json()
        assert st["pending"]["messages"] == 1  # the failing one still has attempts left
        assert st["failed_by_error_class"]["messages"] == {"timeout": 1} and st["gave_up"]["messages"] == 0
        for _ in range(3):
            await client.post("/jobs/ai-process", headers=AUTH)
        st = await (await client.get("/debug/ai-status", headers=DEBUG)).json()
        assert st["pending"]["messages"] == 0 and st["gave_up"]["messages"] == 1
    finally:
        await close()
