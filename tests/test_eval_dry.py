"""The whole eval set through the real pipeline with a PERFECT recorded model: proves timeparse + resolver + pipeline +
scoring agree with the human answer key. Real-LLM quality is measured by scripts/run_eval.py, not here."""
import json
import sys
from pathlib import Path

import pytest
from ai_fakes import ScriptedProvider

sys.path.insert(0, str(Path(__file__).parent / "eval"))
from recorded import RECORDED  # noqa: E402

from skim.ai.evaluate import eval_config, format_report, load_rows, replay, score  # noqa: E402
from skim.ai.pipeline import Pipeline  # noqa: E402
from skim.ai.providers.router import Router  # noqa: E402
from skim.storage.memory import MemoryStorage  # noqa: E402

CSV = Path(__file__).parent / "eval" / "eval_messages.csv"


async def run(rows, recorded=RECORDED):
    script = {r["text"]: recorded[int(r["msg_id"])] for r in rows}
    provider = ScriptedProvider(script)
    store = MemoryStorage()
    pipe = Pipeline(store, Router([provider]), eval_config())
    obs = await replay(rows, store, pipe)
    return score(rows, obs), store, provider, obs


async def test_a_perfect_model_meets_every_target_and_all_rows():
    rows = load_rows(CSV)
    rep, store, provider, _ = await run(rows)
    print(format_report(rep))
    assert rep.passed, "\n".join(rep.failures)
    hard = [f for f in rep.failures if not f.endswith("(info)")]
    assert hard == [], "\n".join(hard)
    assert rep.metrics["category_accuracy"] == 1.0 and rep.metrics["link_accuracy"] == 1.0
    assert rep.counts["unprocessed_messages"] == 0 and rep.counts["fp"] == 0 and rep.counts["fn"] == 0


async def test_dry_run_has_no_status_or_venue_disagreements_with_the_key():
    rows = load_rows(CSV)
    rep, *_ = await run(rows)
    assert [f for f in rep.failures if f.endswith("(info)")] == []


async def test_event_history_matches_the_story_of_the_quiz_and_the_lab():
    rows = load_rows(CSV)
    _, store, *_ = await run(rows)
    by_title = {}
    for e in await store.list_events(team_id="eval-team"):
        by_title.setdefault(e["title"], []).append(e)
    (quiz1,) = by_title["Quiz 1"]
    (lab,) = by_title["Lab session (Problem Set 3)"]
    kinds = lambda e: [c["change_type"] for c in sorted(store.event_changes, key=lambda c: c["id"]) if c["event_id"] == e["id"]]  # noqa: E731
    assert kinds(quiz1) == ["created", "restated", "time_set"]
    assert kinds(lab) == ["created", "cancelled", "uncancelled", "rescheduled", "restated"]
    assert len(by_title["Lecture"]) == 3 and len(by_title["Office hours"]) == 2


async def test_a_broken_model_is_detected_by_the_scorer():
    rows = load_rows(CSV)
    bad = {int(k): v for k, v in json.loads(json.dumps(RECORDED)).items()}
    bad[8]["items"][0]["action"] = "create"
    bad[8]["items"][0]["refers_to_existing"] = False
    bad[8]["items"][0]["title"] = "Surprise test"       # a reminder the model fails to tie to Quiz 1 -> a duplicate event
    bad[9]["items"][0]["when"]["time"] = "11:00"       # wrong time
    bad[12]["category"] = "chitchat"; bad[12]["items"] = []
    rep, *_ = await run(rows, {int(k): v for k, v in bad.items()})
    assert not rep.passed and rep.metrics["duplicate_events"] >= 1
    assert any("msg 12" in f for f in rep.failures) and any("msg 9 " in f for f in rep.failures)


async def test_scorer_flags_invented_times_and_student_events():
    rows = load_rows(CSV)
    bad = {int(k): v for k, v in json.loads(json.dumps(RECORDED)).items()}
    bad[4]["items"][0]["when"] = {**bad[4]["items"][0]["when"], "time": "10:00"}  # label: time unknown
    bad[11] = {"category": "change", "confidence": "high", "items": [RECORDED[12]["items"][0]]}  # student "changes" an event
    rep, *_ = await run(rows, {int(k): v for k, v in bad.items()})
    assert rep.metrics["invented_times"] >= 1 and "INVENTED" in "\n".join(rep.failures)
    assert rep.metrics["student_events"] == 0  # the PIPELINE ignores student items, so none reach the store
