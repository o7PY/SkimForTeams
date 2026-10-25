"""P3-DIFF-014: NUL / lone surrogates must never reach Postgres (CharacterNotInRepertoireError stalled run-all)."""
import json

import pytest
from ai_fakes import extraction, item
from ai_harness import CONV, Harness
from test_ai_documents import doc_row, make_docx, make_pdf, make_ctx, setup

from skim.ai import documents as docs
from skim.jobs.ai_documents import ai_documents
from skim.storage.ai_memory import CharacterNotInRepertoireError
from skim.storage.cleanpool import CleanPool
from skim.textclean import clean_arg, clean_json, clean_text


def test_clean_text_and_json():
    assert clean_text("Qu\x00iz \ud83d 1 \U0001f600 café\n\t") == "Quiz  1 \U0001f600 café\n\t"
    assert clean_json({"a\x00": ["x\x00", {"b": "y\ud800"}, 3, None, True]}) == {"a": ["x", {"b": "y"}, 3, None, True]}
    assert clean_arg(None) is None and clean_arg(5) == 5 and clean_arg(b"\x00") == b"\x00" and clean_arg("a\x00") == "a"


class FakePool:
    def __init__(self):
        self.calls = []

    async def execute(self, q, *a):
        self.calls.append(("execute", a))

    async def fetch(self, q, *a):
        self.calls.append(("fetch", a))
        return []

    async def fetchval(self, q, *a):
        self.calls.append(("fetchval", a))

    async def fetchrow(self, q, *a):
        self.calls.append(("fetchrow", a))

    async def executemany(self, q, rows):
        self.calls.append(("executemany", tuple(rows)))

    def acquire(self):
        pool = self

        class Ctx:
            async def __aenter__(self_):
                return pool

            async def __aexit__(self_, *e):
                return False
        return Ctx()


async def test_clean_pool_cleans_every_parameter_on_pool_and_acquired_connections():
    fp = FakePool()
    pool = CleanPool(fp)
    await pool.execute("q", "a\x00", {"k": "v\x00"}, 5)
    await pool.fetch("q", ["x\x00"])
    await pool.fetchval("q", "y\ud800")
    await pool.fetchrow("q", "z\x00")
    async with pool.acquire() as conn:
        await conn.execute("q", "c\x00")
        await conn.executemany("q", [("d\x00", 1)])
    flat = json.dumps([c[1] for c in fp.calls], default=str)
    assert "\\u0000" not in flat and "\\ud800" not in flat
    assert fp.calls[0][1] == ("a", {"k": "v"}, 5) and fp.calls[-1][1] == (("d", 1),)


def test_pdf_text_with_nul_is_cleaned_at_extraction():
    pdf = make_pdf(["Quiz\x00 1 on 14 November"])
    out = docs.extract("a.pdf", pdf, 10)
    assert "\x00" not in out.pages[0]
    assert "\x00" not in docs.extract("a.pdf", make_pdf(["ab"]), 10).pages[0]


def test_memory_store_rejects_nul_like_postgres_so_these_tests_mean_something():
    import asyncio
    from skim.storage.memory import MemoryStorage
    with pytest.raises(CharacterNotInRepertoireError):
        asyncio.run(MemoryStorage().save_document_text(1, "c", "ok", None, 1, 1, [{"chunk_index": 0, "text": "a\x00"}]))


async def test_nul_in_message_text_and_in_llm_output_never_reaches_the_prompt_or_the_store():
    evil_title = "Quiz\x00 1"
    h = await Harness({}).setup()
    reply = extraction("event", [item("create", "quiz", evil_title, {"kind": "absolute", "date": "2026-10-23", "text": "23\x00 Oct"},
                                      venue="LHC\x00 101", details="bring\x00 pen")])
    h.provider.script["Quiz 1 on 23 Oct"] = reply
    # the message text contains NUL and a lone surrogate; the script key is the CLEANED text the model must see
    await h.post("2026-10-20 11:00", "Quiz\x00 1 on 23 Oct\ud83d".replace("Quiz\x00 1", "Quiz 1"), name="Prof\x00. Menon")
    h.store.messages[(CONV, "m1")]["text"] = "Quiz\x00 1 on 23 Oct\ud83d"
    outs = await h.run()
    assert outs and outs[0][1].status == "ok"
    assert "\x00" not in h.provider.calls[0]["user"]
    (e,) = await h.events()
    assert (e["title"], e["venue"], e["details"]) == ("Quiz 1", "LHC 101", "bring pen")
    (a,) = await h.store.list_message_analyses(CONV, "m1")
    assert "\\u0000" not in json.dumps(a["output"])


async def test_nul_in_pdf_text_is_stored_cleaned_and_the_document_event_is_created():
    store = await setup(None)
    await store.upsert_document(doc_row("Syllabus.pdf"))
    ctx, prov = make_ctx(store, {"Quiz 1": extraction("event", [item("create", "quiz", "Quiz 1", when={
        "kind": "absolute", "date": "2026-11-14", "time": "10:00", "text": "14 Nov"})])})

    async def fetch(d):
        return make_pdf(["Quiz\x00 1 on 14 November 2026 at 10:00"])

    assert await ai_documents(ctx, None, fetch=fetch) == "ok"
    d = await store.get_document("d", "Syllabus.pdf")
    (chunk,) = await store.list_doc_chunks(d["id"])
    assert "\x00" not in chunk["text"] and "Quiz" in chunk["text"] and len(store.events) == 1
