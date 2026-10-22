# DIFF.md — deviations from PHASE1.md

(Entries appended as the implementation diverges from the spec.)

## DIFF-001: hosting-teams package is deprecated
- Spec section: §2 D-001
- Spec says: Install `microsoft-agents-hosting-teams`.
- Implementation does: Not installed; the app uses core + aiohttp + msal only.
- Why: The package is deprecated in favour of `-hosting-msteams` and unused.
- Suggested spec update: In D-001 remove `microsoft-agents-hosting-teams` and add `microsoft-agents-hosting-core`; pin 1.9.0.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-002: Raw capture happens in the HTTP handler
- Spec section: §6 FR-2
- Spec says: Write every activity to raw_activities before routing 'use the SDK's before-turn hook or equivalent'.
- Implementation does: Stored in the `/api/messages` route handler after JWT validation and before the SDK sees the activity.
- Why: `before_turn` runs after the SDK strips mentions from the text (not unmodified) and never runs for activities the SDK rejects.
- Suggested spec update: FR-2: '...in the HTTP handler, after JWT validation and before SDK processing.'
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-003: schema_migrations not in 001_init.sql
- Spec section: §5
- Spec says: 001_init.sql creates schema_migrations.
- Implementation does: scripts/migrate.py creates it.
- Why: The runner needs it before applying anything.
- Suggested spec update: Move the schema_migrations DDL from §5 into the description of FR-14.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-004: text/html is not an attachments row
- Spec section: §6 FR-3
- Spec says: Plain text and text/html captured; attachments rows created.
- Implementation does: text/html goes to messages.html only; attachments holds all other attachment objects.
- Why: Avoids a redundant row per message.
- Suggested spec update: FR-3: 'text/html attachment content is stored in messages.html, other attachments in attachments.'
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-005: Thread conversation ids are normalised
- Spec section: §5 Rules
- Spec says: Record whether replyToId or the ';messageid=' suffix is used for reply_to_id.
- Implementation does: conversation_id has the suffix stripped; reply_to_id = replyToId else the suffix root. UNVERIFIED against real Teams payloads.
- Why: Keeps a thread in one conversation. Needs confirmation in the real tenant.
- Suggested spec update: After the manual check, state which field Teams actually provides.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)
- Note: the spec now states the as-built behaviour; real Teams behaviour is still unconfirmed (open item 3 in PHASE1.md).

## DIFF-006: Layout: flat repo, spec in Bookkeeping/
- Spec section: §4.1
- Spec says: Repo layout shows a `skim/` root containing PHASE1.md etc.
- Implementation does: Repo root is the project root; PHASE1.md is in Bookkeeping/ (gitignored by the human).
- Why: Matches the existing repo.
- Suggested spec update: §4.1: drop the outer `skim/` folder.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-007: LOG_MESSAGE_TEXT added to .env.example
- Spec section: §7
- Spec says: Env list omits LOG_MESSAGE_TEXT (but §8 requires the flag).
- Implementation does: Added `LOG_MESSAGE_TEXT=true` to .env.example.
- Why: §8 requires the flag.
- Suggested spec update: Add `LOG_MESSAGE_TEXT=true` to the §7 env block.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

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
- Update 2026-10-22: the Docker image is now built and deployed by Cloud Build (Cloud Run). Still unverified: the Postgres integration test, `/health` `db: true`, and an explicit check that migrations ran (implied by the working DM reply).

## DIFF-010: Welcome triggers and once-per-install tracking
- Spec section: §6 FR-7 (and §5 teams table)
- Spec says: `installationUpdate` add (in a team): upsert `teams` row, compute pairing code, post once in that conversation. No dedupe beyond raw-activity dedupe; `teams` has no welcome tracking column.
- Implementation does: Also triggers on `conversationUpdate` `membersAdded` containing the bot in a team conversation; `add-upgrade` counts as add. Adds `teams.welcome_sent_at` (migration 002) and posts at most once per install; reinstall after removal resets it.
- Why: Human requested (Teams may not send installationUpdate; retries/duplicates must not double-post).
- Suggested spec update: FR-7: "On `installationUpdate` with action starting `add`, or `conversationUpdate` whose `membersAdded` includes the bot (`member.id == recipient.id`) in a team: upsert the `teams` row, compute the pairing code, and post the welcome once per install (tracked by `teams.welcome_sent_at`; a fresh install after removal resets it)." In §5 add `welcome_sent_at timestamptz` to `teams`.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-011: Retried deliveries trigger no reply or welcome; deployment target named
- Spec section: §6 FR-2/7/9, §2 D-004
- Spec says: Duplicates are ignored; D-004 says only 'a serverless container host'.
- Implementation does: A duplicate delivery is stored once and skips normalisation, the welcome and the `Logged ✅` reply. Production runs on Google Cloud Run (asia-south1, service skimforteams) with Cloud Build auto-deploy from `main`, env vars + Secret Manager, Supabase via the transaction pooler.
- Why: Prevents double replies on Teams retries; deployment facts supplied by the human.
- Suggested spec update: Applied: FR-2, FR-9 and the Deployment (as-built) section.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-012: Debug API is absent (404) when DEBUG_TOKEN is unset; limit clamped
- Spec section: §6 FR-11
- Spec says: Disabled entirely if DEBUG_TOKEN is unset; no limit bounds stated.
- Implementation does: Routes are not registered without the token (404); `limit` is clamped to 1-500; unknown message returns 404.
- Why: Smallest attack surface; protects the database from huge reads.
- Suggested spec update: Applied: FR-11.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

## DIFF-013: Manifest details beyond the plan
- Spec section: §6 FR-13
- Spec says: Manifest v1.17+, icons, bot scopes, RSC permissions, webApplicationInfo.
- Implementation does: App version 1.0.0 and `supportsChannelFeatures: tier1` (D-028; the Teams validator required them); `permissions: [identity, messageTeamMembers]`; `webApplicationInfo.resource = https://RscBasedStoreApp`; icons are generated placeholder PNGs; developer/privacy/terms URLs are example.com placeholders; `MICROSOFT_APP_ID` must be a GUID.
- Why: Needed for the validator and sideloading; no art or legal pages exist yet.
- Suggested spec update: Applied: FR-13. Replace the placeholder icons and URLs before any distribution beyond the test tenant.
- Status: applied-to-spec (PHASE1.md as-built, 2026-10-22)

