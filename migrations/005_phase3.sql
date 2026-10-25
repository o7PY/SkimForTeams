-- Phase 3: AI understanding (message analysis, events, change history, document text, embeddings). Safe to run twice.
-- Needs the pgvector extension (already enabled in Supabase).
create extension if not exists vector;

create table if not exists message_analysis (
  conversation_id  text not null,
  message_id       text not null,
  message_version  int  not null,
  prompt_version   text not null,
  status           text not null,          -- ok | failed | skipped
  category         text,                   -- PHASE3 §3.1
  confidence       text,                   -- high | medium | low
  provider         text,
  model            text,
  output           jsonb,                  -- validated LLM output (redacted form, no real names)
  author_role      text,                   -- staff | student | unknown (P3-D-019)
  needs_review     boolean not null default false,
  error_class      text,
  attempts         int  not null default 0,
  processed_at     timestamptz not null default now(),
  primary key (conversation_id, message_id, message_version, prompt_version)
);

create table if not exists events (
  id               bigserial primary key,
  team_id          text not null references teams(id),
  type             text not null,
  title            text not null,
  is_deadline      boolean not null default false,
  starts_at        timestamptz,
  ends_at          timestamptz,
  event_date       date,                   -- IST date, set whenever a date is known
  time_known       boolean not null default false,
  time_defaulted   boolean not null default false,
  venue            text,
  details          text,
  status           text not null default 'scheduled',
  confidence       text not null,
  needs_review     boolean not null default false,
  source_kind      text not null,          -- message | document
  source_ref       jsonb not null,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);
create index if not exists events_team_status_idx on events (team_id, status);
create index if not exists events_team_type_idx on events (team_id, type);

create table if not exists event_changes (
  id               bigserial primary key,
  event_id         bigint not null references events(id),
  change_type      text not null,
  old_values       jsonb,
  new_values       jsonb,
  confidence       text not null,
  source_kind      text not null,
  source_ref       jsonb not null,
  at               timestamptz not null,
  recorded_at      timestamptz not null default now()
);
create index if not exists event_changes_event_idx on event_changes (event_id, at);

create table if not exists message_event_links (
  conversation_id  text not null,
  message_id       text not null,
  event_id         bigint not null references events(id),
  relation         text not null,          -- created | changed | restated
  primary key (conversation_id, message_id, event_id)
);

create table if not exists document_texts (
  document_id      bigint primary key references documents(id),
  ctag             text,
  status           text not null,          -- ok | skipped | failed
  reason           text,
  page_count       int,
  char_count       int,
  extracted_at     timestamptz not null default now()
);

create table if not exists doc_chunks (
  id               bigserial primary key,
  document_id      bigint not null references documents(id),
  ctag             text,
  chunk_index      int not null,
  page_from        int,
  page_to          int,
  text             text not null,
  unique (document_id, ctag, chunk_index)
);

create table if not exists embeddings (
  id               bigserial primary key,
  kind             text not null,          -- message | doc_chunk
  ref              jsonb not null,
  ref_key          text not null unique,
  model            text not null,
  dims             int  not null,
  embedding        vector(768) not null,
  created_at       timestamptz not null default now()
);
create index if not exists embeddings_hnsw_idx on embeddings using hnsw (embedding vector_cosine_ops);
