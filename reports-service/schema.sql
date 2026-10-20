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

-- The team's reader (Claudia's daily digest, problem_report_digest.py on the
-- VPS): secret-guarded like signups_since, same secret. Devices are the first
-- 8 characters of their key hash.
create or replace function public.problem_reports_since(since timestamptz, secret text)
returns table(id uuid, received_at timestamptz, device text, sudo_version text, report jsonb)
language plpgsql
security definer
set search_path to 'public'
as $function$
begin
  if encode(sha256(convert_to(coalesce(secret, ''), 'UTF8')), 'hex')
       <> '2f16290122c6b2a81f57ad4dd8bc81974f7420345b41e726e81297d980bf5df3' then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  return query
    select r.id, r.received_at, left(r.device_hash, 8), r.sudo_version, r.report
    from public.problem_reports r
    where r.received_at > since
    order by r.received_at asc
    limit 500;
end;
$function$;
revoke all on function public.problem_reports_since(timestamptz, text) from public;
grant execute on function public.problem_reports_since(timestamptz, text) to anon, authenticated;
