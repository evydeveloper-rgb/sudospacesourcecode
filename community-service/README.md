# Sudo community service

The one online part of the Sudo dashboard: posts, and a map of where sudos
are (by the city each person types — never a device location).

- `schema.sql` — the tables, on Supabase. Locked to everyone but the function.
- `index.ts` — the `community` Supabase Edge Function (deployed with JWT
  verification off: it authenticates with the Sudo device key itself).

**Sign-in.** A sudo makes a random key when it joins (`/etc/sudo/community.json`,
mode 600) and sends it as `X-Sudo-Device-Key`. The service stores only its
SHA-256. No passwords, no emails. The dashboard proxies every call
(`/api/community/*` in `server.py`), so the browser never sees the key.
Factory reset removes the key file.

**Routes.** Public: `GET /available?u=`. With a device key: `POST /join`,
`GET /me`, `GET /feed`, `POST /posts`, `POST /posts/delete`, `POST /report`
(three reports hide a post), `GET /map`, `POST /leave` (removes the account
and its posts).

The world outline the map draws on ships with the dashboard
(`sudo-dashboard/world.svg`, Natural Earth, public domain).
