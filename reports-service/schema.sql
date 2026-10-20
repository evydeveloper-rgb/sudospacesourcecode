-- Sudo problem reports (Supabase). Opt-in daily reports from beta devices:
-- versions, counts and error kinds, never the owner's messages or names (see
-- raspi-factory/sudo-pi/problem-report.py). Locked: RLS on with no policies and
-- no grants to anon/authenticated -- only the `reports` edge function (service
-- role) writes, and the team reads with the service role.
create table if not exists public.problem_reports (
  id uuid primary key default gen_random_uuid(),
  device_hash text not null check (char_length(device_hash) = 64),  -- sha256 of the device's report key
  received_at timestamptz not null default now(),
  sudo_version text check (char_length(sudo_version) <= 20),
  report jsonb not null check (pg_column_size(report) <= 32768)
);
create index if not exists problem_reports_device_idx on public.problem_reports (device_hash, received_at desc);
create index if not exists problem_reports_received_idx on public.problem_reports (received_at desc);

alter table public.problem_reports enable row level security;
revoke all on public.problem_reports from anon, authenticated;
