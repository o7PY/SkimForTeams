"""What a PERFECT model would return for each eval message (used by `run_eval.py --dry` and the CI test).
This exercises timeparse + resolver + pipeline + scoring end to end without any network. It says nothing about LLM quality."""
from __future__ import annotations


def when(kind="none", date=None, time=None, end=None, text="", relative=None):
    return {"kind": kind, "date": date, "time": time, "end_time": end, "relative": relative, "text": text}


def A(date, time=None, end=None, text=""):
    return when("absolute", date, time, end, text)


def W(wd, q="this", time=None, end=None, weeks=None, text=""):
    return when("relative", None, time, end, text, {"anchor": "weekday", "weekday": wd, "qualifier": q, "days": None, "weeks": weeks})


def P(days, time=None, end=None, text=""):
    return when("relative", None, time, end, text, {"anchor": "posted", "weekday": None, "qualifier": None, "days": days, "weeks": None})


def V(text=""):
    return when("vague", text=text)


def SAME(time=None, end=None, text=""):
    return when("same_as_existing", None, time, end, text)


def it(action, type, title, w=None, deadline=None, venue=None, details=None, conf="high", hint=None):
    return {"action": action, "type": type, "title": title, "is_deadline": type in ("assignment", "project") if deadline is None else deadline,
            "refers_to_existing": action != "create", "reference_hint": hint, "when": w or when(), "venue": venue,
            "details": details, "confidence": conf}


def ex(cat, *items, conf="high"):
    return {"category": cat, "confidence": conf, "items": list(items)}


RECORDED = {
    1: ex("announcement"), 2: ex("announcement"),
    3: ex("event", it("create", "assignment", "Assignment 1", A("2026-10-26", "23:59"))),
    4: ex("event", it("create", "quiz", "Quiz 1", W("fri"), details="Syllabus: Lectures 1-4")),
    5: ex("chitchat"),
    6: ex("change", it("cancel", "class", "Lecture", P(0))),
    7: ex("event", it("create", "class", "Makeup lecture", A("2026-10-24", "11:00", "12:00"), venue="LHC 104")),
    8: ex("event", it("restate", "quiz", "Quiz 1", P(1))),
    9: ex("change", it("set_time", "quiz", "Quiz 1", SAME("10:15", "10:45"))),
    10: ex("resource"), 11: ex("question"),
    12: ex("change", it("reschedule", "assignment", "Assignment 1", A("2026-10-28", "23:59"))),
    13: ex("resource"),
    14: ex("event", it("create", "assignment", "Assignment 2", W("mon", "next", text="by next Monday"), conf="medium")),
    15: ex("change", it("reschedule", "office_hours", "Office hours", P(0, "17:00", "18:00"))),
    16: ex("event", it("create", "exam", "Midsem", A("2026-11-09", "14:00", "17:00"), venue="Main Exam Hall", details="Syllabus: Lectures 1-12")),
    17: ex("announcement"),
    18: ex("event", it("create", "quiz", "Quiz 2", A("2026-11-04")), it("create", "quiz", "Quiz 3", A("2026-11-18"))),
    19: ex("event", it("create", "lab", "Lab session (Problem Set 3)", A("2026-10-30", "14:00", "16:00"), venue="Computer Lab 2")),
    20: ex("chitchat"),
    21: ex("change", it("cancel", "lab", "Lab session", hint="the lab on Friday")),
    22: ex("change", it("reschedule", "lab", "Lab session", A("2026-11-03", text="Tuesday, 3 November, same time and place"))),
    23: ex("chitchat"), 24: ex("question"),
    25: ex("event", it("restate", "assignment", "Assignment 2", P(1, text="tomorrow night"))),
    26: ex("event", it("create", "project", "Project group list", W("fri", text="by Friday"))),
    27: ex("event", it("create", "project", "Project proposal", A("2026-11-13")),
           it("create", "presentation", "Final presentations", V("last week of classes"), conf="low")),
    28: ex("change", it("cancel", "office_hours", "Office hours", P(0))),
    29: ex("event", it("restate", "lab", "Lab session", P(0, "14:00"), venue="Computer Lab 2")),
    30: ex("change", it("reschedule", "quiz", "Quiz 2", A("2026-11-06", text="Friday, 6 November, same time"))),
    31: ex("resource"), 32: ex("question"), 33: ex("announcement"), 34: ex("announcement"),
    35: ex("change", it("venue", "exam", "Midsem", venue="LHC 201 / LHC 202")),
    36: ex("event", it("create", "class", "Doubt-clearing class", P(1, "16:00"), venue="LHC 104")),
    37: ex("event", it("restate", "exam", "Midsem", P(1, "14:00"), venue="LHC 201 / LHC 202")),
    38: ex("event", it("create", "assignment", "Assignment 3", W("wed", None, weeks=1, text="one week after release"), conf="medium")),
    39: ex("change", it("cancel", "class", "Lecture", W("wed")), it("cancel", "class", "Lecture", W("fri"))),
    40: ex("change", it("reschedule", "assignment", "Assignment 3", A("2026-11-20"))),
    41: ex("event", it("restate", "project", "Project proposal")),
    42: ex("event", it("create", "other", "Midsem answer script viewing", W("thu", None, "15:00", "17:00"), venue="Room 213")),
    43: ex("chitchat"),
    44: ex("change", it("reschedule", "quiz", "Quiz 3", A("2026-11-20", "17:00"), deadline=True, details="take-home quiz")),
    45: ex("resource"), 46: ex("question"),
    47: ex("event", it("restate", "project", "Project proposal", P(0, "23:59"))),
    48: ex("event", it("create", "exam", "Endsem", V("first week of December"), conf="low")),
    49: ex("change", it("reschedule", "office_hours", "Office hours (weekly)", W("thu", "next", "16:00", "17:00"))),
    50: ex("change", it("set_date", "presentation", "Final presentations (day 1)", A("2026-11-30", "14:00", "17:00"), venue="LHC 104"),
           it("create", "presentation", "Final presentations (day 2)", A("2026-12-01", "14:00", "17:00"), venue="LHC 104")),
}
