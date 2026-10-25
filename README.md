# Skim · Phase 1: Retrieval & Visibility, Phase 2: Files tab + message history

A Teams bot that stores every message event (raw + normalised) in Postgres. Spec: `Bookkeeping/PHASE1.md`.
Decision log, spec deviations and the plain-language overview live in `Bookkeeping/` (`DECISIONS.md`, `DIFF.md`, `ELI5.md`), which is git-ignored and kept locally.

## Run locally
```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
cp .env.example .env   # fill it in; then export it:  set -a; . ./.env; set +a
python -m skim         # listens on $PORT (default 8080); GET /health
```
Quick start without Teams or a database:
```bash
STORAGE=memory PAIRING_SECRET=dev DEBUG_TOKEN=dev \
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__ANONYMOUS_ALLOWED=true python -m skim
curl -X POST localhost:8080/api/messages -H 'content-type: application/json' -d @tests/fixtures/channel_post.json
curl -H 'Authorization: Bearer dev' localhost:8080/debug/messages
```
With the Microsoft 365 Agents Playground, point it at `http://localhost:8080/api/messages` in anonymous mode
(`ANONYMOUS_ALLOWED=true`; never use that in production).

## Tests
```bash
pytest                                   # unit + app tests (memory storage)
TEST_DATABASE_URL=postgres://... pytest  # also runs the Postgres integration test; it DROPS the public schema, use a throwaway DB
```

## Migrations
```bash
DATABASE_URL=postgres://... python scripts/migrate.py   # idempotent; Supabase direct (5432) or pooler (6543) both work
```

## Teams app package
```bash
MICROSOFT_APP_ID=<bot app id> python scripts/build_manifest.py   # writes appPackage.zip; upload it in Teams
```
Developer/privacy URLs in the manifest are placeholders (example.com).

## Container
```bash
docker build -t skim . && docker run --rm -p 8080:8080 --env-file .env skim
```

## Debug API (needs `Authorization: Bearer $DEBUG_TOKEN`; 403 otherwise; disabled if `DEBUG_TOKEN` unset)
`GET /debug/raw?limit=&type=` · `/debug/messages?limit=&team_id=&conversation_id=` ·
`/debug/messages/{conversation_id}/{message_id}` · `/debug/teams`

## Phase 2: Files metadata and message history (Microsoft Graph, app-only)
Spec: `Bookkeeping/PHASE2.md`. Metadata only: file bytes and download URLs are never requested or stored. Only
standard channels are synced; private and shared channels are skipped entirely.

Extra environment (all optional; without Graph credentials or `JOBS_TOKEN` the `/jobs/*` paths are plain 404s):

| Variable | Default | Meaning |
|---|---|---|
| `JOBS_TOKEN` | unset | Bearer token for `POST /jobs/*` (use Secret Manager) |
| `JOB_TIME_BUDGET_SECONDS` | 240 | One call stops cleanly after this and returns `partial`; call again to resume |
| `GRAPH_MAX_CONCURRENCY` | 4 | Parallel Graph requests |
| `BACKFILL_SINCE_DAYS` | 180 | How far back message history is read |
| `BACKFILL_MAX_MESSAGES_PER_CHANNEL` | 2000 | Cap per channel |
| `GRAPH_BASE_URL` | Graph v1.0 | Tests only |

Graph needs the bot's app registration to have the application roles `Channel.ReadBasic.All`, `Files.Read.All`,
`Group.Selected`, `Team.ReadBasic.All` (admin consent), and the team to be granted via RSC.

**Jobs** (`Authorization: Bearer $JOBS_TOKEN`, JSON in, JSON out; one at a time, a second caller gets `busy`):
`POST /jobs/discover` · `/jobs/sync-files` · `/jobs/backfill?team_id=&since_days=&force=true` ·
`/jobs/enrich-messages` · `/jobs/fix-text` · `/jobs/refill-empty` · `/jobs/link-attachments` · `/jobs/run-all` (discover, sync-files,
backfill of teams that still need it, enrichment of live messages from the Graph message delta feed, refill of empty
live messages, plain-text clean-up, attachment linking). Live Teams events never carry a post's subject; only Graph does, so a subject
appears after the next enrich run. Result:
`{"status":"ok|partial|busy|error","job_run_id":…,"stats":{…},"next":true|false}`; `partial` means "call again".
Errors come back as JSON with `error_class` (HTTP 500) and a failed row in `job_runs`. `force=true` on backfill drops
saved cursors and starts over; it never duplicates rows.
`/health` also reports `db_schema`: `ok`, `behind` (run `scripts/migrate.py`), `unknown` or `n/a`.

Cloud Scheduler: `POST https://<service-url>/jobs/run-all`, header `Authorization: Bearer <JOBS_TOKEN>`, schedule
`*/15 * * * *` (any), attempt deadline 300 s (above `JOB_TIME_BUDGET_SECONDS`), retries off; a `partial` result is
simply finished by the next tick.

More debug endpoints: `/debug/channels` · `/debug/documents?channel_id=&include_deleted=` ·
`/debug/documents/{id}` (with its events and linked messages) · `/debug/sync-status` (Graph links are redacted).

Scripts:
```bash
BASE_URL=https://<service> DEBUG_TOKEN=… [JOBS_TOKEN=…] python scripts/smoke_remote.py [--jobs]   # --jobs runs run-all twice (real changes)
python scripts/spike_phase2.py [--team-id <aad_group_id>]   # read-only Graph probe with sanitised output
```

## Phase 3: AI understanding (classify messages, extract events, search)
Spec: `Bookkeeping/PHASE3.md`. Each new channel message goes through: **redact** (names, emails, phones, roll numbers become
placeholders) → **classify + extract** (Gemini first, Groq as fallback; LLM output is untrusted data, schema-validated, never
executed) → **dates resolved in code** (`timeparse.py`, IST, never guessed) → **match** against open events → saved in ONE
transaction with a full `event_changes` history. Students never create or change events. Files (pdf/docx/pptx) are read too.

Environment (Phase 3 switches itself off with ONE log line if a key or `GROQ_MODEL` is missing; `AI_ENABLED=false` makes
`run-all` behave exactly like Phase 2). Put real values in Secret Manager / your git-ignored `.env`, never in git.

| Variable | Default | Meaning |
|---|---|---|
| `AI_ENABLED` | true | Kill switch |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | `gemini-3.5-flash-lite` | **Primary** provider; also embeddings. Must be a model your key can call (the eval was run with this one) |
| `GROQ_API_KEY`, `GROQ_MODEL` | none (required) | **Secondary** provider, e.g. `openai/gpt-oss-120b` |
| `NIM_API_KEY`, `NIM_MODEL` | unset | Optional third provider (NVIDIA NIM), used only if both are set |
| `EMBED_MODEL`, `EMBED_DIMS` | `gemini-embedding-001`, 768 | Dimensions must stay 768 (the column is `vector(768)`) |
| `PROMPT_VERSION` | `p3-v2` | Bump whenever a prompt changes; messages are re-analysed under the new version |
| `STAFF_AAD_IDS` | empty | Comma list of staff AAD ids (the person who installed the bot always counts). **Set this**: with it, everyone else is a student and can never change events |
| `AI_MAX_MESSAGES_PER_RUN`, `AI_MAX_DOCS_PER_RUN`, `AI_MAX_EMBEDS_PER_RUN` | 200 / 10 / 500 | Per-run limits |
| `AI_MAX_ATTEMPTS` | 3 | A failing message is retried this many times |
| `AI_MIN_CALL_INTERVAL` | 2 | Seconds between calls to one provider (free-tier rate limits) |
| `DOC_MAX_BYTES`, `DOC_MAX_PAGES`, `DOC_MAX_CHARS` | 20 MB / 50 / 150000 | File limits; over a limit the file is skipped with a reason |
| `COURSE_TIMEZONE` | `Asia/Kolkata` | Used for relative dates and the 23:59 deadline default |

Migration `005_phase3.sql` (needs the pgvector extension) must be applied **before** deploying: `python scripts/migrate.py`.

**Jobs** (same token, lock and time budget as Phase 2; all are part of `run-all` when AI is on):
`POST /jobs/ai-process` (messages → events) · `/jobs/ai-documents` (files → text, chunks, events) · `/jobs/ai-embed`
(vectors for search) · `/jobs/ai-reprocess?prompt_version=p3-v9` (re-analyse messages under another prompt version; old
analyses are kept and events are NOT touched; the live version is rejected: bump `PROMPT_VERSION` instead).
A provider outage or rate limit never fails a run: the work stays pending and is retried.

**To re-process after a prompt change:** edit `src/skim/ai/prompts/*.md`, bump `PROMPT_VERSION` (env), deploy; the next
`run-all` analyses every message again under the new version. To compare first, run the eval (below).

Debug endpoints (`DEBUG_TOKEN`): `/debug/events?team_id=&status=&type=&needs_review=` · `/debug/events/{id}` (the event, its
whole change history and linked messages) · `/debug/message-ai/{conversation_id}/{message_id}` · `/debug/search?q=&team_id=&k=`
(semantic search; the query is redacted first) · `/debug/ai-status` (models, failures by class, pending counts, embeddings).
Logs and `job_runs.stats` carry numbers and error classes only: never message text, names, prompts or keys.

**Eval** (50 labelled messages in `tests/eval/`; synthetic, free-tier calls):
```bash
set -a; . ./.env; set +a
python scripts/run_eval.py --dry                       # recorded perfect model: tests the code, no network
python scripts/run_eval.py --providers gemini,groq --note "what changed"   # real models; appends to Bookkeeping/PHASE3_EVAL.md
python scripts/eval_search.py                          # real embeddings: hit@3 on 10 questions
python scripts/eval_nim.py --probe                     # optional: which NVIDIA NIM models work for your key
python scripts/validate_eval.py                        # checks the answer key itself
python scripts/capture_fixtures.py --dry               # sanitised real payload shapes -> tests/fixtures/real/ (read-only DB)
```
Targets (spec §10.1): category ≥ 90%, event F1 ≥ 0.85, type ≥ 90%, date/time ≥ 85%, linking ≥ 85%, and ZERO invented times,
duplicate events, or events from student messages.

### Phase 3 manual acceptance checklist (real test tenant)
- [ ] Post ~10 realistic messages as the professor; after one `run-all`, `/debug/events` shows the right events with IST dates and times.
- [ ] Post a correction ("moved to …"): the SAME event becomes `rescheduled`, with an `event_changes` row old → new.
- [ ] Post a reminder: no duplicate event; a `restated` link exists.
- [ ] Post as a student "Is the quiz moved to Monday?": no event changes.
- [ ] Upload a PDF with a deadline: `document_texts` is ok, chunks exist, an event appears with `source_kind = document`.
- [ ] `/debug/search?q=when is the midsem` returns the midsem messages first.
- [ ] Edit a message's date: the event updates and the history shows the edit time.
- [ ] `AI_ENABLED=false` → `run-all` works exactly like Phase 2.
- [ ] `job_runs.stats` has AI counts and no text or names anywhere in the logs.
- [ ] Scheduler runs keep succeeding for a day; `/debug/ai-status` shows no pile-up of failures.

## Manual acceptance checklist (real test tenant)
- [ ] Adding the bot to a team posts the welcome + pairing code exactly once (even if Teams sends both installationUpdate and conversationUpdate); a `teams` row has that code. Remove + re-add welcomes again.
- [ ] A channel post **without @mention** appears in `messages` within ~5 s.
- [ ] A thread reply appears with `reply_to_id` set (and in the same `conversation_id` as the channel).
- [ ] Editing a post twice → `current_version = 3`, three `message_versions` rows, old text preserved.
- [ ] Deleting it → `deleted_at` set, versions preserved.
- [ ] A 👍 reaction → `reactions` row.
- [ ] Uploading a PDF in the channel → `attachments` row(s).
- [ ] A link post (e.g. calendar link) → stored in `text`/`html`.
- [ ] Bot added to a group chat → its messages are stored.
- [ ] DMing the bot → it replies `Logged ✅ …`.
- [ ] Removing the bot → `removed_at` set.
- [ ] `/debug/*` returns the above with the token and 403 without.
- [ ] The bot posted nothing to the channel except the one welcome message.
- [ ] Every place real payloads differed from the spec is in `Bookkeeping/DIFF.md` (replace hand-written fixtures with sanitised real ones).
