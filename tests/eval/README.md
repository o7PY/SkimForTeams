# Eval set (Phase 3)

`eval_messages.csv` is the human-reviewed answer key for the AI pipeline (PHASE3.md §10.1). It is **synthetic**:
a fake MA 221 course, no real people. It is replayed **in order** through the real pipeline by `scripts/run_eval.py`,
carrying state, so later rows resolve against events created by earlier ones.

| Column | Meaning |
|---|---|
| `msg_id` | 1..50, consecutive. A message with several expected events repeats on several rows (same message columns). |
| `posted_at_ist` | Posted time, Asia/Kolkata (`YYYY-MM-DD HH:MM`). Relative dates are resolved from this. Rows are in posted order. |
| `author` | `prof` (staff) or `student`. Students can never create or change events (P3-D-019). |
| `subject`, `text` | The post. |
| `category` | `event`, `change`, `announcement`, `resource`, `question`, `chitchat` (§3.1). |
| `event_type` | §3.2. Empty for non-event rows. |
| `event_title` | Short title of the expected event. Matching to the pipeline's title is fuzzy; the event is identified by msg_id + type + date. |
| `start_or_due_ist` | Expected `YYYY-MM-DD HH:MM` (time known) or `YYYY-MM-DD` (date only). Empty = no date. |
| `time_known` | `y` / `n`. Deadline rows with no stated time carry `23:59` and `y` (the P3-D-011 default). |
| `venue` | Expected venue after this message. |
| `status` | Expected event status after this message (§3.4). |
| `links_to` | msg_id of the message that created the existing event this row refers to (reminders, changes). Empty = a new event. |
| `notes` | Why the answer is what it is. Not scored. |

Rules of thumb used by the scorer: `links_to` is only scored on `event`/`change` rows; on other rows it is ignored.
Unstated time -> `time_known = n` and the pipeline must not invent one (metric "invented times" must be 0).

Scoring notes (decided 2026-10-25):
- 23:59 on a deadline counts as correct whether it was stated or defaulted.
- End times are **not scored** in Phase 3 (e.g. msg 9's 10:45).
- `time_set` / `date_set` rows (msgs 9, 50) have status `scheduled`; a reminder after a reschedule keeps `rescheduled` (msg 29).
- A cancel/reschedule of a recurring item with a clear date creates a new event with that status (msgs 6, 15, 28, 39).
- Weekly office hours (msg 49) are one event for the first new slot; recurrence is future work.
- Venues are only labelled when the message states them or the linked event has them.
- `links_to` on non-event rows is ignored; msg 50 day 2 has none (it is a new event).

## Check it
    python scripts/validate_eval.py        # errors fail; warnings are informational
    pytest tests/test_eval_set.py

The validator checks columns, enums, date formats, that `time_known` agrees with the timestamp, that every weekday named
in a message matches its date, that `links_to` points to an earlier event/change row, and that students have no events.

**Change (2026-10-26, P3-DIFF-017):** msg 50's "Final presentations (day 1)" no longer has `links_to = 27`. A title with an identifier ("day 1", "Quiz 2", "Assignment 4") never merges into a generic event of the same type ("Final presentations", "Quiz", "Assignment"): a new specific event is created and the generic one is flagged `needs_review`.
