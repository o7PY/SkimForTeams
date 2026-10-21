# DIFF.md — deviations from PHASE1.md

(Entries appended as the implementation diverges from the spec.)

## DIFF-001: hosting-teams package is deprecated
- Spec section: §2 D-001
- Spec says: Install `microsoft-agents-hosting-teams`.
- Implementation does: Not installed; the app uses core + aiohttp + msal only.
- Why: The package is deprecated in favour of `-hosting-msteams` and unused.
- Suggested spec update: In D-001 remove `microsoft-agents-hosting-teams` and add `microsoft-agents-hosting-core`; pin 1.9.0.
- Status: open

## DIFF-002: Raw capture happens in the HTTP handler
- Spec section: §6 FR-2
- Spec says: Write every activity to raw_activities before routing 'use the SDK's before-turn hook or equivalent'.
- Implementation does: Stored in the `/api/messages` route handler after JWT validation and before the SDK sees the activity.
- Why: `before_turn` runs after the SDK strips mentions from the text (not unmodified) and never runs for activities the SDK rejects.
- Suggested spec update: FR-2: '...in the HTTP handler, after JWT validation and before SDK processing.'
- Status: open

## DIFF-003: schema_migrations not in 001_init.sql
- Spec section: §5
- Spec says: 001_init.sql creates schema_migrations.
- Implementation does: scripts/migrate.py creates it.
- Why: The runner needs it before applying anything.
- Suggested spec update: Move the schema_migrations DDL from §5 into the description of FR-14.
- Status: open

## DIFF-004: text/html is not an attachments row
- Spec section: §6 FR-3
- Spec says: Plain text and text/html captured; attachments rows created.
- Implementation does: text/html goes to messages.html only; attachments holds all other attachment objects.
- Why: Avoids a redundant row per message.
- Suggested spec update: FR-3: 'text/html attachment content is stored in messages.html, other attachments in attachments.'
- Status: open

## DIFF-005: Thread conversation ids are normalised
- Spec section: §5 Rules
- Spec says: Record whether replyToId or the ';messageid=' suffix is used for reply_to_id.
- Implementation does: conversation_id has the suffix stripped; reply_to_id = replyToId else the suffix root. UNVERIFIED against real Teams payloads.
- Why: Keeps a thread in one conversation. Needs confirmation in the real tenant.
- Suggested spec update: After the manual check, state which field Teams actually provides.
- Status: open

## DIFF-006: Layout: flat repo, spec in Bookkeeping/
- Spec section: §4.1
- Spec says: Repo layout shows a `skim/` root containing PHASE1.md etc.
- Implementation does: Repo root is the project root; PHASE1.md is in Bookkeeping/ (gitignored by the human).
- Why: Matches the existing repo.
- Suggested spec update: §4.1: drop the outer `skim/` folder.
- Status: open

## DIFF-007: LOG_MESSAGE_TEXT added to .env.example
- Spec section: §7
- Spec says: Env list omits LOG_MESSAGE_TEXT (but §8 requires the flag).
- Implementation does: Added `LOG_MESSAGE_TEXT=true` to .env.example.
- Why: §8 requires the flag.
- Suggested spec update: Add `LOG_MESSAGE_TEXT=true` to the §7 env block.
- Status: open

## DIFF-008: Fixtures are hand-written, not captured
- Spec section: §9.1
- Spec says: Realistic JSON activities for every type.
- Implementation does: Fixtures follow the documented Teams shapes but were not captured from a real tenant.
- Why: No tenant access during implementation. Field placement (eventType, replyToId for reactions, file attachment shape in channels) is unverified.
- Suggested spec update: After capture, replace with sanitised real payloads and list differences here.
- Status: open

## DIFF-009: Verification gaps at hand-off
- Spec section: §9 T3, T9, §10
- Spec says: Postgres rows appear in Supabase; docker build + run succeeds.
- Implementation does: Postgres storage is covered only by an optional test (needs TEST_DATABASE_URL) that was NOT run; Docker was not installed, so the image was not built (only `pip install .` + startup in a clean venv were checked).
- Why: No Postgres or Docker on the dev machine.
- Suggested spec update: None; run `scripts/migrate.py`, the integration test, and `docker build` before relying on them.
- Status: open

