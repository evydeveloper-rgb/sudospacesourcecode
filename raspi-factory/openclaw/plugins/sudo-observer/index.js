// sudo-observer -- lets the agent see the owner's other WhatsApp chats, when
// the owner has switched that on, and lets it talk about them.
//
// This is the missing half of the WhatsApp story. sudo-contacts answers "who
// may the agent message?"; this answers "what is actually being said?". They
// are deliberately separate files and separate switches:
//
//   - sudo-contacts  -- who the agent may message. Enforced on every send.
//   - sudo-observer  -- what the agent may read. Off until the owner turns on
//                       "Let my agent read my other WhatsApp chats".
//
// Two hooks feed it, and one rule gates it:
//
//   - message_received, on the owner's own account: a message from someone who
//     is not the owner is one of "the owner's other chats". When read_others is
//     off, it is dropped here and never written anywhere. When it is on, it is
//     recorded so the agent can answer "who is waiting on me?".
//   - message_sent, from the agent's own number to a contact: the agent's own
//     reply is recorded too, so "did I get back to Rosa?" has an answer.
//
// WhatsApp only broadcasts inbound hook payloads to plugins when the channel
// (or account) sets pluginHooks.messageReceived -- see the WhatsApp channel
// docs. configure-openclaw.sh sets it on the owner's account only when this
// plugin is installed, so nothing is broadcast it does not have to be.
import { readFileSync, writeFileSync, renameSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const CONFIG_FILE = "/opt/sudo/whatsapp-observer.json";
const ACTIVITY_FILE = "/opt/sudo/whatsapp-activity.json";
const CONTACTS_FILE = "/opt/sudo/whatsapp-contacts.json";

// Keep the tail bounded: this is a small device with a small disk, and the
// agent only ever needs the recent shape of a conversation, not its history.
const MAX_RECENT = 200;
const MAX_BODY = 600;

function digits(value) {
  return String(value ?? "").split("@")[0].split(":")[0].replace(/[^\d]/g, "");
}

function readJson(path, fallback) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch (_) {
    return fallback;
  }
}

function writeJson(path, data) {
  try {
    mkdirSync(dirname(path), { recursive: true });
    const tmp = `${path}.tmp`;
    writeFileSync(tmp, JSON.stringify(data, null, 2), { mode: 0o600 });
    renameSync(tmp, path);
  } catch (_) {
    // A failed write must never take the gateway down over a log line.
  }
}

// Read on every hook rather than cached: the dashboard flips this switch and
// we want the very next message to respect it, not the next restart.
function readOthers() {
  return readJson(CONFIG_FILE, {}).read_others === true;
}

function text(value) {
  return { content: [{ type: "text", text: typeof value === "string" ? value : JSON.stringify(value, null, 2) }] };
}

function bodyOf(content) {
  if (typeof content !== "string") return "";
  // Steer clear of dumping anything enormous into a log the model may read.
  const trimmed = content.trim();
  return trimmed.length > MAX_BODY ? trimmed.slice(0, MAX_BODY) + "…" : trimmed;
}

const configSchema = {
  safeParse(value) {
    if (value === undefined) return { success: true, data: undefined };
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { success: false, error: { issues: [{ path: [], message: "expected config object" }] } };
    }
    return { success: true, data: value };
  },
  jsonSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      ownerNumber: { type: "string" },
      ownerName: { type: "string" },
    },
  },
};

export default {
  id: "sudo-observer",
  name: "Sudo WhatsApp chats",
  description: "Reads the owner's other WhatsApp chats when the owner turns that on.",
  get configSchema() {
    return configSchema;
  },
  register(api) {
    const cfg = api.pluginConfig || {};
    const owner = digits(cfg.ownerNumber);
    const ownerName = cfg.ownerName || "your owner";

    function remember(entry) {
      const data = readJson(ACTIVITY_FILE, { recent: [] });
      const recent = Array.isArray(data.recent) ? data.recent : [];
      recent.push(entry);
      while (recent.length > MAX_RECENT) recent.shift();
      writeJson(ACTIVITY_FILE, { recent, updated: Date.now() });
    }

    function contactName(number) {
      const contacts = readJson(CONTACTS_FILE, {}).contacts || [];
      const hit = contacts.find((c) => digits(c.number) === digits(number));
      return hit ? hit.name : "";
    }

    // Inbound to the owner's own account from anyone but the owner: one of
    // "the owner's other chats". Dropped entirely unless read_others is on.
    api.on("message_received", async (event, ctx) => {
      const channel = ctx?.channelId || event?.metadata?.channel;
      if (channel !== "whatsapp") return;
      const account = ctx?.accountId || event?.metadata?.accountId;
      // Only the owner's own account has "other chats" to read. The agent's
      // own number only ever hears from the owner.
      if (account && account !== "owner") return;
      if (event?.metadata?.isGroup || String(event?.threadId || "").includes("@g.us")) return;
      const sender = digits(event?.senderId ?? event?.from);
      if (!sender) return;
      if (owner && sender === owner) return;
      if (!readOthers()) {
        api.logger.info("sudo-observer: read_others is off, ignoring an inbound WhatsApp message");
        return;
      }
      remember({
        ts: event?.timestamp || Date.now(),
        number: `+${sender}`,
        name: contactName(sender),
        direction: "in",
        body: bodyOf(event?.content),
      });
    }, { priority: 50 });

    // When the owner has reading on, their account accepts messages from people
    // who are not the owner, so the agent can see "the owner's other chats".
    // Those messages must never reach the model: this claims and consumes every
    // non-owner DM on the owner's account before agent routing, so the agent
    // can read them (via whatsapp_chats) but can never act on them or reply.
    // It mirrors the owner-only gate in sudo-contacts; kept here too so the
    // safety does not depend on a second plugin being installed.
    api.on("inbound_claim", async (event, ctx) => {
      if (event?.channel !== "whatsapp") return;
      const account = event?.accountId || ctx?.accountId;
      if (account && account !== "owner") return;
      if (event?.isGroup) return;
      const sender = digits(event?.senderId);
      if (!sender || (owner && sender === owner)) return;
      api.logger.info("sudo-observer: a non-owner message on the owner's account is read-only, not acted on");
      return { handled: true };
    }, { priority: 90 });

    // The agent's own reply to a contact. Recorded regardless of the read
    // switch -- it is the agent's own action, not someone's private message --
    // so that "who is still waiting on a reply?" can tell a reply from silence.
    api.on("message_sent", async (event, ctx) => {
      const channel = ctx?.channelId || event?.metadata?.channel;
      if (channel !== "whatsapp") return;
      const account = ctx?.accountId || event?.metadata?.accountId;
      if (account === "owner") return; // never logs the owner's own chat
      const to = digits(event?.to);
      if (!to || (owner && to === owner)) return;
      remember({
        ts: Date.now(),
        number: `+${to}`,
        name: contactName(to),
        direction: "out",
        body: bodyOf(event?.content),
      });
    }, { priority: 50 });

    // What the owner's other chats look like, most recent first, folded per
    // person. This is the agent's window into them; it exists only while the
    // read switch is on, and says so plainly when it is off.
    api.registerTool({
      name: "whatsapp_chats",
      label: "WhatsApp chats",
      description:
        `The recent WhatsApp conversations on ${ownerName}'s own linked WhatsApp, with people other than ` +
        `${ownerName}. Shows the last messages in each, who sent them, and who may still be waiting on a ` +
        `reply. Only available while ${ownerName} has "Let my agent read my other WhatsApp chats" switched on. ` +
        `You can read these to remind ${ownerName} of things, but you never reply to anyone yourself here -- ` +
        `you only ever talk to ${ownerName}.`,
      parameters: {
        type: "object",
        additionalProperties: false,
        properties: {
          number: { type: "string", description: "Optional: one person's number, e.g. +15551234567." },
          limit: { type: "number", description: "How many recent messages to show per person (default 5)." },
        },
      },
      async execute(_id, params) {
        if (!readOthers()) {
          return text(`Reading ${ownerName}'s other WhatsApp chats is switched off. Only ${ownerName} can turn it on, in Channels -> WhatsApp on the dashboard.`);
        }
        const data = readJson(ACTIVITY_FILE, { recent: [] });
        const recent = Array.isArray(data.recent) ? data.recent : [];
        const want = digits(params?.number);
        const perPerson = Math.max(1, Math.min(Number(params?.limit) || 5, 20));

        const byPerson = new Map();
        for (const entry of recent) {
          const n = digits(entry.number);
          if (!n || (want && n !== want)) continue;
          if (!byPerson.has(n)) byPerson.set(n, []);
          byPerson.get(n).push(entry);
        }
        if (!byPerson.size) {
          return text(want ? `No recent messages with +${want}.` : `No recent WhatsApp chats with other people.`);
        }
        const people = [];
        for (const [n, entries] of byPerson) {
          const last = entries[entries.length - 1];
          // If the most recent message came in and we never answered it, the
          // ball is with the owner. That is the useful line to hand back.
          const waitingOnOwner = last && last.direction === "in";
          people.push({
            number: `+${n}`,
            name: last?.name || "",
            waiting_on_owner: waitingOnOwner,
            last_message: { from: last?.direction === "in" ? "them" : "you", at: last?.ts, body: last?.body },
            recent: entries.slice(-perPerson).map((e) => ({
              from: e.direction === "in" ? "them" : "you", at: e.ts, body: e.body,
            })),
          });
        }
        people.sort((a, b) => (b.last_message.at || 0) - (a.last_message.at || 0));
        return text({
          reading_others: true,
          people_waiting_on_owner: people.filter((p) => p.waiting_on_owner).map((p) => p.number),
          chats: people,
        });
      },
    });

    // The people who wrote to the owner that are not on the list of people the
    // agent may message yet. This is what drives "want me to add them?". It is
    // a suggestion list, never a send list -- adding still goes through
    // sudo-contacts, and only when the owner says so.
    api.registerTool({
      name: "whatsapp_new_numbers",
      label: "WhatsApp new numbers",
      description:
        `People who recently messaged ${ownerName} on WhatsApp but are not yet on the list of people you ` +
        `may message. Check this now and then and, when there is someone new, ask ${ownerName} whether to ` +
        `add them -- briefly, with their number, and only in a real conversation, never as a scheduled ` +
        `notice with nobody there to answer. Adding them is a separate step (whatsapp_contacts, action add) ` +
        `and only happens if ${ownerName} says yes.`,
      parameters: { type: "object", additionalProperties: false, properties: {} },
      async execute() {
        if (!readOthers()) {
          return text(`Reading ${ownerName}'s other WhatsApp chats is switched off, so there is nothing to compare against the list.`);
        }
        const data = readJson(ACTIVITY_FILE, { recent: [] });
        const recent = Array.isArray(data.recent) ? data.recent : [];
        const contacts = readJson(CONTACTS_FILE, {}).contacts || [];
        const known = new Set(contacts.map((c) => digits(c.number)));
        const seen = new Map();
        for (const entry of recent) {
          if (entry.direction !== "in") continue;
          const n = digits(entry.number);
          if (!n || n === owner) continue;
          if (!seen.has(n)) seen.set(n, entry);
        }
        const fresh = [];
        for (const [n, entry] of seen) {
          if (known.has(n)) continue;
          fresh.push({ number: `+${n}`, last_message: bodyOf(entry.body), at: entry.ts });
        }
        return text({
          new_numbers: fresh,
          note: fresh.length
            ? `Ask ${ownerName} once, in conversation, whether to add any of these. Do not add without a clear yes.`
            : "No new numbers since the last look.",
        });
      },
    });
  },
};
