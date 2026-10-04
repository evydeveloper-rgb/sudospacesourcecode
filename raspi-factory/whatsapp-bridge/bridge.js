// sudo-whatsapp-bridge — links this device to one WhatsApp account and
// answers direct messages using the same agent the dashboard talks to.
//
// Deliberately thin: this file's only job is to speak the WhatsApp protocol
// and turn incoming text into a POST against the dashboard's own
// /api/agent/chat. Local-model-vs-cloud routing, history, personality --
// all of that already exists there and is not duplicated here. A message
// sent from a phone in the kitchen and a message typed into the dashboard
// get the same reply, because they go through the same code.
//
// No status is invented here that the dashboard can't already show: it is
// written as one small JSON file, the same pattern local_model and devtools
// already use, so server.py can read it with the helper it already has.
import makeWASocket, { useMultiFileAuthState, DisconnectReason, Browsers } from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import QRCode from 'qrcode';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';

const AUTH_DIR = process.env.WA_AUTH_DIR || '/opt/sudo/whatsapp-auth';
const STATUS_FILE = process.env.WA_STATUS_FILE || '/var/lib/sudo-whatsapp-status.json';
const CHAT_URL = process.env.SUDO_CHAT_URL || 'http://127.0.0.1/api/agent/chat';
// picoclaw keeps the WhatsApp thread under this key on the device, so a
// message sent from a phone in the kitchen and the next one after it share
// context instead of each being answered cold.
// Fallback session key, used only before we know who we're talking to. Once a
// message arrives we key the session by the sender's JID, so two people who
// text this device never share one memory/thread (and, before this, the
// heartbeat's context gate read a file nobody was writing to).
const DEFAULT_SESSION = process.env.SUDO_SESSION || 'sudo-whatsapp:main';

// Baileys JIDs look like `15551234567@s.whatsapp.net`; picoclaw uses the key
// as a filename for its session store, so strip anything path-unsafe. The
// leading colon on a device JID is normalised to `_` rather than dropped, so
// two different senders can never collapse onto the same file.
function sessionKeyFor(jid) {
  if (!jid) return DEFAULT_SESSION;
  return 'sudo-whatsapp:' + jid.replace(/[^A-Za-z0-9._-]/g, '_');
}
// Who to reach when this device speaks first (heartbeat, reminder). Learned
// from the first direct message the owner sends, and remembered so the box
// does not have to be told again after a restart. WA_OWNER pins it by hand.
const OWNER_FILE = process.env.WA_OWNER_FILE || '/opt/sudo/whatsapp-auth/owner.json';
const SEND_TOKEN_FILE = process.env.WA_SEND_TOKEN_FILE || '/opt/sudo/whatsapp/send-token';
const SEND_PORT = parseInt(process.env.WA_SEND_PORT || '8790', 10);

// Module scope so the outbound server started below can reach the live
// socket. Set in start().
let sock = null;
const CHAT_TIMEOUT_MS = 130000; // matches the dashboard's own chat timeout

function log(line) {
  console.log(`${new Date().toISOString()} ${line}`);
}

function writeStatus(patch) {
  let current = {};
  try {
    current = JSON.parse(fs.readFileSync(STATUS_FILE, 'utf8'));
  } catch (_) {
    // First run, or the file is missing/corrupt -- start clean rather than
    // failing a status write over it.
  }
  const next = { ...current, ...patch, updated: Date.now() };
  fs.mkdirSync(path.dirname(STATUS_FILE), { recursive: true });
  const tmp = `${STATUS_FILE}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(next));
  fs.renameSync(tmp, STATUS_FILE);
}

// Ask the agent exactly the way the dashboard's own chat box does. We name
// our own session key so this device's WhatsApp conversation keeps its thread
// inside picoclaw's session store rather than starting fresh every message.
// (The dashboard box uses its own key, so the two stay separate.)
function askAgent(message, sessionKey) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ message, history: [], session: sessionKey || DEFAULT_SESSION });
    const req = http.request(
      CHAT_URL,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        timeout: CHAT_TIMEOUT_MS,
      },
      (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const parsed = JSON.parse(data);
            if (parsed && parsed.reply) resolve(parsed.reply);
            else reject(new Error(parsed && parsed.error ? parsed.error : 'empty reply'));
          } catch (err) {
            reject(err);
          }
        });
      }
    );
    req.on('timeout', () => req.destroy(new Error('chat request timed out')));
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

function readOwner() {
  if (process.env.WA_OWNER) return process.env.WA_OWNER;
  try {
    const data = JSON.parse(fs.readFileSync(OWNER_FILE, 'utf8'));
    return data && data.jid ? data.jid : null;
  } catch (_) {
    return null;
  }
}

// Remember who wrote to us, so the heartbeat has somewhere to send. First DM
// wins; a later different DM must not silently take over the target.
function rememberOwner(jid) {
  if (!jid || readOwner()) return;
  try {
    fs.mkdirSync(path.dirname(OWNER_FILE), { recursive: true });
    const tmp = `${OWNER_FILE}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ jid, at: Date.now() }));
    fs.renameSync(tmp, OWNER_FILE);
    log(`remembered owner ${jid}`);
  } catch (err) {
    log(`could not save owner: ${err.message}`);
  }
}

// Shared secret the local send helper presents. Generated on first use; the
// file lives beside the bridge's own state, root-only.
function sendToken() {
  try {
    const existing = fs.readFileSync(SEND_TOKEN_FILE, 'utf8').trim();
    if (existing) return existing;
  } catch (_) { /* not created yet */ }
  const token = crypto.randomBytes(24).toString('hex');
  fs.mkdirSync(path.dirname(SEND_TOKEN_FILE), { recursive: true });
  fs.writeFileSync(SEND_TOKEN_FILE, token, { mode: 0o600 });
  return token;
}

async function deliver(text) {
  const owner = readOwner();
  if (!owner) return { ok: false, error: 'no owner yet — message the device first' };
  if (!sock) return { ok: false, error: 'not connected' };
  try {
    await sock.sendMessage(owner, { text });
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

// A tiny loopback HTTP server: the only way into an outbound message. Bound
// to 127.0.0.1 so it is not reachable off the device, and gated on the token
// so a stray local process cannot make the box text its owner.
function startSendServer() {
  const token = sendToken();
  const server = http.createServer((req, res) => {
    if (req.method !== 'POST' || req.url !== '/send') {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'not found' }));
      return;
    }
    if (req.headers['x-sudo-token'] !== token) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'unauthorized' }));
      return;
    }
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 64 * 1024) req.destroy();
    });
    req.on('end', async () => {
      let text = '';
      try {
        text = String((JSON.parse(body) || {}).text || '').trim();
      } catch (_) { /* fall through to the empty check */ }
      const result = text
        ? await deliver(text.slice(0, 4000))
        : { ok: false, error: 'text required' };
      res.writeHead(result.ok ? 200 : 502, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(result));
    });
  });
  server.on('error', (err) => log(`send server error: ${err.message}`));
  server.listen(SEND_PORT, '127.0.0.1', () => log(`send server on 127.0.0.1:${SEND_PORT}`));
}

function messageText(msg) {
  const m = msg.message;
  if (!m) return '';
  return (
    m.conversation ||
    (m.extendedTextMessage && m.extendedTextMessage.text) ||
    ''
  ).trim();
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  sock = makeWASocket({
    auth: state,
    // WhatsApp inspects this string during linking and rejects unknown
    // "browsers", so it must be a shape Baileys knows is valid. The old
    // literal ['Sudo', 'Chrome', '1.0'] is not, which is a known cause of
    // the pairing code failing to link. Browsers.ubuntu('Chrome') produces a
    // real, accepted descriptor; the device still shows as "Sudo" to the
    // owner because the linked-device name comes from elsewhere.
    browser: Browsers.ubuntu('Chrome'),
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      try {
        const dataUri = await QRCode.toDataURL(qr);
        writeStatus({ state: 'qr', qr: dataUri, message: '' });
      } catch (err) {
        log(`could not render pairing code: ${err.message}`);
        writeStatus({ state: 'error', qr: null, message: 'Could not generate a pairing code.' });
      }
    }

    if (connection === 'open') {
      const number = (sock.user && sock.user.id ? sock.user.id.split(':')[0] : '') || '';
      log(`connected as ${number || '(unknown number)'}`);
      writeStatus({ state: 'connected', qr: null, number, message: '' });
    }

    if (connection === 'close') {
      const statusCode =
        lastDisconnect && lastDisconnect.error
          ? new Boom(lastDisconnect.error).output.statusCode
          : null;
      const loggedOut = statusCode === DisconnectReason.loggedOut;

      if (loggedOut) {
        // The phone itself removed this as a linked device. Pairing again
        // needs a fresh QR, not a reconnect -- the saved credentials are
        // dead, so leaving them on disk would just fail the same way again.
        log('logged out from the phone side -- clearing saved session');
        fs.rmSync(AUTH_DIR, { recursive: true, force: true });
        writeStatus({ state: 'absent', qr: null, number: null,
                     message: 'Unlinked from the phone. Set up again to reconnect.' });
        return;
      }

      log(`connection closed (code ${statusCode ?? 'unknown'}) -- reconnecting`);
      writeStatus({ state: 'reconnecting', message: 'Connection dropped, reconnecting…' });
      setTimeout(() => { start().catch((err) => log(`restart failed: ${err.message}`)); }, 3000);
    }
  });

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;
    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue;
      const jid = msg.key.remoteJid;
      // Direct messages only, to start: a group chat means several people
      // reading whatever the agent says, which is a different conversation
      // with different stakes and belongs behind its own explicit toggle.
      if (!jid || jid.endsWith('@g.us') || jid === 'status@broadcast') continue;

      const text = messageText(msg);
      if (!text) continue;

      rememberOwner(jid);

      try {
        await sock.sendPresenceUpdate('composing', jid);
      } catch (_) {
        // Cosmetic only.
      }

      try {
        const reply = await askAgent(text, sessionKeyFor(jid));
        await sock.sendMessage(jid, { text: reply });
      } catch (err) {
        log(`reply failed: ${err.message}`);
        try {
          await sock.sendMessage(jid, {
            text: "Sorry, I couldn't get an answer just then — try again in a moment.",
          });
        } catch (_) {
          // If even this fails the connection itself is the problem, and
          // connection.update above is already handling that.
        }
      }
    }
  });
}

process.on('unhandledRejection', (err) => {
  log(`unhandled rejection: ${err && err.stack ? err.stack : err}`);
});

log('starting');
startSendServer();
start().catch((err) => {
  log(`fatal: ${err && err.stack ? err.stack : err}`);
  writeStatus({ state: 'error', message: String(err && err.message ? err.message : err) });
  process.exit(1);
});
