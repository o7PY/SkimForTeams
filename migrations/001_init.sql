create table if not exists raw_activities (
  id               bigserial primary key,
  received_at      timestamptz not null default now(),
  dedupe_key       text not null unique,
  activity_type    text not null,
  activity_id      text,
  event_type       text,
  conversation_id  text,
  tenant_id        text,
  payload          jsonb not null
);
create index if not exists raw_activities_received_idx on raw_activities (received_at desc);
create index if not exists raw_activities_conv_idx on raw_activities (conversation_id, received_at desc);

create table if not exists teams (
  id               text primary key,
  aad_group_id     text,
  name             text,
  tenant_id        text,
  pairing_code     text unique,
  installed_at     timestamptz,
  removed_at       timestamptz,
  installed_by_aad text
);

create table if not exists conversations (
  id                text primary key,
  conversation_type text,
  team_id           text references teams(id),
  channel_id        text,
  name              text,
  tenant_id         text,
  first_seen_at     timestamptz not null default now()
);

create table if not exists messages (
  conversation_id  text not null,
  message_id       text not null,
  reply_to_id      text,
  from_aad_id      text,
  from_name        text,
  text             text,
  html             text,
  importance       text,
  mentions         jsonb,
  created_at       timestamptz,
  last_edited_at   timestamptz,
  deleted_at       timestamptz,
  current_version  int not null default 1,
  raw_activity_id  bigint references raw_activities(id),
  primary key (conversation_id, message_id)
);
create index if not exists messages_conv_created_idx on messages (conversation_id, created_at desc);

create table if not exists message_versions (
  conversation_id  text not null,
  message_id       text not null,
  version          int  not null,
  text             text,
  html             text,
  event            text not null,
  at               timestamptz not null,
  raw_activity_id  bigint references raw_activities(id),
  primary key (conversation_id, message_id, version)
);

create table if not exists attachments (
  id               bigserial primary key,
  conversation_id  text not null,
  message_id       text not null,
  content_type     text,
  name             text,
  content_url      text,
  payload          jsonb not null,
  raw_activity_id  bigint references raw_activities(id)
);
create index if not exists attachments_msg_idx on attachments (conversation_id, message_id);

create table if not exists reactions (
  id               bigserial primary key,
  conversation_id  text not null,
  message_id       text not null,
  user_aad_id      text,
  user_name        text,
  reaction_type    text,
  action           text not null,
  at               timestamptz not null,
  raw_activity_id  bigint references raw_activities(id)
);
create index if not exists reactions_msg_idx on reactions (conversation_id, message_id);
