"""Eval replay + scoring (PHASE3 FR-310, §10.1).

The CSV is replayed IN ORDER through the real Pipeline on an isolated in-memory store (nothing real is touched), carrying
state, so later messages resolve against events created by earlier ones. Then each metric is computed against the labels.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import AIConfig
from .pipeline import Pipeline
from .timeparse import IST

CONV = "19:eval-channel@thread.tacv2"
TEAM = "eval-team"
PROF, STUDENT = "eval-prof-aad", "eval-student-aad"
TARGETS = {"category_accuracy": 0.90, "event_f1": 0.85, "type_accuracy": 0.90, "datetime_exact": 0.85, "link_accuracy": 0.85}
ZERO_TARGETS = ("invented_times", "duplicate_events", "student_events")


def load_rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def eval_config(base: AIConfig | None = None) -> AIConfig:
    from dataclasses import replace
    cfg = base or AIConfig(enabled=True, groq_api_key="x", gemini_api_key="x", groq_model="x")
    return replace(cfg, staff_aad_ids=frozenset({PROF}), max_attempts=cfg.max_attempts, max_messages_per_run=1000)


async def replay(rows: list[dict], storage, pipeline: Pipeline, rounds: int = 4, pause=None) -> dict[int, dict]:
    """Insert every message, then process in order. Returns per-message observations taken RIGHT AFTER each message."""
    await storage.upsert_team({"id": TEAM, "aad_group_id": "eval-g", "name": "MA 221", "tenant_id": "eval"})
    await storage.mark_team_installed(TEAM, "EVAL01", PROF, datetime(2026, 10, 1, tzinfo=ZoneInfo("UTC")))
    await storage.upsert_conversation({"id": CONV, "conversation_type": "channel", "team_id": TEAM, "channel_id": CONV, "name": "General"})
    seen: set[str] = set()
    for r in rows:
        if r["msg_id"] in seen:
            continue
        seen.add(r["msg_id"])
        await storage.backfill_message({
            "conversation_id": CONV, "message_id": r["msg_id"], "reply_to_id": None,
            "from_aad_id": PROF if r["author"] == "prof" else STUDENT, "from_name": "Prof" if r["author"] == "prof" else "Student",
            "text": r["text"], "html": None, "importance": None, "mentions": None, "subject": r["subject"] or None,
            "created_at": datetime.strptime(r["posted_at_ist"], "%Y-%m-%d %H:%M").replace(tzinfo=IST),
            "last_edited_at": None, "deleted_at": None}, [], [])
    obs: dict[int, dict] = {}
    for _ in range(rounds):  # failed items (rate limits, outages) are retried, like later scheduler runs would
        pending = await storage.list_pending_messages(pipeline.prompt_version, pipeline.cfg.max_attempts, 1000)
        if not pending:
            break
        for m in pending:
            out = await pipeline.process(m)
            if out.status != "ok":
                continue
            mid = int(m["message_id"])
            items = []
            for link in await storage.list_message_links(CONV, m["message_id"]):
                ev = (await storage.get_event(link["event_id"]))["event"]
                items.append({"event_id": ev["id"], "relation": link["relation"], "type": ev["type"], "title": ev["title"],
                              "date": ev["event_date"].isoformat() if ev["event_date"] else "",
                              "time": ev["starts_at"].astimezone(IST).strftime("%H:%M") if ev["starts_at"] else "",
                              "time_known": ev["time_known"], "time_defaulted": ev["time_defaulted"],
                              "status": ev["status"], "venue": ev["venue"] or ""})
            obs[mid] = {"category": out.category, "items": items, "role": out.role}
        if pause:
            await pause()
    return obs


def _tok(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _expected(r: dict) -> dict:
    s = r["start_or_due_ist"]
    return {"type": r["event_type"], "title": r["event_title"], "date": s[:10], "time": s[11:16] if len(s) > 10 else "",
            "time_known": r["time_known"] == "y", "status": r["status"], "venue": r["venue"], "links_to": r["links_to"],
            "relation": None}


@dataclass
class Report:
    metrics: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        m = self.metrics
        return all(m[k] >= t for k, t in TARGETS.items()) and all(m[k] == 0 for k in ZERO_TARGETS)


def score(rows: list[dict], obs: dict[int, dict]) -> Report:
    rep = Report()
    by_msg: dict[int, list[dict]] = {}
    labels: dict[int, dict] = {}
    for r in rows:
        mid = int(r["msg_id"])
        labels[mid] = r
        if r["category"] in ("event", "change") and r["event_title"]:
            by_msg.setdefault(mid, []).append(r)

    # --- category accuracy (per message)
    cat_ok = 0
    for mid, r in labels.items():
        got = obs.get(mid, {}).get("category")
        cat_ok += got == r["category"]
        if got != r["category"]:
            rep.failures.append(f"msg {mid}: category {got!r}, expected {r['category']!r}")
    rep.metrics["category_accuracy"] = cat_ok / len(labels)

    # --- pair expected rows with produced items
    tp = fp = fn = paired = type_ok = dt_ok = dt_n = link_ok = link_n = invented = duplicates = student_events = 0
    created_by: dict[int, list[dict]] = {}
    for mid, o in obs.items():
        created_by[mid] = [i for i in o["items"] if i["relation"] == "created"]
    for mid, r in sorted(labels.items()):
        produced = list(obs.get(mid, {}).get("items", []))
        if r["author"] == "student":
            student_events += len(produced)
            if produced:
                rep.failures.append(f"msg {mid}: student message touched {len(produced)} event(s)")
        exp_rows = [_expected(x) for x in by_msg.get(mid, [])]
        used: set[int] = set()
        for e in exp_rows:
            best, best_score = None, -1.0
            for k, p in enumerate(produced):
                if k in used:
                    continue
                s = 4 * (p["date"] == e["date"]) + 2 * (p["type"] == e["type"]) + len(_tok(p["title"]) & _tok(e["title"])) / max(1, len(_tok(e["title"]) | _tok(p["title"])))
                if (p["date"] == e["date"] or p["type"] == e["type"]) and s > best_score:
                    best, best_score = k, s
            tag = f"msg {mid} [{e['title']}]"
            if best is None:
                fn += 1
                dt_n += 1
                rep.failures.append(f"{tag}: no event produced")
                if e["links_to"]:
                    link_n += 1
                continue
            used.add(best)
            p = produced[best]
            paired += 1
            strict = p["type"] == e["type"] and p["date"] == e["date"]
            tp += strict
            fn += not strict
            fp += not strict
            type_ok += p["type"] == e["type"]
            if p["type"] != e["type"]:
                rep.failures.append(f"{tag}: type {p['type']!r}, expected {e['type']!r}")
            # date/time exact
            dt_n += 1
            want_time = e["time"] if e["time_known"] else ""
            ok = p["date"] == e["date"] and p["time"] == want_time
            dt_ok += ok
            if not ok:
                rep.failures.append(f"{tag}: when {p['date']} {p['time']!r}, expected {e['date']} {want_time!r}")
            if not e["time_known"] and p["time"] and not p["time_defaulted"]:
                invented += 1
                rep.failures.append(f"{tag}: INVENTED time {p['time']}")
            # linking / duplicates
            if e["links_to"]:
                link_n += 1
                targets = {i["event_id"] for i in created_by.get(int(e["links_to"]), []) if i["type"] == e["type"]}
                good = p["event_id"] in targets
                link_ok += good
                if not good:
                    rep.failures.append(f"{tag}: linked to event {p['event_id']} ({p['relation']}), expected an event created by msg {e['links_to']}")
                if p["relation"] == "created":
                    duplicates += 1
                    rep.failures.append(f"{tag}: DUPLICATE event created instead of linking to msg {e['links_to']}")
            elif p["relation"] != "created":
                rep.failures.append(f"{tag}: merged into an existing event but the key says new (info)")
            if e["status"] and p["status"] != e["status"]:
                rep.failures.append(f"{tag}: status {p['status']!r}, expected {e['status']!r} (info)")
            if e["venue"] != p["venue"]:
                rep.failures.append(f"{tag}: venue {p['venue']!r}, expected {e['venue']!r} (info)")
        extra = len(produced) - len(used)
        fp += extra
        for k, p in enumerate(produced):
            if k not in used and r["author"] != "student":
                rep.failures.append(f"msg {mid}: extra event produced [{p['title']}] {p['date']} (not in the key)")
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    rep.metrics.update(
        event_precision=prec, event_recall=rec, event_f1=(2 * prec * rec / (prec + rec)) if prec + rec else 0.0,
        type_accuracy=type_ok / paired if paired else 0.0, datetime_exact=dt_ok / dt_n if dt_n else 0.0,
        link_accuracy=link_ok / link_n if link_n else 1.0, invented_times=invented, duplicate_events=duplicates,
        student_events=student_events)
    rep.counts = {"messages": len(labels), "expected_event_rows": sum(len(v) for v in by_msg.values()), "paired": paired,
                  "tp": tp, "fp": fp, "fn": fn, "unprocessed_messages": len(labels) - len(obs)}
    return rep


def format_report(rep: Report) -> str:
    m = rep.metrics
    lines = ["| Metric | Result | Target | |", "|---|---|---|---|"]
    for k, t in TARGETS.items():
        lines.append(f"| {k} | {m[k]:.3f} | >= {t} | {'PASS' if m[k] >= t else 'FAIL'} |")
    for k in ZERO_TARGETS:
        lines.append(f"| {k} | {m[k]} | 0 | {'PASS' if m[k] == 0 else 'FAIL'} |")
    lines.append(f"\nprecision {m['event_precision']:.3f}, recall {m['event_recall']:.3f}; counts {rep.counts}")
    return "\n".join(lines)
