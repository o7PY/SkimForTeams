"""PHASE6A mini eval for "Ask the course". Runs the REAL ChatService (retrieval, redaction, prompt, post-check) and scores each
question against tests/eval/chat_questions.csv. Appends a section to Bookkeeping/PHASE6A_EVAL.md.

Default corpus = the Phase 3 eval corpus (MA 221, tests/eval/eval_messages.csv) in an isolated in-memory store, "now" fixed at
2026-11-08 12:00 IST, events built from the CSV labels (so the answers do not depend on extraction quality):

    set -a; . ./.env; set +a      # needs GEMINI_API_KEY, GROQ_API_KEY and GROQ_MODEL in the environment
    python scripts/run_chat_eval.py [--interval 2] [--no-log]
    python scripts/run_chat_eval.py --dry        # offline plumbing check: hash embeddings + an extractive stand-in model (NOT a quality result)

Live mode (read-only against the real database, one student's own teams; writes nothing, not even the rate-limit row):

    python scripts/run_chat_eval.py --live --user-id <uuid> --questions tests/eval/chat_questions_live.csv

Question file columns: id, question, anchor_text (answer anchored to the card whose message contains it), expected_source (text that
must appear in a citation's title or snippet), expected_fact (`;` separates facts that must ALL appear, `|` separates alternatives),
forbid (`;` separated texts that must NOT appear), expect_refusal (y/n), notes. Nothing here is sent anywhere but the model providers,
and only text that has been through the Phase 3 redactor."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src")]

from skim.ai.config import AIConfig  # noqa: E402
from skim.ai.embeddings import Embedder, names_of  # noqa: E402
from skim.ai.evaluate import CONV, PROF, STUDENT, TEAM, load_rows  # noqa: E402
from skim.ai.providers.base import ProviderReply  # noqa: E402
from skim.ai.providers.router import AllProvidersFailed, Router  # noqa: E402
from skim.ai.services import AIServices  # noqa: E402
from skim.api.chat import ChatService  # noqa: E402
from skim.jobs.ai_embed import _row, _text  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=IST)
USER = "00000000-0000-4000-8000-0000000000e1"
MODEL_FACTS = {"max_words": 120}


class HashProvider:
    """--dry: deterministic bag-of-words vectors, no network."""
    name, model = "gemini", "dry-hash"

    async def embed(self, model, texts, dims, task):
        out = []
        for t in texts:
            v = [0.0] * dims
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
            out.append(v)
        return out, None


class ExtractiveModel:
    """--dry stand-in for the LLM: answers with the first line of context entry [1] and cites it. Proves the plumbing, says nothing
    about answer quality."""
    name, model = "dry", "extractive"

    async def complete_json(self, system, user, schema, repair=None):
        ctx = user.split("<context>")[1].split("</context>")[0]
        first = re.search(r"\[1\][^\n]*\n([^\n]+)", ctx)
        refused = first is None
        text = ("I couldn't find that in your course messages." if refused else f"{first.group(1)[:300]} [1]")
        return ProviderReply(json.dumps({"answer": text, "cited": [] if refused else [1], "refused": refused}), 1)

    async def close(self):
        pass


def to_dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=IST)


async def build_synthetic(embedder: Embedder, now: datetime):
    from skim.storage.memory import MemoryStorage
    s = MemoryStorage()
    await s.upsert_team({"id": TEAM, "aad_group_id": "eval-g", "name": "MA 221", "tenant_id": "eval"})
    await s.mark_team_installed(TEAM, "EVAL01", PROF, datetime(2026, 10, 1, tzinfo=timezone.utc))
    await s.upsert_conversation({"id": CONV, "conversation_type": "channel", "team_id": TEAM, "channel_id": CONV, "name": "General"})
    await s.upsert_channel({"id": CONV, "team_id": TEAM, "display_name": "General", "membership_type": "standard"})
    rows = [r for r in load_rows(ROOT / "tests" / "eval" / "eval_messages.csv") if to_dt(r["posted_at_ist"]) <= now]
    seen: set[str] = set()
    for r in rows:
        if r["msg_id"] in seen:
            continue
        seen.add(r["msg_id"])
        await s.backfill_message({
            "conversation_id": CONV, "message_id": r["msg_id"], "reply_to_id": None, "from_aad_id": PROF if r["author"] == "prof" else STUDENT,
            "from_name": "Prof" if r["author"] == "prof" else "Student", "text": r["text"], "html": None, "importance": None, "mentions": None,
            "subject": r["subject"] or None, "created_at": to_dt(r["posted_at_ist"]), "last_edited_at": None, "deleted_at": None}, [], [])
    # Events straight from the labels, final state as of `now` (a later row for the same title overrides an earlier one).
    by_title: dict[str, dict] = {}
    msg_events: dict[str, list[str]] = {}
    for r in rows:
        if r["event_title"]:
            by_title[r["event_title"]] = r
            msg_events.setdefault(r["msg_id"], []).append(r["event_title"])
    ids: dict[str, int] = {}
    for title, r in by_title.items():
        when = r["start_or_due_ist"]
        known = r["time_known"] == "y" and " " in when
        d = datetime.strptime(when[:10], "%Y-%m-%d").date() if when else None
        eid = len(s.events) + 1
        s.events[eid] = {"id": eid, "team_id": TEAM, "type": r["event_type"], "title": title, "is_deadline": r["event_type"] in ("assignment", "project"),
                         "starts_at": to_dt(when) if known else None, "ends_at": None, "event_date": d, "time_known": known, "time_defaulted": False,
                         "venue": r["venue"] or None, "details": None, "status": r["status"] or "scheduled",
                         "confidence": "low" if not d else "high", "needs_review": not d, "source_kind": "message",
                         "source_ref": {}, "created_at": now, "updated_at": now}
        ids[title] = eid
    for r in rows:
        if r["author"] != "prof" or r["category"] == "chitchat":
            continue
        await s.upsert_feed_item({"team_id": TEAM, "kind": "message", "conversation_id": CONV, "message_id": r["msg_id"],
                                  "occurred_at": to_dt(r["posted_at_ist"]), "hidden": False},
                                 [ids[t] for t in msg_events.get(r["msg_id"], []) if t in ids])
    await s.upsert_app_user(USER, "eval@example.invalid", "Eval Student", now)
    await s.add_subscription(USER, TEAM, datetime(2026, 10, 19, tzinfo=IST))
    emb_rows = await s.list_unembedded(embedder.model, 10_000)
    vecs = await embedder.embed_documents([_text(r) for r in emb_rows], [names_of(r) for r in emb_rows])
    await s.save_embeddings([_row(r, v, embedder.model) for r, v in zip(emb_rows, vecs)])
    return s, len(rows), len(by_title)


def anchor_item(storage, text: str) -> int | None:
    if not text:
        return None
    for (cid, mid), m in storage.messages.items():
        if text.lower() in ((m.get("subject") or "") + " " + (m.get("text") or "")).lower():
            for iid, f in storage.feed_items.items():
                if f.get("message_id") == mid and f.get("conversation_id") == cid:
                    return iid
    return None


def score(q: dict, out: dict) -> dict:
    ans = out["answer"].lower()
    cites = " ".join((c["title"] + " " + c["snippet"]).lower() for c in out["citations"])
    refusal_ok = out["refused"] == (q["expect_refusal"].strip().lower() == "y")
    fact_ok = source_ok = True
    if q["expect_refusal"].strip().lower() != "y":
        groups = [g for g in q["expected_fact"].split(";") if g.strip()]
        fact_ok = all(any(a.strip().lower() in ans for a in g.split("|") if a.strip()) for g in groups)
        src = q["expected_source"].strip().lower()
        source_ok = (not src) or src in cites
    forbid_ok = not any(f.strip() and f.strip().lower() in ans for f in q["forbid"].split(";"))
    words_ok = len(out["answer"].split()) <= MODEL_FACTS["max_words"] + 5
    cites_ok = all(isinstance(c["n"], int) for c in out["citations"]) and (not out["refused"] or not out["citations"])
    return {"refusal": refusal_ok, "fact": fact_ok, "source": source_ok, "forbid": forbid_ok, "words": words_ok, "cites": cites_ok}


def git_rev() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--user-id")
    ap.add_argument("--questions", default=str(ROOT / "tests" / "eval" / "chat_questions.csv"))
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between model calls (free-tier pacing)")
    ap.add_argument("--log", default=str(ROOT / "Bookkeeping" / "PHASE6A_EVAL.md"))
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--only", help="comma-separated question ids to run (e.g. 15 or 11,15): a cheap re-check after a prompt change")
    a = ap.parse_args()
    questions = list(csv.DictReader(open(a.questions, newline="", encoding="utf-8")))
    if a.only:
        wanted = {x.strip() for x in a.only.split(",")}
        questions = [q for q in questions if q["id"] in wanted]
    cfg = AIConfig.from_env()
    if not a.dry and not cfg.enabled:
        print("Real run needs GEMINI_API_KEY, GROQ_API_KEY and GROQ_MODEL in the environment (or use --dry).", file=sys.stderr)
        return 2
    close = None
    if a.dry:
        cfg = AIConfig(enabled=True, groq_api_key="x", gemini_api_key="x", groq_model="x", embed_model="dry-hash", min_call_interval=0)
        embedder = Embedder(HashProvider(), "dry-hash")
        router = Router([ExtractiveModel()])
        label = "DRY (hash embeddings, extractive stand-in model: plumbing only)"
    else:
        from dataclasses import replace
        cfg = replace(cfg, min_call_interval=a.interval)
        services = AIServices.build(cfg, None)
        embedder, router, close = services.embedder, services.router, services.close
        label = f"{cfg.gemini_model} (primary), {cfg.groq_model} (fallback), embeddings {cfg.embed_model}"
    stats = {}
    if a.live:
        if not a.user_id or not os.environ.get("DATABASE_URL"):
            print("--live needs --user-id and DATABASE_URL", file=sys.stderr)
            return 2
        from skim.storage.postgres import PostgresStorage
        storage = PostgresStorage(os.environ["DATABASE_URL"])
        await storage.start()
        uid, now = a.user_id, datetime.now(timezone.utc)
        corpus = "LIVE (read-only, the student's own teams)"
    else:
        embedder.interval = a.interval if not a.dry else 0
        now = NOW
        storage, nmsg, nev = await build_synthetic(embedder, now)
        uid = USER
        corpus = f"MA 221 eval corpus, now = {now:%Y-%m-%d %H:%M} IST, {nmsg} messages, {nev} events (from labels)"
    svc = ChatService(storage, router, embedder, cfg, staff_ids=frozenset({PROF}), id_secret="eval")
    results = []
    for q in questions:
        item = anchor_item(storage, q["anchor_text"]) if not a.live else None
        try:
            out = await svc.answer(uid, q["question"], item, [], now)
            error = None
        except AllProvidersFailed as e:
            out, error = {"answer": "", "citations": [], "refused": True}, f"all providers failed: {e.error_class}"
        res = score(q, out) if not error else {k: False for k in ("refusal", "fact", "source", "forbid", "words", "cites")}
        results.append((q, out, res, error))
        if not a.dry:
            await asyncio.sleep(a.interval)
    if close:
        await close()

    n = len(results)
    passed = [r for r in results if all(r[2].values())]
    lines = [f"\n\n## Chat eval run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC" + (" (DRY, plumbing only)" if a.dry else ""),
             f"- git: {git_rev()}", f"- providers/models: {label}", f"- corpus: {corpus}", f"- questions: {a.questions.split('/')[-1]} ({n})",
             f"- router stats: {dict(getattr(router, 'stats', Counter()))}",
             f"- result: **{len(passed)}/{n} questions fully correct**" + (" (a dry run cannot be quality evidence)" if a.dry else ""),
             "", "| # | Question | refuse | fact | source | no-forbidden | <=120 words |", "|---|---|---|---|---|---|---|"]
    for q, out, res, err in results:
        lines.append(f"| {q['id']} | {q['question'][:60]} | " + " | ".join("PASS" if res[k] else "FAIL" for k in ("refusal", "fact", "source", "forbid", "words")) + " |")
    fails = [(q, out, res, err) for q, out, res, err in results if not all(res.values())]
    lines += ["", "**Failures** (answer shown so a human can judge whether the check or the answer was wrong):"]
    for q, out, res, err in fails or []:
        lines.append(f"- #{q['id']} {q['question']!r}: failed {[k for k, v in res.items() if not v]}" + (f" ({err})" if err else "")
                     + f"\n  - answer: {out['answer'][:300]!r}\n  - citations: {[(c['n'], c['title'][:40]) for c in out['citations']]}")
    if not fails:
        lines.append("- none")
    report = "\n".join(lines) + "\n"
    print(report)
    if not a.no_log:
        p = Path(a.log)
        if not p.exists():
            p.write_text("# PHASE6A_EVAL.md: every chat eval run (append only; never overwrite)\n")
        p.write_text(p.read_text() + report)
        print(f"appended to {p}")
    return 0 if len(passed) == n else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
