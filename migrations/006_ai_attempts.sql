-- Phase 3 fix: failing documents / embeddings count attempts and stop after AI_MAX_ATTEMPTS (P3-DIFF-015). Safe to run twice.
alter table document_texts add column if not exists attempts int not null default 0;

-- status 'error' = an unexpected failure while processing the file (reason = the error class); retried until attempts
-- reaches AI_MAX_ATTEMPTS, or until the file's ctag changes.
create table if not exists embed_failures (
  ref_key      text primary key,          -- the embeddings.ref_key (including the model) that failed
  model        text not null,
  attempts     int  not null default 0,
  error_class  text,
  updated_at   timestamptz not null default now()
);
