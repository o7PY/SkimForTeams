# DECISIONS.md — decision log

Never delete entries; supersede instead.

## D-001: Python 3.12 + Microsoft 365 Agents SDK + aiohttp
- Date: 2026-10-21
- Made by: spec
- Decision: Python 3.12, M365 Agents SDK for Python (`microsoft-agents-hosting-aiohttp`, `microsoft-agents-hosting-teams`, `microsoft-agents-authentication-msal`, v1.x), hosted with aiohttp. Import path is `microsoft_agents...`.
- Why: Current official Microsoft SDK for Teams bots; the old Bot Framework SDK is being replaced.
- Alternatives considered: Bot Framework SDK (being replaced).
- Affects: whole project

## D-002: Single-tenant bot via Teams Developer Portal
- Date: 2026-10-21
- Made by: spec
- Decision: Bot registered via the Teams Developer Portal as a single-tenant bot in the human's own M365 tenant.
- Why: New multi-tenant registrations are deprecated; test tenant is ours, no IT approval needed.
- Alternatives considered: multi-tenant (deprecated).
- Affects: manifest, config

## D-003: Resource-Specific Consent (RSC)
- Date: 2026-10-21
- Made by: spec
- Decision: RSC permissions in the manifest: `ChannelMessage.Read.Group`, `ChannelMessage.Send.Group`, `ChannelSettings.Read.Group`, `TeamSettings.Read.Group`, `TeamMember.Read.Group`, `ChatMessage.Read.Chat`.
- Why: Bot receives all channel and group-chat messages without @mention; team owner consents at install, no tenant-admin consent.
- Alternatives considered: tenant-wide Graph permissions (needs admin).
- Affects: scripts/build_manifest.py

## D-004: Container listening on $PORT
- Date: 2026-10-21
- Made by: spec
- Decision: Runtime is a container listening on `$PORT` (default 8080).
- Why: Free serverless container hosts that scale to zero.
- Alternatives considered: always-on server.
- Affects: Dockerfile, config.py

## D-005: Postgres on Supabase with asyncpg
- Date: 2026-10-21
- Made by: spec
- Decision: Storage is Postgres on Supabase (free tier), accessed with asyncpg.
- Why: SQL fits future features; table viewer gives free visibility; pgvector later.
- Alternatives considered: none recorded.
- Affects: storage/postgres.py, migrations

## D-006: Raw-first storage
- Date: 2026-10-21
- Made by: spec
- Decision: Every incoming activity is stored as unmodified JSON before any parsing; normalised tables are derived from it.
- Why: Parsing bugs lose nothing; Phase 1 is about learning what Teams sends.
- Alternatives considered: normalise-only.
- Affects: bot.py, storage

## D-007: Bot is silent in channels/group chats
- Date: 2026-10-21
- Made by: spec
- Decision: Bot only speaks (a) once on install (welcome + pairing code) and (b) in 1:1 personal chat, replying `Logged ✅ (N attachment(s))`.
- Why: Must never spam a class.
- Alternatives considered: none.
- Affects: bot.py

## D-008: Pairing code
- Date: 2026-10-21
- Made by: spec
- Decision: First 6 chars of base32(HMAC-SHA256(PAIRING_SECRET, team_id)), uppercase, stored on `teams` row.
- Why: Deterministic, no extra state, not guessable without secret.
- Alternatives considered: random codes with state (Phase 4).
- Affects: pairing.py

## D-009: No UI in Phase 1
- Date: 2026-10-21
- Made by: spec
- Decision: Visibility = Supabase table viewer + bearer-token JSON debug endpoints.
- Why: Keep Phase 1 small.
- Alternatives considered: none.
- Affects: debug_api.py

## D-010: Idempotency via dedupe_key
- Date: 2026-10-21
- Made by: spec
- Decision: `dedupe_key` = SHA-256 of canonical JSON (sorted keys); duplicate inserts ignored.
- Why: Teams may retry; edits reuse message id so activity id alone isn't unique.
- Alternatives considered: activity id as key.
- Affects: storage, raw_activities

## D-011: Edits create versions; deletes are soft
- Date: 2026-10-21
- Made by: spec
- Decision: `messages` holds latest; `message_versions` holds every version; deletes set `deleted_at`.
- Why: Phase 3 needs "date changed from X to Y".
- Alternatives considered: overwrite.
- Affects: normalize.py, storage

## D-012: Private GitHub repo
- Date: 2026-10-21
- Made by: spec
- Decision: Repo is private.
- Why: Contains a bot that reads class messages.
- Alternatives considered: public.
- Affects: repo hosting

## D-013: Timestamps are UTC timestamptz
- Date: 2026-10-21
- Made by: spec
- Decision: All timestamps `timestamptz` in UTC.
- Why: Avoid timezone bugs; convert to IST only for display.
- Alternatives considered: none.
- Affects: schema, normalize.py

## D-014: Product name
- Date: 2026-10-21
- Made by: spec
- Decision: Working name **Skim**; Teams app display name **"Skim · Course Updates"**.
- Why: Name should make purpose obvious to professors.
- Alternatives considered: none.
- Affects: manifest

## D-015: Flat repo layout; PHASE1.md stays in Bookkeeping/
- Date: 2026-10-21
- Made by: claude-code
- Decision: The repo root is the project root (no extra `skim/` folder). The spec lives in `Bookkeeping/PHASE1.md`, which the human's `.gitignore` excludes, so it is not committed.
- Why: That is how the repo was set up; I did not change the human's ignore rule.
- Alternatives considered: Moving the spec to the root (would override the human's ignore choice).
- Affects: .gitignore, repo layout

## D-016: Dependencies: drop hosting-teams, pin 1.9.0
- Date: 2026-10-21
- Made by: claude-code
- Decision: Depend on `microsoft-agents-hosting-core`, `-hosting-aiohttp`, `-authentication-msal`, `-activity` (all 1.9.0), aiohttp 3.14.5, asyncpg 0.32.0. `microsoft-agents-hosting-teams` is not used.
- Why: It is deprecated (its import warns to use `-msteams`) and Phase 1 needs none of its Teams helpers: we read raw JSON and send plain text. See DIFF-001.
- Alternatives considered: Adding `-hosting-msteams` (unneeded code).
- Affects: pyproject.toml

## D-017: Capture raw in the HTTP handler, before the SDK
- Date: 2026-10-21
- Made by: claude-code
- Decision: `/api/messages` parses the body, stores the raw JSON and normalises it, then hands the request to the SDK; reply handlers read the result from a ContextVar.
- Why: The SDK's `before_turn` hook runs after `_remove_mentions` mutates the activity text, which would break D-006 (unmodified raw). Capturing earlier also stores activities the SDK would reject. See DIFF-002.
- Alternatives considered: `app.before_turn` (text already modified); subclassing `on_turn`.
- Affects: app.py, bot.py

## D-018: Thread messages share the channel's conversation_id
- Date: 2026-10-21
- Made by: claude-code
- Decision: Strip `;messageid=N` from `conversation.id` for `conversations`/`messages`/etc. `reply_to_id` = `replyToId` if present, else the `;messageid=` root when it differs from the message's own id.
- Why: Otherwise every thread would be a separate conversation and (conversation_id, message_id) lookups for replies, edits and reactions would not line up. Real payloads must confirm what Teams sends (acceptance checklist).
- Alternatives considered: Keeping the suffix and joining later.
- Affects: normalize.py; DIFF-005

## D-019: text/html content lives on the message, not in attachments
- Date: 2026-10-21
- Made by: claude-code
- Decision: The `text/html` attachment goes to `messages.html`; `attachments` rows are for everything else.
- Why: Avoids duplicating the body as an 'attachment'. The raw payload still has everything.
- Alternatives considered: Storing both.
- Affects: normalize.py; DIFF-004

## D-020: Failure policy on /api/messages
- Date: 2026-10-21
- Made by: claude-code
- Decision: Storage failure while saving the raw row => 500 (Teams retries, nothing lost). Normalisation failure => logged, raw kept, 202. SDK/turn errors, unknown types => logged, 202. Invalid JSON / non-object body => 400.
- Why: FR-15 forbids 5xx for unknown/malformed activities, but a DB outage is different: failing loudly lets Teams redeliver.
- Alternatives considered: Always 202 (would silently drop data during a DB outage).
- Affects: app.py, bot.py

## D-021: Bot is truly silent: typing indicator off, errors swallowed
- Date: 2026-10-21
- Made by: claude-code
- Decision: `start_typing_timer=False` and an `app.error` handler that logs and never replies.
- Why: The SDK would otherwise send typing indicators and re-raise turn errors; D-007 says never speak except for FR-7/FR-9.
- Alternatives considered: Defaults.
- Affects: bot.py

## D-022: schema_migrations is created by migrate.py
- Date: 2026-10-21
- Made by: claude-code
- Decision: `scripts/migrate.py` creates `schema_migrations` itself; `001_init.sql` contains only the domain tables.
- Why: The runner must have the table before it can know what to apply. See DIFF-003.
- Alternatives considered: Keeping it in 001 (chicken-and-egg).
- Affects: migrations/001_init.sql, scripts/migrate.py

## D-023: JWT check scoped to /api/messages
- Date: 2026-10-21
- Made by: claude-code
- Decision: Use `jwt_authorization_decorator` on that route only instead of app-wide middleware.
- Why: /health must be unauthenticated and /debug uses its own bearer token (FR-10, FR-11).
- Alternatives considered: App-wide middleware (would block /health).
- Affects: app.py

## D-024: Welcome only for team installs; pairing code only on install
- Date: 2026-10-21
- Made by: claude-code
- Decision: `installationUpdate` add without `channelData.team` (personal or group-chat installs) stores raw and sends nothing. The code is computed when the install is processed.
- Why: FR-7 says 'in a team'. Code is deterministic so it can be recomputed any time.
- Alternatives considered: Welcoming group chats too (no team to pair).
- Affects: bot.py

## D-025: Edit/delete for an unseen message
- Date: 2026-10-21
- Made by: claude-code
- Decision: Create the `messages` row from that event at version 1 with the event name as given (`edited`/`deleted`/`undeleted`) and log a warning with the raw id.
- Why: Spec rule in §5.
- Alternatives considered: n/a
- Affects: storage/*.py, bot.py

## D-026: Supabase pooler detection
- Date: 2026-10-21
- Made by: claude-code
- Decision: `statement_cache_size=0` whenever the DSN contains `:6543`. `migrate.py` always uses 0.
- Why: Transaction-mode pooler cannot use prepared statements.
- Alternatives considered: A config flag.
- Affects: storage/postgres.py

## D-027: Tooling
- Date: 2026-10-21
- Made by: claude-code
- Decision: pyproject.toml + setuptools, src layout, venv via `uv`, pytest-asyncio in auto mode, hand-rolled PNG icons for the manifest (no Pillow), placeholder developer URLs (example.com) in the manifest.
- Why: Fewest dependencies; placeholders are fine for sideloading in our own tenant.
- Alternatives considered: requirements.txt; Pillow.
- Affects: pyproject.toml, scripts/build_manifest.py

