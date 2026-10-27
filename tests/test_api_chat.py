"""PHASE6A: POST /api/v1/chat on both storages. The model and the embedder are fakes; everything else is the real code."""
import asyncio
import json
import logging
from datetime import date, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ai_fakes import FakeProvider
from skim.ai.config import AIConfig
from skim.ai.embeddings import Embedder, message_text, names_of
from skim.ai.providers.base import ProviderError
from skim.ai.providers.router import Router
from skim.api import chat as chat_mod
from skim.api import routes as api_routes
from skim.api import routes_chat
from skim.api.chat import ChatService, _timing, check_citations, ensure_courses, event_line, pick_events, today_line, trim_words
from skim.jobs.ai_embed import _row, _text
from test_ai_embed import FakeEmbedProvider
from test_api_deck import A, B, Api, Clock, FakeVerifier, card_for, make_event
from test_feed_build import CONV, NOW, PROF, STAFF, T0, msg, team
from test_reminders_engine import SECRET

CONV2 = "19:other@thread.tacv2"
MODEL = "fake-embed"


class Chat:
    """The test app with a scripted model. `say` is what the model answers; `prompts` are what it was sent."""

    def __init__(self, api, provider, embedder_provider):
        self.api, self.provider, self.embedder_provider = api, provider, embedder_provider

    @property
    def prompts(self):
        return [c["user"] for c in self.provider.calls]

    async def ask(self, question="when is the quiz", user="a", **extra):
        return await self.api.call("POST", "/chat", user, json={"question": question, **extra})


def answer(text="The quiz is on Mon 2 Nov at 10:00 [1].", cited=(1,), refused=False):
    return json.dumps({"answer": text, "cited": list(cited), "refused": refused})


async def build_chat(store, replies, enabled=True):
    clock = Clock()
    provider = FakeProvider("gemini", replies)
    fe = FakeEmbedProvider()
    emb = Embedder(fe, MODEL)
    svc = ChatService(store, Router([provider]), emb, AIConfig(embed_model=MODEL), staff_ids=STAFF, id_secret=SECRET) if enabled else None
    app = web.Application()
    api_routes.register(app, store, FakeVerifier(), now=clock, staff_aad_ids=STAFF, id_secret=SECRET, chat=svc)
    client = TestClient(TestServer(app))
    await client.start_server()
    await team(store)
    return Chat(Api(store, client, clock), provider, fe), client, emb


@pytest.fixture
async def chat(store):
    c, client, emb = await build_chat(store, [lambda s, u: answer()])
    c.emb = emb
    yield c
    await client.close()


async def second_team(s):
    await s.upsert_team({"id": "T2", "aad_group_id": "g2", "name": "PH 105", "tenant_id": "x"})
    await s.mark_team_installed("T2", "ZZZ999", PROF, T0)
    await s.upsert_conversation({"id": CONV2, "conversation_type": "channel", "team_id": "T2", "channel_id": CONV2, "name": "General"})
    await s.upsert_channel({"id": CONV2, "team_id": "T2", "display_name": "General", "membership_type": "standard"})


async def embed_all(chat):
    rows = await chat.api.store.list_unembedded(MODEL, 1000)
    vecs = await chat.emb.embed_documents([_text(r) for r in rows], [names_of(r) for r in rows])
    await chat.api.store.save_embeddings([_row(r, v, MODEL) for r, v in zip(rows, vecs)])


async def corpus(chat, *posts, conv=CONV):
    for mid, text in posts:
        await msg(chat.api.store, mid, text=text, at=NOW - timedelta(hours=2), conv=conv)
    await chat.api.build()
    await embed_all(chat)


# ---------------------------------------------------------------- pure helpers
def test_trim_words_keeps_short_answers_and_cuts_long_ones_at_a_sentence():
    assert trim_words("Short answer.") == "Short answer."
    long = ("First sentence here. " * 30).strip()
    out = trim_words(long, 20)
    assert len(out.split()) <= 20 and out.endswith(".")
    assert trim_words("word " * 200, 10).endswith("…")


def test_check_citations_drops_numbers_that_are_not_in_the_context_and_keeps_ones_the_text_uses():
    text, nums = check_citations("Quiz is Monday [1][7]. Venue LHC 104 [2].", [1, 9], {1, 2, 3})
    assert nums == [1, 2] and "[7]" not in text and "[1]" in text and "[2]" in text
    assert check_citations("No sources.", [5], {1}) == ("No sources.", [])
    assert check_citations("Placeholder [PERSON_1] stays [1].", [], {1})[0] == "Placeholder [PERSON_1] stays [1]."


def test_event_line_says_when_where_status_and_unconfirmed():
    chip = {"title": "Quiz 1", "type": "quiz", "start": "2026-11-02T10:00:00+05:30", "all_day": False, "venue": "LHC 104",
            "status": "scheduled", "unconfirmed": True, "details": None}
    line = event_line(chip)
    assert "Mon 2 Nov 2026, 10:00" in line and "venue LHC 104" in line and "UNCONFIRMED" in line and "status scheduled" in line
    assert "time not announced" in event_line({**chip, "start": "2026-11-02", "all_day": True, "unconfirmed": False})
    assert "no date announced" in event_line({**chip, "start": None})


def test_timing_says_in_plain_words_how_far_an_event_is_from_today_in_india_time():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 26, 20, 0, tzinfo=timezone.utc)                     # 01:30 on Tue 27 Oct in India
    assert _timing("2026-10-25", now) == "ALREADY PASSED"
    assert _timing("2026-10-27", now) == "today"                                 # today in India, though still Mon 26 in UTC
    assert _timing("2026-10-28", now) == "tomorrow"
    assert _timing("2026-11-02", now) == "in 6 days"
    assert _timing("2026-10-26T15:00:00+05:30", now) == "ALREADY PASSED"          # yesterday afternoon
    assert _timing("2026-10-27T10:00:00+05:30", now) == "today"
    assert _timing("2026-10-27T01:00:00+05:30", now) == "ALREADY PASSED"          # earlier today than now (01:30)
    assert _timing("2026-12-20T10:00:00+05:30", now) == "in 54 days"
    assert _timing(None, now) is None and _timing("2026-10-28", None) is None and _timing("garbage", now) is None
    assert today_line(now) == "Tuesday 27 October 2026, 01:30 (India time)"
    chip = {"title": "Quiz 2", "type": "quiz", "start": "2026-10-26T15:00:00+05:30", "all_day": False, "venue": None, "status": "rescheduled", "unconfirmed": False}
    assert "ALREADY PASSED" in event_line(chip, now) and "ALREADY PASSED" not in event_line(chip)   # without a clock: as before


def test_pick_events_takes_upcoming_and_matching_ones_and_skips_past_non_matches():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    ev = lambda i, title, type_, day, status="scheduled": {  # noqa: E731
        "id": i, "title": title, "type": type_, "event_date": day, "starts_at": None, "ends_at": None, "status": status,
        "time_known": False, "time_defaulted": False}
    events = {1: ev(1, "Quiz 1", "quiz", date(2026, 11, 2)), 2: ev(2, "Old quiz", "quiz", date(2026, 9, 1)),
              3: ev(3, "Lab report", "lab", date(2026, 9, 2)), 4: ev(4, "Midsem", "exam", date(2026, 11, 20))}
    ids = [e["id"] for e in pick_events(events, "when was the old quiz", now)]
    assert ids[0] == 2 and set(ids) == {1, 2, 4} and 3 not in ids                 # matches first, upcoming too, other past ones not
    assert [e["id"] for e in pick_events(events, "hello", now)] == [1, 4]          # no match: just what is coming, soonest first
    assert [e["id"] for e in pick_events(events, "quiz", now, exclude={1})] == [2, 4]
    more = {**events, 5: ev(5, "Quiz 2", "quiz", date(2026, 11, 4)), 6: ev(6, "Quiz 3", "quiz", date(2026, 11, 18))}
    assert [e["id"] for e in pick_events(more, "when is quiz 2", now)][0] == 5          # "2" tells Quiz 2 from Quiz 1 and Quiz 3


# ---------------------------------------------------------------- the endpoint
async def test_an_answer_comes_with_numbered_citations_that_open_the_card(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 will be on Monday 2 November at 10:00 in LHC 104."), ("m2", "Please bring a calculator."))
    st, b = await chat.ask("when is quiz 1 in LHC 104")
    assert st == 200 and b["refused"] is False
    assert b["answer"] == "The quiz is on Mon 2 Nov at 10:00 [1]. (from MA 221)"                          # the course is always named
    c = b["citations"]
    assert [x["n"] for x in c] == [1] and c[0]["team"] == "MA 221"
    assert "Quiz 1" in c[0]["snippet"] or "calculator" in c[0]["snippet"] or c[0]["title"]
    assert "item_id" in c[0] and c[0]["message_id"] in ("m1", "m2")
    assert chat.api.store and (await chat.api.call("GET", f"/items/{c[0]['item_id']}"))[0] == 200        # the app can open it
    assert "quiz 1 will be on monday" in chat.prompts[0].lower()                                           # the context was sent


async def test_another_teams_content_is_never_retrieved_and_its_cards_are_404(chat):
    s = chat.api.store
    await second_team(s)
    await chat.api.join()                                                                                  # a joins T1 only
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    await msg(s, "x1", text="SECRETWORD the PH 105 midsem is on Friday", at=NOW - timedelta(hours=1), conv=CONV2)
    await chat.api.build()
    await embed_all(chat)
    other = await card_in(s, "x1")
    st, b = await chat.ask("SECRETWORD midsem friday")
    assert st == 200
    assert not any("SECRETWORD" in p for p in chat.prompts)                                                 # not in what the model saw
    assert "SECRETWORD" not in json.dumps(b)
    assert (await chat.ask("what is this", item_id=other))[0] == 404                                        # not a team of theirs
    assert (await chat.ask("what is this", item_id=999999))[0] == 404                                       # nor does a bogus id leak
    assert (await chat.api.call("POST", "/chat", "b", json={"question": "x", "item_id": 1}))[0] in (404, 200)  # b never joined: no access


async def card_in(s, mid):
    for i in range(1, 80):
        it = await s.get_feed_item(i)
        if it and it.get("message_id") == mid:
            return i
    raise AssertionError(mid)


async def test_the_card_the_student_is_looking_at_is_context_one_with_its_events(chat):
    await chat.api.join()
    await msg(chat.api.store, "orig", text="Quiz 1 will be on Monday.", at=NOW - timedelta(days=1))
    eid = await make_event(chat.api, "orig", NOW - timedelta(days=1))
    item = await card_for(chat.api, "orig")
    st, b = await chat.ask("when is this", item_id=item)
    assert st == 200
    first = chat.prompts[0].split("<context>")[1]
    assert first.lstrip().startswith("[1] (the card the student is looking at)")
    assert "Quiz 1 will be on Monday." in first and "Event: Quiz 1" in first and "Mon 2 Nov 2026" in first
    assert first.count("Event: Quiz 1") == 1                                                                 # not repeated as a separate fact


async def test_this_means_the_card_only_when_a_card_is_anchored(chat):
    await chat.api.join()
    await msg(chat.api.store, "orig", text="Quiz 1 has moved to Thursday.", at=NOW - timedelta(days=1))
    await make_event(chat.api, "orig", NOW - timedelta(days=1))
    item = await card_for(chat.api, "orig")
    await chat.ask("has this changed", item_id=item)
    await chat.ask("has the quiz changed")
    anchored, plain = chat.prompts[0], chat.prompts[1]
    assert "looking at entry [1]" in anchored and '"this" and "it"' in anchored
    assert "looking at entry [1]" not in plain
    assert '"this" or "it"' in chat.provider.calls[0]["system"] and "answered yes" in chat.provider.calls[0]["system"]


async def test_events_are_facts_with_status_venue_and_an_unconfirmed_flag(chat):
    s = chat.api.store
    await chat.api.join()
    await msg(s, "orig", text="Quiz 1 will be on Monday.", at=NOW - timedelta(days=1))
    eid = await make_event(chat.api, "orig", NOW - timedelta(days=1))
    if getattr(s, "pool", None):
        await s.pool.execute("update events set venue='LHC 104', needs_review=true where id=$1", eid)
    else:
        s.events[eid].update(venue="LHC 104", needs_review=True)
    st, b = await chat.ask("when is the quiz")
    assert st == 200
    p = chat.prompts[0]
    assert "Event: Quiz 1" in p and "venue LHC 104" in p and "UNCONFIRMED" in p and "status scheduled" in p


async def test_without_a_card_the_model_is_told_today_and_which_events_have_passed(chat):
    """The real bug: "when is the next quiz?" with no card was refused, because the model did not know what day it was."""
    s = chat.api.store
    await chat.api.join()
    await msg(s, "old", text="Quiz 1 was on the 20th.", at=NOW - timedelta(days=6))
    await make_event(chat.api, "old", NOW - timedelta(days=6), date(2026, 10, 20))
    await msg(s, "new", text="Quiz 2 is on the 2nd.", at=NOW - timedelta(days=1))
    await make_event(chat.api, "new", NOW - timedelta(days=1), date(2026, 11, 2))
    await chat.api.build()
    st, b = await chat.ask("when is the next quiz")
    assert st == 200
    p = chat.prompts[0]
    assert p.startswith("<today>Monday 26 October 2026, 17:30 (India time)</today>")                      # NOW is 12:00 UTC
    old = next(ln for ln in p.splitlines() if "Tue 20 Oct 2026" in ln and ln.startswith("Event:"))
    new = next(ln for ln in p.splitlines() if "Mon 2 Nov 2026" in ln and ln.startswith("Event:"))
    assert "ALREADY PASSED" in old and "ALREADY PASSED" not in new and "in 7 days" in new
    sysmsg = chat.provider.calls[0]["system"]
    for needle in ("<today>", "ALREADY PASSED", "soonest event that has not passed", "count the event entries", "trust the event entry", "\"rescheduled\" means"):
        assert needle in sysmsg


async def test_retrieved_messages_say_when_they_were_posted_so_older_ones_can_be_told_from_newer(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 will be on Friday 30 October."))
    await chat.ask("when is quiz 1 on friday")
    assert "(message, posted " in chat.prompts[0] and "posted Mon 26 Oct 2026" in chat.prompts[0]


async def test_the_card_the_student_is_looking_at_also_has_its_events_timed(chat):
    await chat.api.join()
    await msg(chat.api.store, "orig", text="Quiz 1 will be on Monday.", at=NOW - timedelta(days=1))
    await make_event(chat.api, "orig", NOW - timedelta(days=1))
    item = await card_for(chat.api, "orig")
    await chat.ask("when is this", item_id=item)
    card_block = chat.prompts[0].split("<context>")[1].split("\n\n")[0]
    assert "Event: Quiz 1" in card_block and "in 7 days" in card_block


def test_ensure_courses_names_every_cited_course_once_and_never_a_placeholder():
    assert ensure_courses("Quiz 3 is on Monday [1].", ["MA 221"]) == "Quiz 3 is on Monday [1]. (from MA 221)"
    assert ensure_courses("In MA 221, Quiz 3 is on Monday [1].", ["MA 221"]) == "In MA 221, Quiz 3 is on Monday [1]."     # already named
    assert ensure_courses("in ma 221 it is Monday", ["MA 221"]) == "in ma 221 it is Monday"                                # case does not matter
    assert ensure_courses("Mixed [1][2].", ["MA 221", "PH 105", "MA 221"]) == "Mixed [1][2]. (from MA 221, PH 105)"        # each once
    assert ensure_courses("In MA 221 [1]; also [2].", ["MA 221", "PH 105"]) == "In MA 221 [1]; also [2]. (from PH 105)"     # only the missing one
    assert ensure_courses("Nothing named.", ["Course", ""]) == "Nothing named."                                           # a placeholder is never added
    assert ensure_courses("Plain.", []) == "Plain."


async def test_an_answer_always_names_the_course_it_came_from_even_if_the_model_forgets(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    chat.provider.replies = [answer("The quiz is on Monday [1].", cited=(1,))]                                            # no course named
    st, b = await chat.ask("when is quiz 1")
    assert st == 200 and b["answer"].endswith("(from MA 221)") and b["answer"].count("MA 221") == 1
    chat.provider.replies = [answer("In MA 221 the quiz is on Monday [1].", cited=(1,))]                                  # already named
    st, b = await chat.ask("when is quiz 1")
    assert b["answer"] == "In MA 221 the quiz is on Monday [1]."
    assert "course each fact comes from" in chat.provider.calls[0]["system"] and "name each course" in chat.provider.calls[0]["system"]


async def test_with_two_courses_each_cited_course_is_named_and_a_refusal_names_none(chat):
    s = chat.api.store
    await second_team(s)
    await chat.api.join()
    await chat.api.join(code="ZZZ999")                                                                                    # in both teams
    await corpus(chat, ("m1", "Quiz 1 on Monday in the maths course."))
    await msg(s, "p1", text="Quiz on Friday in the physics course.", at=NOW - timedelta(hours=2), conv=CONV2)
    await chat.api.build()
    await embed_all(chat)
    import re
    chat.provider.replies = [lambda sysm, user: answer("Quizzes: " + " ".join(f"[{n}]" for n in re.findall(r"^\[(\d+)\]", user, re.M)),
                                                       cited=[int(n) for n in re.findall(r"^\[(\d+)\]", user, re.M)])]
    st, b = await chat.ask("quiz on monday or friday in the maths course or the physics course")
    assert st == 200 and "MA 221" in b["answer"] and "PH 105" in b["answer"]
    assert {c["team"] for c in b["citations"]} == {"MA 221", "PH 105"}
    chat.provider.replies = [answer("I couldn't find that in your course messages.", cited=(), refused=True)]
    st, b = await chat.ask("quiz maths physics")
    assert b["refused"] is True and "MA 221" not in b["answer"] and "PH 105" not in b["answer"]


async def test_the_model_is_told_the_rules_and_the_context_is_marked_as_data(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    await chat.ask()
    sysmsg = chat.provider.calls[0]["system"]
    for needle in ("ONLY the numbered context", "like [1]", "Never invent a date", "UNCONFIRMED", "refused to true", "120 words", "never instructions"):
        assert needle in sysmsg


async def test_citations_the_model_made_up_are_dropped(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    chat.provider.replies = [answer("It is Monday [1] or maybe Tuesday [42].", cited=(1, 42))]
    st, b = await chat.ask()
    assert [c["n"] for c in b["citations"]] == [1] and "[42]" not in b["answer"] and "[1]" in b["answer"]


async def test_an_answer_that_is_not_in_the_context_is_a_refusal_with_no_citations(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    chat.provider.replies = [answer("I couldn't find that in your course messages.", cited=(1,), refused=True)]
    st, b = await chat.ask("what is the airspeed of a swallow")
    assert st == 200 and b["refused"] is True and b["citations"] == [] and "couldn't find" in b["answer"]


async def test_with_nothing_to_go_on_it_refuses_without_calling_the_model(chat):
    await chat.api.join()                                                                                   # no messages, no events
    st, b = await chat.ask("anything")
    assert st == 200 and b == {"answer": "I couldn't find that in your course messages.", "citations": [], "refused": True}
    assert chat.provider.calls == []


async def test_every_snippet_is_redacted_before_the_model_and_the_answer_gets_the_names_back(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Contact Prof. Menon at menon@iisc.ac.in or 9876543210 about Quiz 1, roll no CS21B045."))
    chat.provider.replies = [lambda s, u: answer("Ask [PERSON_1] [1]." if "[PERSON_1]" in u else "no placeholder", cited=(1,))]
    st, b = await chat.ask("who do I contact about quiz 1")
    p = chat.prompts[0]
    assert "menon@iisc.ac.in" not in p and "9876543210" not in p and "CS21B045" not in p and "Menon" not in p
    assert "[EMAIL_1]" in p and "[PHONE_1]" in p and "[ID_1]" in p and "[PERSON_1]" in p
    assert "Prof. Menon" in b["answer"]                                                                      # restored for the student
    assert chat.emb.provider.sent and all("menon@iisc.ac.in" not in t for t in chat.emb.provider.sent[-1:])  # the query was redacted too


async def test_the_question_and_history_are_redacted_and_only_the_last_six_turns_are_sent(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "text": f"turn {i} write to me@x.com"} for i in range(10)]
    st, b = await chat.ask("quiz 1 on monday, mail me@x.com", history=hist)
    assert st == 200
    p = chat.prompts[0]
    assert "me@x.com" not in p and "turn 4" in p and "turn 9" in p and "turn 3" not in p   # the last 6 of the 10: turns 4 to 9
    assert p.count("Student:") == 3 and p.count("Assistant:") == 3


async def test_validation_and_auth(chat):
    await chat.api.join()
    assert (await chat.api.call("POST", "/chat", json={}))[0] == 400
    assert (await chat.ask(" "))[0] == 400
    assert (await chat.ask("x" * 501))[0] == 400
    assert (await chat.ask("ok", history="nope"))[0] == 400
    assert (await chat.ask("ok", history=[{"role": "system", "text": "x"}]))[0] == 400
    assert (await chat.ask("ok", history=[{"role": "user"}]))[0] == 400
    assert (await chat.ask("ok", item_id="1"))[0] == 400
    assert (await chat.ask("ok", item_id=True))[0] == 400
    r = await chat.api.client.post("/api/v1/chat", json={"question": "x"})
    assert r.status == 401
    r = await chat.api.client.post("/api/v1/chat", data="not json", headers={"Authorization": "Bearer u:a"})
    assert r.status == 400


async def test_nothing_is_counted_for_a_rejected_request(chat):
    s = chat.api.store
    await chat.api.join()
    uid = A
    await chat.ask(" ")
    await chat.ask("ok", item_id=424242)
    assert await s.count_chat_uses(uid, NOW - timedelta(days=2)) == 0


async def test_rate_limit_is_20_an_hour_then_60_a_day_with_a_friendly_429(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    s = chat.api.store
    for _ in range(20):
        assert (await chat.ask())[0] == 200
    st, b = await chat.ask()
    assert st == 429 and b["scope"] == "hour" and "try again" in b["error"].lower()
    r = await chat.api.client.post("/api/v1/chat", json={"question": "x"}, headers={"Authorization": "Bearer u:a"})
    assert r.status == 429 and r.headers["Retry-After"]
    assert len(chat.provider.calls) == 20                                                                    # the 21st never reached the model
    assert (await chat.api.call("POST", "/chat", "b", json={"question": "x"}))[0] != 429                     # per user
    chat.api.clock.advance(minutes=61)                                                                       # the hour passes
    for _ in range(20):
        assert (await chat.ask())[0] == 200
    chat.api.clock.advance(minutes=61)
    for _ in range(20):
        assert (await chat.ask())[0] == 200
    chat.api.clock.advance(minutes=61)
    st, b = await chat.ask()                                                                                 # 60 in 3 hours: the day limit
    assert st == 429 and b["scope"] == "day"
    chat.api.clock.advance(days=1)
    assert (await chat.ask())[0] == 200


async def test_a_failing_or_slow_model_gets_a_friendly_error(chat, monkeypatch):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    chat.provider.replies = [ProviderError("server", 500)]
    st, b = await chat.ask()
    assert st == 502 and "busy" in b["error"].lower()

    async def slow(system, user, schema, repair=None):
        await asyncio.sleep(1)

    chat.provider.complete_json = slow
    monkeypatch.setattr(routes_chat, "TIMEOUT_SECONDS", 0.05)
    st, b = await chat.ask()
    assert st == 504 and "too long" in b["error"].lower()


async def test_if_the_embedder_is_down_events_and_the_card_still_answer(chat):
    s = chat.api.store
    await chat.api.join()
    await msg(s, "orig", text="Quiz 1 will be on Monday.", at=NOW - timedelta(days=1))
    await make_event(chat.api, "orig", NOW - timedelta(days=1))
    chat.emb.provider.fail = ProviderError("rate_limit", 429)
    st, b = await chat.ask("when is quiz 1")
    assert st == 200 and "Event: Quiz 1" in chat.prompts[0]


async def test_nothing_the_student_wrote_or_was_told_is_logged_or_stored(chat, caplog):
    s = chat.api.store
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    chat.provider.replies = [answer("TOPSECRETANSWER [1]")]
    with caplog.at_level(logging.DEBUG):
        st, b = await chat.ask("TOPSECRETQUESTION about quiz")
    assert st == 200
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "TOPSECRETQUESTION" not in logged and "TOPSECRETANSWER" not in logged
    assert any('"event": "chat"' in r.getMessage() for r in caplog.records)                                   # counts and timing only
    usage = await s.count_chat_uses(A, NOW - timedelta(days=2))
    assert usage == 1                                                                                          # a time, nothing else
    if not getattr(s, "pool", None):
        assert all(isinstance(t, tuple) and len(t) == 2 for t in s.chat_usage)


async def test_the_log_line_says_where_an_answer_came_from_in_numbers_only(chat, caplog):
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday in SECRETROOM."))
    with caplog.at_level(logging.INFO):
        await chat.ask("quiz 1 SECRETQUESTION")
    line = next(json.loads(r.getMessage()) for r in caplog.records if r.getMessage().startswith("{") and '"event": "chat"' in r.getMessage())
    assert set(line["context"]) == {"card", "event", "message", "file"}
    assert line["teams"] == 1 and line["embedder"] is True and line["hits_raw"] >= 1 and line["hits_kept"] >= 1
    assert isinstance(line["best_score"], float) and "events_scanned" in line and "events_picked" in line and line["ms"] >= 0
    assert "SECRETROOM" not in json.dumps(line) and "SECRETQUESTION" not in json.dumps(line)


async def test_a_left_team_is_out_of_scope(chat):
    await chat.api.join()
    await corpus(chat, ("m1", "LEFTTEAMWORD on Monday."))
    await chat.api.call("DELETE", "/subscriptions/T1")
    st, b = await chat.ask("LEFTTEAMWORD")
    assert st == 200 and b["refused"] is True and chat.provider.calls == []


# ---------------------------------------------------------------- off switch and the profile flag
async def test_when_chat_is_off_the_endpoint_is_503_and_the_profile_says_so(store):
    c, client, _ = await build_chat(store, [lambda s, u: answer()], enabled=False)
    try:
        await c.api.join()
        assert (await c.ask())[0] == 503
        assert (await c.api.call("GET", "/me"))[1]["features"] == {"chat": False}
    finally:
        await client.close()


async def test_when_chat_is_on_the_profile_says_so(chat):
    assert (await chat.api.call("GET", "/me"))[1]["features"] == {"chat": True}


# ---------------------------------------------------------------- the table behind the rate limit
async def test_the_usage_table_is_locked_down_holds_only_times_and_goes_with_the_account(chat):
    s = chat.api.store
    await chat.api.join()
    await corpus(chat, ("m1", "Quiz 1 on Monday."))
    await chat.ask()
    if getattr(s, "pool", None):
        assert await s.pool.fetchval("select relrowsecurity from pg_class where relname = 'chat_usage' and relnamespace = 'public'::regnamespace") is True
        cols = {r["column_name"] for r in await s.pool.fetch("select column_name from information_schema.columns where table_name = 'chat_usage'")}
        assert cols == {"id", "user_id", "at"}                                                                  # no text column, ever
    assert await s.count_chat_uses(A, NOW - timedelta(days=2)) == 1
    assert (await chat.api.call("DELETE", "/me"))[0] == 204
    assert await s.count_chat_uses(A, NOW - timedelta(days=2)) == 0
