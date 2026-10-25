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


async def test_reset_then_rerun_rebuilds_the_same_events_without_duplicates():
    h = await Harness({
        "quiz next sunday": extraction("event", [item("create", "quiz", "Quiz", WD("sun", "next", text="next sunday"), venue="LHC 104")]),
        "Quiz 2 on Friday, 3 PM, in LHC 101": extraction("event", [item("create", "quiz", "Quiz 2", WD("fri", t="15:00", text="Friday, 3 PM"), venue="LHC 101")]),
        "Correction: Quiz 2 is moved to Monday, same time": extraction("change", [item("reschedule", "quiz", "Quiz 2", WD("mon", text="Monday, same time"))]),
    }).setup()
    for when, text in (("2026-10-19 10:00", "quiz next sunday"), ("2026-10-20 10:00", "Quiz 2 on Friday, 3 PM, in LHC 101"),
                       ("2026-10-21 10:00", "Correction: Quiz 2 is moved to Monday, same time")):
        await h.post(when, text)
    await h.run()
    snap = lambda evs: sorted((e["title"], e["event_date"], e["status"], e["time_known"], e["venue"], e["needs_review"]) for e in evs)  # noqa: E731
    first = snap(await h.events())
    assert len(first) == 2
    counts = await h.store.reset_ai_data("T1", dry_run=False)
    assert counts["events"] == 2 and await h.events() == []
    await h.run()
    assert snap(await h.events()) == first                                                   # same events, none doubled


# ---- P3-DIFF-020: a reminder must never move an event (live bug, 2026-10-26)
A = "Quiz 1 will be on Friday, 30 October, at 10 AM in LHC 104."
B = "Correction: Quiz 1 is moved to Monday, 2 November, same time and place."


def _script(extra=None):
    s = {A: extraction("event", [item("create", "quiz", "Quiz 1", ABS("2026-10-30", "10:00", text="Friday, 30 October, at 10 AM"), venue="LHC 104")]),
         B: extraction("change", [item("reschedule", "quiz", "Quiz 1", {"kind": "absolute", "date": "2026-11-02", "text": "Monday, 2 November, same time and place"})])}
    s.update(extra or {})
    return s


def _monday_reminder(text="Reminder: Quiz 1 is on Monday", action="restate"):
    return {text: extraction("event", [item(action, "quiz", "Quiz 1", WD("mon", text="on Monday"))])}


async def _quiz1(h):
    (e,) = await h.events()
    return e


async def test_reminder_with_the_events_weekday_is_a_restate_even_when_next_monday_is_earlier():
    """The live case: the reminder was posted Monday 01:44 IST ('Sun 25 Oct' in UTC), so 'on Monday' resolves to TODAY (26 Oct)."""
    for posted in ("2026-10-26 01:44", "2026-10-25 10:00"):
        h = await Harness(_script(_monday_reminder())).setup()
        await h.post("2026-10-23 09:00", A)
        await h.post("2026-10-24 09:00", B)
        await h.post(posted, "Reminder: Quiz 1 is on Monday")
        await h.run()
        e = await _quiz1(h)
        assert (e["event_date"], e["status"], e["venue"], e["time_known"]) == (date(2026, 11, 2), "rescheduled", "LHC 104", True), posted
        assert e["starts_at"].astimezone(IST).time() == time(10, 0), posted        # the 10 AM survives
        assert await h.changes(e["id"]) == ["created", "rescheduled", "restated"], posted
        assert e["needs_review"] is False                                          # same weekday: not even a conflict


async def test_a_reminder_that_conflicts_with_the_events_date_changes_nothing_and_is_flagged():
    h = await Harness(_script(_monday_reminder("Reminder: Quiz 1 is on Tuesday") | {
        "Reminder: Quiz 1 is on Tuesday": extraction("event", [item("restate", "quiz", "Quiz 1", WD("tue", text="on Tuesday"))])})).setup()
    await h.post("2026-10-23 09:00", A)
    await h.post("2026-10-24 09:00", B)
    await h.post("2026-10-26 10:00", "Reminder: Quiz 1 is on Tuesday")
    await h.run()
    e = await _quiz1(h)
    assert (e["event_date"], e["status"], e["time_known"]) == (date(2026, 11, 2), "rescheduled", True)   # untouched
    assert e["needs_review"] is True
    ch = (await h.store.get_event(e["id"]))["changes"]
    assert [c["change_type"] for c in ch] == ["created", "rescheduled", "needs_review"]
    assert ch[-1]["new_values"]["reason"] == "reminder conflicts with the event's date" and ch[-1]["new_values"]["stated_date"] == "2026-10-27"


async def test_only_explicit_change_actions_reschedule_so_a_mislabelled_item_is_flagged_not_applied():
    """The resolver decides, not the prompt: create/restate/venue/details never move an event; reschedule does."""
    for action in ("create", "restate", "venue", "details"):
        h = await Harness(_script({"moved?": extraction("event", [item(action, "quiz", "Quiz 1", ABS("2026-11-09", "11:00", text="9 Nov 11am"))])})).setup()
        await h.post("2026-10-23 09:00", A)
        await h.post("2026-10-24 09:00", "moved?")
        await h.run()
        e = await _quiz1(h)
        assert (e["event_date"], e["status"]) == (date(2026, 10, 30), "scheduled"), action
        assert e["needs_review"] is True, action
    h = await Harness(_script({"moved?": extraction("change", [item("reschedule", "quiz", "Quiz 1", ABS("2026-11-09", "11:00", text="9 Nov 11am"))])})).setup()
    await h.post("2026-10-23 09:00", A)
    await h.post("2026-10-24 09:00", "moved?")
    await h.run()
    assert (await _quiz1(h))["event_date"] == date(2026, 11, 9)


async def test_a_reminder_may_fill_in_what_is_missing_but_never_overwrite():
    h = await Harness({
        "Quiz 1 next Friday": extraction("event", [item("create", "quiz", "Quiz 1", WD("fri", "next", text="next Friday"))]),
        "Reminder: Quiz 1 on Friday at 3 PM": extraction("event", [item("restate", "quiz", "Quiz 1", WD("fri", t="15:00", text="Friday at 3 PM"))]),
        "Reminder: Quiz 1 at 4 PM": extraction("event", [item("restate", "quiz", "Quiz 1", {"kind": "none", "time": "16:00", "text": "at 4 PM"})]),
    }).setup()
    await h.post("2026-10-20 09:00", "Quiz 1 next Friday")
    await h.post("2026-10-21 09:00", "Reminder: Quiz 1 on Friday at 3 PM")
    await h.post("2026-10-22 09:00", "Reminder: Quiz 1 at 4 PM")
    await h.run()
    e = await _quiz1(h)
    assert e["event_date"] == date(2026, 10, 23) and e["starts_at"].astimezone(IST).time() == time(15, 0)   # time filled once...
    assert e["needs_review"] is True                                                                          # ...the 4 PM conflict is flagged
    assert await h.changes(e["id"]) == ["created", "time_set", "needs_review"]
