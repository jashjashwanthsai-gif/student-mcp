-- Run this once in the Supabase SQL Editor (Project > SQL Editor > New query)
-- before the app can store accounts/students/progress in Supabase.

create extension if not exists pgcrypto;

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  created_at timestamptz not null default now()
);

create table if not exists students (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references users(id) on delete cascade,
  name text not null,
  mcp_token text not null unique,
  status text not null default 'created',
  status_message text not null default '',
  bio_text text not null default '',
  profile jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_students_owner on students(owner_id);
create index if not exists idx_students_token on students(mcp_token);

-- Structured, append-only mastery log (one row per update_learning_progress
-- call) - the trend/pattern tracking that the Qdrant vector store is NOT used
-- for. See student_adaptive_mcp/progress.py.
create table if not exists progress_log (
  id uuid primary key default gen_random_uuid(),
  student_id uuid not null references students(id) on delete cascade,
  topic text not null,
  subject text not null default '',
  mastery_level text not null,
  notes text not null default '',
  created_at timestamptz not null default now()
);

create index if not exists idx_progress_log_student on progress_log(student_id);
