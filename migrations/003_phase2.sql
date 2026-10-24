-- Phase 2: Files tab metadata, Graph raw store, sync state, job runs, backfilled messages. Safe to run twice.

alter table teams
  add column if not exists needs_initial_sync boolean not null default true,
  add column if not exists last_discovered_at timestamptz,
  add column if not exists last_files_sync_at timestamptz,
  add column if not exists last_backfill_at  timestamptz;

alter table messages
  add column if not exists source text not null default 'live',     -- live | graph
  add column if not exists subject text,                            -- announcement headline
  add column if not exists backfilled_at timestamptz;

create table if not exists graph_raw (
  id           bigserial primary key,
  fetched_at   timestamptz not null default now(),
  kind         text not null,          -- driveItem | chatMessage | channel | error
  endpoint     text not null,          -- path called, no query string, no tokens
  item_key     text,                   -- driveId:itemId or teamId:channelId:messageId
  dedupe_key   text not null unique,   -- sha256 of canonical payload
  payload      jsonb not null
);
create index if not exists graph_raw_kind_idx on graph_raw (kind, fetched_at desc);

create table if not exists channels (
  id                    text primary key,   -- Graph channel id (= bot channel conversation id, verified by spike)
  team_id               text not null references teams(id),
  display_name          text,
  membership_type       text,               -- standard | private | shared
  files_drive_id        text,
  files_folder_item_id  text,
  files_folder_path     text,               -- optional, informational (P2-DIFF-001)
  discovered_at         timestamptz not null default now(),
  last_synced_at        timestamptz,
  sync_error            text
);
create index if not exists channels_team_idx on channels (team_id);

create table if not exists documents (
  id                 bigserial primary key,
  drive_id           text not null,
  item_id            text not null,
  team_id            text references teams(id),
  channel_id         text references channels(id),
  name               text,
  parent_item_id     text,                 -- parentReference.id; paths come from this chain (P2-D-121)
  path               text,                 -- relative to the channel's Files folder
  is_folder          boolean not null default false,
  size_bytes         bigint,
  mime_type          text,
  web_url            text,
  web_url_norm       text,                 -- normalised web_url for attachment linking (P2-D-122)
  etag               text,
  ctag               text,
  created_at         timestamptz,
  created_by_aad     text,
  created_by_name    text,
  modified_at        timestamptz,
  modified_by_aad    text,
  modified_by_name   text,
  deleted_at         timestamptz,
  first_seen_at      timestamptz not null default now(),
  last_seen_at       timestamptz not null default now(),
  graph_raw_id       bigint references graph_raw(id),
  unique (drive_id, item_id)
);
create index if not exists documents_live_idx on documents (team_id, channel_id) where deleted_at is null;
create index if not exists documents_url_idx on documents (web_url_norm);
create index if not exists documents_parent_idx on documents (drive_id, parent_item_id);

create table if not exists document_events (
  id            bigserial primary key,
  document_id   bigint not null references documents(id),
  event         text not null,   -- created | modified | renamed | moved | deleted | restored
  at            timestamptz not null,
  old_name      text,
  new_name      text,
  old_path      text,
  new_path      text,
  graph_raw_id  bigint references graph_raw(id)
);
create index if not exists document_events_doc_idx on document_events (document_id, at);

create table if not exists sync_state (
  key          text primary key,  -- 'folder:<channelId>' | 'drive:<driveId>' | 'backfill:<channelId>'
  delta_link   text,
  cursor       jsonb,             -- resumable position for partial runs
  updated_at   timestamptz not null default now(),
  last_status  text,
  last_error   text
);

create table if not exists job_runs (
  id           bigserial primary key,
  job          text not null,     -- discover | sync-files | backfill | link-attachments | run-all
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  status       text,              -- ok | partial | busy | error
  stats        jsonb,
  error        text
);
create index if not exists job_runs_job_idx on job_runs (job, started_at desc);

create table if not exists attachment_document_links (
  attachment_id  bigint not null references attachments(id),
  document_id    bigint not null references documents(id),
  match_method   text not null,   -- web_url | name_path (P2-DIFF-003)
  primary key (attachment_id, document_id)
);
