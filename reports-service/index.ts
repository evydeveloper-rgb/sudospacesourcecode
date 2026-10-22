// Sudo problem reports: the daily report from beta devices, and "Report this".
//
// A sudo makes a random key the first time reports are turned on and keeps it
// on the device (/etc/sudo/reports.json); this function stores only its
// SHA-256, so reports from one device can be told apart without knowing whose
// it is. No accounts, no emails. The tables are locked to everyone else (RLS
// on, no policies); only this function, with the service role, writes.
//
//   POST /report    the daily report: counts, never conversation text
//                   (JSON, at most 32 KB; 4 per device per day)
//   POST /feedback  "Report this": one exchange the owner chose to send, with
//                   their note (at most 32 KB; 20 per device per day)
//   POST /forget    delete everything this device has sent
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const db = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  { auth: { persistSession: false } },
);

const MAX_BYTES = 32 * 1024;
const PER_DAY = 4;
const FEEDBACK_PER_DAY = 20;

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
}

async function sha256(text: string) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function reportKey(req: Request) {
  const key = req.headers.get("x-sudo-report-key") || "";
  return key.length >= 32 && key.length <= 128 ? key : "";
}

async function countToday(table: string, device: string) {
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const { count } = await db.from(table).select("id", { count: "exact", head: true })
    .eq("device_hash", device).gte("received_at", since);
  return count ?? 0;
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  const route = new URL(req.url).pathname.replace(/^.*\/reports/, "") || "/";
  const key = reportKey(req);
  if (!key) return json({ error: "Missing device key" }, 401);
  const device = await sha256(key);

  if (route === "/forget") {
    const a = await db.from("problem_reports").delete({ count: "exact" }).eq("device_hash", device);
    const b = await db.from("problem_feedback").delete({ count: "exact" }).eq("device_hash", device);
    if (a.error || b.error) return json({ error: "Could not delete" }, 500);
    return json({ ok: true, deleted: (a.count ?? 0) + (b.count ?? 0) });
  }

  if (route !== "/report" && route !== "/feedback") return json({ error: "Not found" }, 404);
  const raw = await req.text();
  if (raw.length > MAX_BYTES) return json({ error: "Too large" }, 413);
  let body: Record<string, unknown>;
  try {
    body = JSON.parse(raw);
  } catch {
    return json({ error: "Not JSON" }, 400);
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) return json({ error: "Not an object" }, 400);
  const version = typeof body.sudo === "string" ? body.sudo.slice(0, 20) : null;

  if (route === "/feedback") {
    if (!body.exchange || typeof body.exchange !== "object") return json({ error: "Nothing to send" }, 400);
    if (await countToday("problem_feedback", device) >= FEEDBACK_PER_DAY) {
      return json({ error: "Enough reports from this device today" }, 429);
    }
    const { error } = await db.from("problem_feedback").insert({
      device_hash: device, sudo_version: version,
      source: typeof body.source === "string" ? body.source.slice(0, 20) : null,
      note: typeof body.note === "string" ? body.note.slice(0, 2000) : null,
      exchange: body.exchange,
    });
    if (error) return json({ error: "Could not save it" }, 500);
    return json({ ok: true });
  }

  if (body.schema !== 1 && body.schema !== 2) return json({ error: "Not a report" }, 400);
  if (await countToday("problem_reports", device) >= PER_DAY) {
    return json({ error: "Enough reports from this device today" }, 429);
  }
  // Defence in depth: the device scrubs before sending, but a reminder's name
  // is the owner's own words and OpenClaw writes it unquoted
  // ("Cron: <name> failed"), which older devices let through.
  const report = JSON.parse(JSON.stringify(body).replace(/(Cron(?: job)?:?)\s+[^"\\]*?\s+failed/g, "$1 <reminder> failed"));
  const { error } = await db.from("problem_reports").insert({ device_hash: device, sudo_version: version, report });
  if (error) return json({ error: "Could not save the report" }, 500);
  return json({ ok: true });
});
