# Skim · Phase 1: Retrieval & Visibility

A Teams bot that stores every message event (raw + normalised) in Postgres. Spec: `Bookkeeping/PHASE1.md`.
Logs of choices: `DECISIONS.md`, `DIFF.md`; plain-language overview: `ELI5.md`.

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
- [ ] Every place real payloads differed from the spec is in `DIFF.md` (replace hand-written fixtures with sanitised real ones).
