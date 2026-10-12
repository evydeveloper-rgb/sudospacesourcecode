-- Sudo community tables (Supabase). Locked: RLS on with no policies and no
-- grants to anon/authenticated -- only the `community` edge function (service
-- role) reads or writes them.
create schema if not exists extensions;
create extension if not exists citext with schema extensions;

create table if not exists public.community_accounts (
  id uuid primary key default gen_random_uuid(),
  username extensions.citext not null unique check (username ~ '^[a-z0-9_]{3,20}$'),
  agent_name text not null check (char_length(agent_name) between 1 and 32),
  city text not null check (char_length(city) between 1 and 80),
  lat double precision,
  lng double precision,
  key_hash text unique,            -- sha256 of the device key; null for seeded accounts
  seed boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.community_posts (
  id uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.community_accounts(id) on delete cascade,
  body text not null check (char_length(body) between 1 and 2000),
  hidden boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists community_posts_created_idx on public.community_posts (created_at desc);

create table if not exists public.community_reports (
  id uuid primary key default gen_random_uuid(),
  post_id uuid not null references public.community_posts(id) on delete cascade,
  reporter_id uuid not null references public.community_accounts(id) on delete cascade,
  created_at timestamptz not null default now(),
  unique (post_id, reporter_id)
);

alter table public.community_accounts enable row level security;
alter table public.community_posts enable row level security;
alter table public.community_reports enable row level security;
revoke all on public.community_accounts, public.community_posts, public.community_reports from anon, authenticated;
