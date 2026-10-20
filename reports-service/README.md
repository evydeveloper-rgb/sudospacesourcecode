# Sudo problem reports

Opt-in daily reports from beta devices, so the team hears about a failed
reminder, a crashing agent or a broken update without the owner having to
notice and describe it.

- `schema.sql` — the `problem_reports` table, on Supabase. Locked to everyone
  but the function.
- `index.ts` — the `reports` Supabase Edge Function (deployed with JWT
  verification off: it authenticates with the device's own report key).
- The device side is `raspi-factory/sudo-pi/problem-report.py`, run daily by
  `sudo-problem-report.timer` once the owner turns reports on (Settings →
  Problem reports). The owner sees the exact report there.

**What a report holds.** Versions, the board and its memory, uptime,
temperature and throttling flags, which kinds of key and chat app are set up
(never the keys or numbers), and in the last 24 hours: how many reminders ran,
failed or were not delivered with their error kinds, error-line counts per
part of the agent, and out-of-memory kills. Reminder names, messages,
contacts and files are never collected; error strings are scrubbed of quoted
text, links, email addresses and long numbers.

**Identity.** A random key made on the device when reports are first turned
on (`/etc/sudo/reports.json`, mode 600), sent as `X-Sudo-Report-Key`. The
service stores only its SHA-256. Turning reports off in Settings can also
delete everything the device sent (`POST /forget`). Factory reset removes the key.

**Routes.** `POST /report` (one report, ≤ 32 KB, 4 per device per day),
`POST /forget`.
