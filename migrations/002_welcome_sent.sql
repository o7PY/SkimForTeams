-- Tracks that the welcome + pairing code was posted for the current install (reset on a fresh install).
alter table teams add column if not exists welcome_sent_at timestamptz;
