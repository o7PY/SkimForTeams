"""P3-DIFF-017: wrong merges seen live. Titles with different identifiers never merge; a specific event is created next to a
generic one and the generic one is flagged; "same time and place" copy from the correctly matched event."""
from datetime import date, datetime, time, timezone

from ai_fakes import extraction, item
from ai_harness import Harness, IST
from test_ai_pipeline import ABS, WD

from skim.ai.resolver import candidates_for, identifiers, tokens

POSTED = date(2026, 10, 20)


def ev(i, type_, title, d=date(2026, 10, 23), status="scheduled", **kw):
    return {"id": i, "type": type_, "title": title, "event_date": d, "status": status, "updated_at": datetime(2026, 10, 1, tzinfo=timezone.utc), **kw}


def groups(item_type, title, events, new_date=None):
    return candidates_for(item_type, title, events, set(), POSTED, new_date)


def ids(title):
    return identifiers(*tokens(title))


def test_identifiers():
    assert ids("Quiz") == frozenset() and ids("the lab") == frozenset() and ids("Office hours") == frozenset()
    assert ids("Quiz 2") == {"2"} and ids("Assignment 4") == {"4"}
    assert ids("Lab session (Problem Set 3)") == {"problem", "set", "3"} and ids("Project proposal") == {"proposal"}


def test_distinct_identifiers_never_merge_and_generic_events_are_reported():
    quiz, q1, q2 = ev(1, "quiz", "Quiz"), ev(2, "quiz", "Quiz 1"), ev(3, "quiz", "Quiz 2")
    g = groups("quiz", "Quiz 2", [quiz, q1])
    assert not g["exact"] and not g["subset"] and not g["generic"]            # not Quiz 1, not the generic Quiz
    assert [e["id"] for e in g["generic_events"]] == [1]                       # ...but the generic one is reported
    assert [e["id"] for e in groups("quiz", "Quiz 2", [quiz, q1, q2])["exact"]] == [3]
    a = groups("assignment", "Assignment 4", [ev(1, "assignment", "Assignment")])
    assert not a["exact"] and not a["subset"] and [e["id"] for e in a["generic_events"]] == [1]
    assert not groups("quiz", "Quiz 3", [q1, q2])["subset"]                    # a new number is a new quiz


def test_only_an_item_without_identifier_may_weakly_match_a_specific_event():
    q2 = ev(3, "quiz", "Quiz 2")
    g = groups("quiz", "the quiz", [q2])
    assert [e["id"] for e in g["generic"]] == [3]                              # "the quiz" -> the only quiz
    g = groups("quiz", "Quiz", [ev(1, "quiz", "Quiz 1"), q2])
    assert sorted(e["id"] for e in g["generic"]) == [1, 3]                     # several: the caller asks the tie-break
    assert [e["id"] for e in groups("quiz", "Quiz", [ev(1, "quiz", "the Quiz")])["exact"]] == [1]


def test_compatible_specific_titles_still_match():
    lab = ev(1, "lab", "Lab session (Problem Set 3)")
    assert [e["id"] for e in groups("lab", "Lab Problem Set 3", [lab])["exact"]] == [1]
    assert [e["id"] for e in groups("lab", "Problem Set 3 lab", [ev(1, "lab", "Lab (Problem Set 3, extra)")])["exact"]] == [1]
    assert [e["id"] for e in groups("project", "Project proposal", [ev(1, "project", "Project proposal 1")])["subset"]] == [1]
    assert [e["id"] for e in groups("exam", "midsem", [ev(2, "exam", "Midsem")])["exact"]] == [2]
    assert not groups("quiz", "Quiz 2", [ev(1, "exam", "Quiz 2")])["exact"]    # other type


async def test_quiz_2_and_its_correction_do_not_merge_into_an_older_generic_quiz():
    """The live sequence: 'quiz next sunday' (generic, LHC 104) ... 'Quiz 2 on Friday, 3 PM, in LHC 101' ... 'Correction'."""
    h = await Harness({
        "quiz next sunday": extraction("event", [item("create", "quiz", "Quiz", WD("sun", "next", text="next sunday"), venue="LHC 104")]),
        "Quiz 2 on Friday, 3 PM, in LHC 101": extraction("event", [item("create", "quiz", "Quiz 2", WD("fri", t="15:00", text="Friday, 3 PM"), venue="LHC 101")]),
        "Correction: Quiz 2 is moved to Monday, same time": extraction("change", [item("reschedule", "quiz", "Quiz 2", WD("mon", text="Monday, same time"))]),
    }).setup()
    await h.post("2026-10-19 10:00", "quiz next sunday")
    await h.post("2026-10-20 10:00", "Quiz 2 on Friday, 3 PM, in LHC 101")
    await h.post("2026-10-21 10:00", "Correction: Quiz 2 is moved to Monday, same time")
    await h.run()
    evs = {e["title"]: e for e in await h.events()}
    assert set(evs) == {"Quiz", "Quiz 2"}
    q2, generic = evs["Quiz 2"], evs["Quiz"]
    assert q2["event_date"] == date(2026, 10, 26) and q2["status"] == "rescheduled"
    assert q2["time_known"] and q2["starts_at"].astimezone(IST).time() == time(15, 0)        # the time survived "same time"
    assert q2["venue"] == "LHC 101"                                                           # not the generic quiz's LHC 104
    assert generic["event_date"] == date(2026, 10, 25) and generic["venue"] == "LHC 104" and not generic["time_known"]
    assert generic["status"] == "scheduled" and generic["needs_review"] is True               # flagged, otherwise untouched
    assert await h.changes(q2["id"]) == ["created", "rescheduled"]
    flagged = (await h.store.get_event(generic["id"]))["changes"]
    assert [c["change_type"] for c in flagged] == ["created", "needs_review"]
    assert len([k for k in h.store.message_event_links if k[2] == generic["id"]]) == 1       # only the message that created it links


async def test_assignment_4_does_not_merge_into_a_generic_assignment():
    h = await Harness({
        "assignment due next friday": extraction("event", [item("create", "assignment", "Assignment", WD("fri", "next", text="next friday"))]),
        "Assignment 4 due next Wednesday 11:59 PM": extraction("event", [item("create", "assignment", "Assignment 4", WD("wed", "next", t="23:59", text="next Wednesday 11:59 PM"))]),
    }).setup()
    await h.post("2026-10-19 10:00", "assignment due next friday")
    await h.post("2026-10-22 10:00", "Assignment 4 due next Wednesday 11:59 PM")
    await h.run()
    evs = {e["title"]: e for e in await h.events()}
    assert set(evs) == {"Assignment", "Assignment 4"}
    a4 = evs["Assignment 4"]
    assert a4["event_date"] == date(2026, 10, 28) and a4["time_known"] and not a4["time_defaulted"]
    assert a4["starts_at"].astimezone(IST).time() == time(23, 59)
    assert evs["Assignment"]["needs_review"] and evs["Assignment"]["event_date"] == date(2026, 10, 23)


async def test_reminders_and_the_generic_reference_still_resolve_to_the_right_event():
    h = await Harness({
        "Quiz 1 on 23 Oct": extraction("event", [item("create", "quiz", "Quiz 1", ABS("2026-10-23", "10:00", text="23 Oct"))]),
        "Quiz 2 on 30 Oct": extraction("event", [item("create", "quiz", "Quiz 2", ABS("2026-10-30", "10:00", text="30 Oct"))]),
        "Reminder: Quiz 2 on Friday": extraction("event", [item("restate", "quiz", "quiz-2", ABS("2026-10-30", text="Friday"))]),
        "the quiz is cancelled": extraction("change", [item("cancel", "quiz", "the quiz")]),
        "__tiebreak__": "2",
    }).setup()
    await h.post("2026-10-19 10:00", "Quiz 1 on 23 Oct")
    await h.post("2026-10-19 11:00", "Quiz 2 on 30 Oct")
    await h.post("2026-10-27 09:00", "Reminder: Quiz 2 on Friday")
    await h.run()
    assert sorted(e["title"] for e in await h.events()) == ["Quiz 1", "Quiz 2"]               # the reminder merged, no duplicate
    q2 = next(e for e in await h.events() if e["title"] == "Quiz 2")
    assert await h.changes(q2["id"]) == ["created", "restated"] and q2["time_known"]           # reminder did not erase the time
