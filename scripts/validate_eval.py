"""Validate tests/eval/eval_messages.csv (PHASE3.md T2). Pure; also imported by tests/test_eval_set.py.

    python scripts/validate_eval.py [path]      # exit 1 on errors; warnings are printed but do not fail
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import date, datetime
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "tests" / "eval" / "eval_messages.csv"
COLUMNS = ["msg_id", "posted_at_ist", "author", "subject", "text", "category", "event_type", "event_title",
           "start_or_due_ist", "time_known", "venue", "status", "links_to", "notes"]
CATEGORIES = {"event", "change", "announcement", "resource", "question", "chitchat"}
TYPES = {"quiz", "exam", "assignment", "project", "lab", "class", "office_hours", "presentation", "other"}
STATUSES = {"scheduled", "rescheduled", "cancelled", "done"}
AUTHORS = {"prof", "student"}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?$")
WEEKDAY_DATE = re.compile(r"\b(" + "|".join(DAYS) + r"),?\s+(\d{1,2})\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", re.I)


def load(path: Path = DEFAULT) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def validate(rows: list[dict]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warns: list[str] = []
    if rows and list(rows[0].keys()) != COLUMNS:
        errors.append(f"columns differ: {list(rows[0].keys())}")
        return errors, warns
    ids_seen: dict[int, dict] = {}
    last_posted = None
    for n, r in enumerate(rows, 2):  # line number in the file
        where = f"line {n} (msg {r['msg_id']})"
        try:
            mid = int(r["msg_id"])
        except ValueError:
            errors.append(f"{where}: msg_id not an int")
            continue
        m = STAMP.match(r["posted_at_ist"])
        if not m or not m.group(2):
            errors.append(f"{where}: posted_at_ist must be 'YYYY-MM-DD HH:MM'")
            continue
        posted = datetime.strptime(r["posted_at_ist"], "%Y-%m-%d %H:%M")
        if last_posted and posted < last_posted:
            errors.append(f"{where}: rows are not in posted order")
        last_posted = posted
        first_row = mid not in ids_seen
        if mid in ids_seen:
            for col in ("posted_at_ist", "author", "subject", "text", "category"):
                if ids_seen[mid][col] != r[col]:
                    errors.append(f"{where}: repeated msg_id but {col} differs")
        else:
            if ids_seen and mid != max(ids_seen) + 1:
                errors.append(f"{where}: msg_id not consecutive")
            ids_seen[mid] = r
        if r["author"] not in AUTHORS:
            errors.append(f"{where}: author {r['author']!r}")
        if r["category"] not in CATEGORIES:
            errors.append(f"{where}: category {r['category']!r}")
        if not r["text"].strip():
            errors.append(f"{where}: empty text")
        has_event = r["category"] in ("event", "change")
        event_cols = ("event_type", "event_title", "start_or_due_ist", "time_known", "venue", "status", "links_to")
        if not has_event:
            for c in event_cols:
                if r[c] and not (c == "links_to"):
                    errors.append(f"{where}: {c} set on a {r['category']} row")
            if r["links_to"]:
                warns.append(f"{where}: links_to on a {r['category']} row (ignored in scoring; see README)")
            continue
        if r["author"] == "student":
            errors.append(f"{where}: student row with events (P3-D-019: students cannot create or change events)")
        if r["event_type"] not in TYPES:
            errors.append(f"{where}: event_type {r['event_type']!r}")
        if not r["event_title"]:
            errors.append(f"{where}: event_title missing")
        if r["status"] not in STATUSES:
            errors.append(f"{where}: status {r['status']!r}")
        if r["time_known"] not in ("y", "n"):
            errors.append(f"{where}: time_known must be y or n")
        s = STAMP.match(r["start_or_due_ist"]) if r["start_or_due_ist"] else None
        if r["start_or_due_ist"] and not s:
            errors.append(f"{where}: bad start_or_due_ist {r['start_or_due_ist']!r}")
        if s:
            if r["time_known"] == "y" and not s.group(2):
                errors.append(f"{where}: time_known=y but no time")
            if r["time_known"] == "n" and s.group(2):
                errors.append(f"{where}: time_known=n but a time is given")
        elif r["time_known"] == "y":
            errors.append(f"{where}: time_known=y but no date")
        if r["links_to"]:
            for part in r["links_to"].split(","):
                try:
                    t = int(part)
                except ValueError:
                    errors.append(f"{where}: links_to {part!r} not an int")
                    continue
                if t not in ids_seen or t >= mid:
                    errors.append(f"{where}: links_to {t} must be an earlier msg_id")
                elif ids_seen[t]["category"] not in ("event", "change"):
                    errors.append(f"{where}: links_to {t} is not an event/change row")
        # Weekday + date written in the message text must agree with the calendar.
        for wd, d, mon, yr in WEEKDAY_DATE.findall(r["text"]):
            year = int(yr) if yr else posted.year
            try:
                real = date(year, MONTHS[mon.lower()], int(d))
            except ValueError:
                errors.append(f"{where}: impossible date in text {wd} {d} {mon}")
                continue
            if DAYS[real.weekday()] != wd.lower():
                errors.append(f"{where}: text says {wd} {d} {mon} but that is a {DAYS[real.weekday()]}")
        if s and r["status"] != "cancelled" and not r["links_to"] and s.group(1) < r["posted_at_ist"][:10]:
            errors.append(f"{where}: event date {s.group(1)} is before the post date")
        if not first_row and r["links_to"] == "" and False:
            pass
    # A reminder/restatement pair must not produce a second *new* event with the same type+title.
    seen: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["category"] in ("event", "change") and r["event_title"] and not r["links_to"]:
            key = (r["event_type"], r["event_title"].lower())
            if key in seen and seen[key] != int(r["msg_id"]):
                warns.append(f"msg {r['msg_id']}: creates {key} again (first in msg {seen[key]}) with no links_to")
            seen.setdefault(key, int(r["msg_id"]))
    return errors, warns


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    rows = load(path)
    errors, warns = validate(rows)
    print(f"{len(rows)} rows, {len({r['msg_id'] for r in rows})} messages")
    for w in warns:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
