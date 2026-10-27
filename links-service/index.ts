// links — a Sudo's link that does not change.
//
// The public address of a Sudo (a Cloudflare quick tunnel) changes whenever
// the tunnel restarts, so a home-screen app saved at it went white after a
// reboot, and one saved at sudo.local only worked at home. Each device keeps
// a fixed link, https://sudospace.tech/go/<code>, and reports its current
// address here whenever it changes; the link page asks for it and forwards.
//
//   POST /links/link  {code, url|null}  header X-Sudo-Link-Key (device secret)
//   GET  /links/go?c=<code>             -> {url, updated_at}
//
// The device proves it is the same device with its own random key, of which
// only a hash is stored (as the problem reports do). The dashboard behind the
// address still asks for its password over the tunnel.
import { createClient } from "jsr:@supabase/supabase-js@2";

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const CODE = /^[a-z0-9]{12}$/;
const TUNNEL = /^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/;
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "content-type, x-sudo-link-key",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS, "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

async function sha256(text: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  const path = new URL(req.url).pathname;

  if (req.method === "GET" && path.endsWith("/go")) {
    const code = new URL(req.url).searchParams.get("c") || "";
    if (!CODE.test(code)) return json({ error: "Not a link" }, 400);
    const { data } = await db.from("remote_links").select("url, updated_at").eq("code", code).maybeSingle();
    if (!data) return json({ error: "Unknown link" }, 404);
    return json({ url: data.url, updated_at: data.updated_at });
  }

  if (req.method === "POST" && path.endsWith("/link")) {
    const key = req.headers.get("x-sudo-link-key") || "";
    if (key.length < 32 || key.length > 128) return json({ error: "No key" }, 401);
    let body: { code?: unknown; url?: unknown };
    try {
      body = await req.json();
    } catch {
      return json({ error: "Not JSON" }, 400);
    }
    const code = String(body.code || "");
    const url = body.url === null ? null : String(body.url || "").replace(/\/+$/, "");
    if (!CODE.test(code) || (url !== null && !TUNNEL.test(url))) return json({ error: "Bad request" }, 400);
    const device = await sha256(key);
    const { data: taken } = await db.from("remote_links").select("device_hash").eq("code", code).maybeSingle();
    if (taken && taken.device_hash !== device) return json({ error: "Code taken" }, 409);
    const { error } = await db.from("remote_links").upsert(
      { device_hash: device, code, url, updated_at: new Date().toISOString() },
      { onConflict: "device_hash" },
    );
    if (error) return json({ error: "Could not save" }, 500);
    return json({ ok: true });
  }

  return json({ error: "Not found" }, 404);
});
