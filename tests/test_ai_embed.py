"""T10: embeddings job, redaction before embedding, stale/deleted/other-team filtering, /debug/search, failure handling."""
import hashlib
import math
import re

from ai_fakes import ProviderErrorLike, extraction
from ai_harness import ist
from test_ai_jobs import AUTH, DEBUG, make
from test_jobs import CHAN, TEAM

from skim.ai.config import AIConfig
from skim.ai.embeddings import Embedder
from skim.ai.providers.base import ProviderError

OPEN = {"": extraction("chitchat")}


class FakeEmbedProvider:
    """Deterministic bag-of-words embeddings (768-d): shared words => similar vectors. Records every text it was sent."""
    name, model = "gemini", "fake-embed"

    def __init__(self):
        self.sent, self.tasks, self.fail = [], [], None

    async def complete_json(self, *a, **k):
        raise AssertionError("not used")

    async def close(self):
        pass

    async def embed(self, model, texts, dims, task):
        if self.fail:
            raise self.fail
        self.sent += texts
        self.tasks.append(task)
        out = []
        for t in texts:
            v = [0.0] * dims
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
            out.append(v)
        return out, None


async def post(store, mid, text, when="2026-10-21 10:00", name="Prof", subject=None, conv=CHAN):
    await store.backfill_message({"conversation_id": conv, "message_id": mid, "reply_to_id": None, "from_aad_id": "prof-aad",
                                  "from_name": name, "text": text, "html": None, "importance": None, "mentions": None,
                                  "subject": subject, "created_at": ist(when), "last_edited_at": None, "deleted_at": None}, [], [])


async def test_embed_job_search_redaction_and_idempotence():
    fe = FakeEmbedProvider()
    client, store, provider, close = await make(OPEN, extra_providers=[fe])
    try:
        await post(store, "m2", "The midsem exam is on 2 November, mail Asha Rao at asha@uni.edu or 9876543210", name="Asha Rao")
        await post(store, "m3", "Lecture slides for week five uploaded")
        body = await (await client.post("/jobs/ai-embed", headers=AUTH)).json()
        assert body["status"] == "ok" and body["stats"]["ai_embedded"] == 3
        sent = " ".join(fe.sent)
        assert "asha@uni.edu" not in sent and "9876543210" not in sent and "Asha" not in sent and "[EMAIL_1]" in sent
        assert set(fe.tasks) == {"RETRIEVAL_DOCUMENT"}
        again = await (await client.post("/jobs/ai-embed", headers=AUTH)).json()
        assert not again["stats"].get("ai_embedded") and len(fe.sent) == 3  # nothing re-embedded
        r = await client.get("/debug/search?q=when is the midsem exam&k=2", headers=DEBUG)
        hits = (await r.json())["hits"]
        assert r.status == 200 and hits[0]["ref"]["message_id"] == "m2" and hits[0]["score"] > hits[1]["score"]
        assert "RETRIEVAL_QUERY" in fe.tasks
        st = await (await client.get("/debug/ai-status", headers=DEBUG)).json()
        assert st["embeddings"]["stored"] == 3 and st["embeddings"]["pending"] == 0
    finally:
        await close()


async def test_search_query_is_redacted_and_endpoint_validates_and_needs_the_token():
    fe = FakeEmbedProvider()
    client, store, provider, close = await make(OPEN, extra_providers=[fe])
    try:
        assert (await client.get("/debug/search?q=x")).status == 403
        assert (await client.get("/debug/search", headers=DEBUG)).status == 400
        assert (await client.get("/debug/search?q=" + "a" * 501, headers=DEBUG)).status == 400
        await client.get("/debug/search?q=ask asha@uni.edu about the quiz", headers=DEBUG)
        assert "asha@uni.edu" not in " ".join(fe.sent) and "[EMAIL_1]" in " ".join(fe.sent)
        fe.fail = ProviderError("rate_limit", 429)
        assert (await client.get("/debug/search?q=quiz", headers=DEBUG)).status == 502
    finally:
        await close()


async def test_edited_deleted_and_other_team_messages_never_appear_in_search():
    fe = FakeEmbedProvider()
    client, store, provider, close = await make(OPEN, extra_providers=[fe])
    try:
        await post(store, "gone", "secret quiz moved to friday")
        await post(store, "old", "quiz is on monday")
        await client.post("/jobs/ai-embed", headers=AUTH)
        store.messages[(CHAN, "gone")]["deleted_at"] = ist("2026-10-22 10:00")
        store.messages[(CHAN, "old")]["current_version"] = 2  # edited: the stored vector is for version 1
        store.messages[(CHAN, "old")]["text"] = "quiz is on tuesday"
        hits = (await (await client.get("/debug/search?q=quiz", headers=DEBUG)).json())["hits"]
        assert all(h["ref"]["message_id"] not in ("gone", "old") for h in hits)  # deleted + stale-version vectors are invisible
        # the edit is embedded as its own vector on the next run and then found
        await client.post("/jobs/ai-embed", headers=AUTH)
        hits = (await (await client.get("/debug/search?q=quiz tuesday", headers=DEBUG)).json())["hits"]
        assert hits[0]["ref"]["message_id"] == "old" and hits[0]["ref"]["version"] == 2
        assert (await (await client.get("/debug/search?q=quiz&team_id=OTHER", headers=DEBUG)).json())["hits"] == []
    finally:
        await close()


async def test_provider_failure_leaves_items_pending_and_the_run_partial():
    fe = FakeEmbedProvider()
    fe.fail = ProviderError("rate_limit", 429)
    client, store, provider, close = await make(OPEN, extra_providers=[fe])
    try:
        body = await (await client.post("/jobs/ai-embed", headers=AUTH)).json()
        assert body["status"] == "partial" and body["stats"]["ai_embed_failed"] == 1 and body["stats"]["ai_embed_error_rate_limit"] == 1
        fe.fail = None
        body = await (await client.post("/jobs/ai-embed", headers=AUTH)).json()
        assert body["status"] == "ok" and body["stats"]["ai_embedded"] == 1
    finally:
        await close()


async def test_document_chunks_are_embedded_found_and_replaced_when_the_file_changes():
    from datetime import datetime, timezone
    from test_ai_documents import doc_row, make_docx
    from skim.jobs.ai_documents import ai_documents
    from skim.jobs.common import Budget, JobContext
    fe = FakeEmbedProvider()
    client, store, provider, close = await make(OPEN, extra_providers=[fe])
    try:
        await store.upsert_document(doc_row("Syllabus.docx", ctag="c1", team_id=TEAM, channel_id=CHAN))
        from skim.ai.services import AIServices
        from ai_harness import CFG
        svc = AIServices.build(CFG, store, providers=[provider, fe])
        ctx = JobContext(store, None, Budget(60), ai=svc)

        async def fetch(d):
            return make_docx(["Project proposal due on 12 December"])

        await ai_documents(ctx, None, fetch=fetch)
        await client.post("/jobs/ai-embed", headers=AUTH)
        hits = (await (await client.get("/debug/search?q=project proposal due", headers=DEBUG)).json())["hits"]
        assert hits[0]["kind"] == "doc_chunk" and "proposal" in hits[0]["snippet"]
        await store.upsert_document(doc_row("Syllabus.docx", ctag="c2", team_id=TEAM, channel_id=CHAN))
        await ai_documents(ctx, None, fetch=fetch)
        assert not [e for e in store.embeddings.values() if e["kind"] == "doc_chunk"]  # old vectors dropped with the old chunks
        await client.post("/jobs/ai-embed", headers=AUTH)
        assert len([e for e in store.embeddings.values() if e["kind"] == "doc_chunk"]) == 1
    finally:
        await close()


async def test_embeddings_off_without_768_dims_or_ai():
    import dataclasses
    from ai_harness import CFG
    from skim.ai.services import AIServices
    bad = AIServices.build(dataclasses.replace(CFG, embed_dims=1024), object(), providers=[FakeEmbedProvider()])
    assert bad.embedder is not None and not bad.embedder.usable
    client, store, provider, close = await make(OPEN)  # no embedding-capable provider at all
    try:
        assert (await client.get("/debug/search?q=x", headers=DEBUG)).status == 503
        body = await (await client.post("/jobs/ai-embed", headers=AUTH)).json()
        assert body["status"] == "ok" and body["stats"]["ai_embed_disabled"] == 1 and not body["stats"].get("ai_embedded")
    finally:
        await close()
