// Sudo problem reports: the opt-in daily report from beta devices.
//
// A sudo makes a random key the first time its owner turns reports on and
// keeps it on the device (/etc/sudo/reports.json); this function stores only
// its SHA-256, so reports from one device can be told apart without knowing
// whose it is. No accounts, no emails. The table is locked to everyone else
// (RLS on, no policies); only this function, with the service role, writes.
//
//   POST /report   one report (JSON, at most 32 KB; 4 per device per day)
//   POST /forget   delete every report this device has sent
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const db = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  { auth: { persistSession: false } },
);

const MAX_BYTES = 32 * 1024;
const PER_DAY = 4;

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

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "POST only" }, 405);
  const route = new URL(req.url).pathname.replace(/^.*\/reports/, "") || "/";
  const key = reportKey(req);
  if (!key) return json({ error: "Missing device key" }, 401);
  const device = await sha256(key);

  if (route === "/forget") {
    const { error, count } = await db.from("problem_reports").delete({ count: "exact" }).eq("device_hash", device);
    if (error) return json({ error: "Could not delete" }, 500);
    return json({ ok: true, deleted: count ?? 0 });
  }

  if (route !== "/report") return json({ error: "Not found" }, 404);
  const raw = await req.text();
  if (raw.length > MAX_BYTES) return json({ error: "Report too large" }, 413);
  let report: Record<string, unknown>;
  try {
    report = JSON.parse(raw);
  } catch {
    return json({ error: "Not JSON" }, 400);
  }
  if (!report || typeof report !== "object" || Array.isArray(report) || report.schema !== 1) {
    return json({ error: "Not a report" }, 400);
  }
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const { count } = await db.from("problem_reports").select("id", { count: "exact", head: true })
    .eq("device_hash", device).gte("received_at", since);
  if ((count ?? 0) >= PER_DAY) return json({ error: "Enough reports from this device today" }, 429);
  // Defence in depth: the device scrubs before sending, but a reminder's name
  // is the owner's own words and OpenClaw writes it unquoted
  // ("Cron: <name> failed"), which older devices let through.
  report = JSON.parse(JSON.stringify(report).replace(/(Cron(?: job)?:?)\s+[^"\\]*?\s+failed/g, "$1 <reminder> failed"));
  const version = typeof report.sudo === "string" ? report.sudo.slice(0, 20) : null;
  const { error } = await db.from("problem_reports").insert({ device_hash: device, sudo_version: version, report });
  if (error) return json({ error: "Could not save the report" }, 500);
  return json({ ok: true });
});
