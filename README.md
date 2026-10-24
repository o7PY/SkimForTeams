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
`/jobs/refill-empty` · `/jobs/link-attachments` · `/jobs/run-all` (discover, sync-files, backfill of teams that
still need it, refill of empty live messages, attachment linking). Result:
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
