// Sudo community: one account per sudo, signed in with the device's own key.
//
// A sudo makes a random key when it joins and keeps it on the device; this
// function stores only its SHA-256. Every member request carries the key in
// X-Sudo-Device-Key and is matched by that hash -- no passwords, no emails.
// The tables are locked to everyone else (RLS on, no policies); only this
// function, with the service role, reads or writes them.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const db = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  { auth: { persistSession: false } },
);

const USERNAME = /^[a-z0-9_]{3,20}$/;
const RESERVED = new Set([
  "admin", "administrator", "sudo", "sudospace", "support", "help", "official", "community",
  "team", "staff", "moderator", "mod", "root", "system", "everyone", "null", "undefined",
]);
const POSTS_PER_MINUTE = 3;
const REPORTS_TO_HIDE = 3;

function json(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

async function sha256(text: string) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function deviceKey(req: Request) {
  const key = req.headers.get("x-sudo-device-key") || "";
  return key.length >= 32 && key.length <= 128 ? key : "";
}

async function member(req: Request) {
  const key = deviceKey(req);
  if (!key) return null;
  const { data } = await db.from("community_accounts")
    .select("id, username, agent_name, city")
    .eq("key_hash", await sha256(key))
    .maybeSingle();
  return data;
}

function usernameProblem(u: string) {
  if (!USERNAME.test(u)) return "format";
  if (RESERVED.has(u)) return "taken";
  return null;
}

// City -> coordinates of that city's centre. Only the city the person typed
// is looked up; nothing about where the device actually is. OpenStreetMap
// first, then Open-Meteo (name only, matched on the region or country typed
// after the comma: "Austin, Texas").
async function geocode(city: string) {
  const round = (lat: number, lng: number) => ({
    lat: Math.round(lat * 100) / 100, lng: Math.round(lng * 100) / 100,
  });
  try {
    const r = await fetch(
      "https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(city),
      { headers: { "User-Agent": "sudo-community/1.0 (https://sudospace.tech)" },
        signal: AbortSignal.timeout(5000) },
    );
    const hits = await r.json();
    if (Array.isArray(hits) && hits.length) return round(Number(hits[0].lat), Number(hits[0].lon));
  } catch { /* try the next one */ }
  try {
    const [name, ...rest] = city.split(",").map((x) => x.trim()).filter(Boolean);
    if (!name) return null;
    const r = await fetch(
      "https://geocoding-api.open-meteo.com/v1/search?count=10&language=en&format=json&name=" +
        encodeURIComponent(name),
      { signal: AbortSignal.timeout(5000) },
    );
    const results = (await r.json()).results || [];
    const want = rest.join(" ").toLowerCase();
    const hit = results.find((x: any) => want && [x.admin1, x.country]
      .some((v: string) => v && (want.includes(String(v).toLowerCase()) || String(v).toLowerCase().includes(want))))
      || results[0];
    if (hit) return round(Number(hit.latitude), Number(hit.longitude));
  } catch { /* no coordinates: the person just is not on the map */ }
  return null;
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const route = url.pathname.replace(/^.*\/community/, "") || "/";
  try {
    // Is a username free? Public, so the join form can answer as you type.
    if (route === "/available" && req.method === "GET") {
      const u = (url.searchParams.get("u") || "").trim().toLowerCase();
      const problem = usernameProblem(u);
      if (problem) return json({ available: false, reason: problem });
      const { data } = await db.from("community_accounts").select("id").eq("username", u).maybeSingle();
      return json({ available: !data, reason: data ? "taken" : null });
    }

    if (route === "/join" && req.method === "POST") {
      const key = deviceKey(req);
      if (!key) return json({ error: "Missing device key" }, 400);
      if (await member(req)) return json({ error: "This sudo already has an account" }, 409);
      const body = await req.json().catch(() => ({}));
      const username = String(body.username || "").trim().toLowerCase();
      const agentName = String(body.agent_name || "").trim().slice(0, 32);
      const city = String(body.city || "").trim().slice(0, 80);
      const problem = usernameProblem(username);
      if (problem) {
        return json({
          error: problem === "format" ? "Usernames are 3 to 20 letters, numbers or _" : "That username is taken",
          reason: problem,
        }, 400);
      }
      if (!agentName || !city) return json({ error: "Your agent's name and a city are needed" }, 400);
      const coords = await geocode(city);
      const { data, error } = await db.from("community_accounts").insert({
        username, agent_name: agentName, city,
        lat: coords?.lat ?? null, lng: coords?.lng ?? null,
        key_hash: await sha256(key),
      }).select("username, agent_name, city").single();
      if (error) {
        const taken = error.code === "23505";
        return json({ error: taken ? "That username is taken" : "Could not create the account",
                      reason: taken ? "taken" : "error" }, taken ? 409 : 500);
      }
      return json({ ok: true, account: data });
    }

    // Everything below is for members only.
    const me = await member(req);
    if (!me) return json({ error: "Not a community member yet" }, 401);

    if (route === "/me" && req.method === "GET") {
      return json({ account: { username: me.username, agent_name: me.agent_name, city: me.city } });
    }

    if (route === "/feed" && req.method === "GET") {
      const { data, error } = await db.from("community_posts")
        .select("id, body, created_at, account_id, community_accounts(username, agent_name, city)")
        .eq("hidden", false)
        .order("created_at", { ascending: false })
        .limit(60);
      if (error) throw error;
      return json({
        posts: (data || []).map((p: any) => ({
          id: p.id, body: p.body, created_at: p.created_at, mine: p.account_id === me.id,
          username: p.community_accounts?.username, agent_name: p.community_accounts?.agent_name,
          city: p.community_accounts?.city,
        })),
      });
    }

    if (route === "/posts" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const text = String(body.body || "").trim();
      if (!text) return json({ error: "Write something first" }, 400);
      if (text.length > 2000) return json({ error: "That post is too long (2000 characters at most)" }, 400);
      const since = new Date(Date.now() - 60_000).toISOString();
      const { count } = await db.from("community_posts").select("id", { count: "exact", head: true })
        .eq("account_id", me.id).gte("created_at", since);
      if ((count || 0) >= POSTS_PER_MINUTE) return json({ error: "Slow down a little, then post again" }, 429);
      const { error } = await db.from("community_posts").insert({ account_id: me.id, body: text });
      if (error) throw error;
      return json({ ok: true });
    }

    if (route === "/posts/delete" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      await db.from("community_posts").delete().eq("id", String(body.id || "")).eq("account_id", me.id);
      return json({ ok: true });
    }

    if (route === "/report" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const postId = String(body.id || "");
      await db.from("community_reports").upsert({ post_id: postId, reporter_id: me.id },
        { onConflict: "post_id,reporter_id", ignoreDuplicates: true });
      const { count } = await db.from("community_reports").select("id", { count: "exact", head: true })
        .eq("post_id", postId);
      if ((count || 0) >= REPORTS_TO_HIDE) await db.from("community_posts").update({ hidden: true }).eq("id", postId);
      return json({ ok: true });
    }

    // Where sudos are: by city, as people typed it. Never a device location.
    if (route === "/map" && req.method === "GET") {
      const { data, error } = await db.from("community_accounts")
        .select("username, agent_name, city, lat, lng").not("lat", "is", null);
      if (error) throw error;
      const places = new Map<string, any>();
      for (const a of data || []) {
        const k = `${a.lat},${a.lng}`;
        if (!places.has(k)) places.set(k, { city: a.city, lat: a.lat, lng: a.lng, members: [] });
        places.get(k).members.push({ username: a.username, agent_name: a.agent_name, me: a.username === me.username });
      }
      return json({ places: [...places.values()], total: (data || []).length });
    }

    // Leave: the account and its posts go.
    if (route === "/leave" && req.method === "POST") {
      await db.from("community_accounts").delete().eq("id", me.id);
      return json({ ok: true });
    }

    return json({ error: "Not found" }, 404);
  } catch (_e) {
    return json({ error: "Something went wrong. Try again in a moment." }, 500);
  }
});
