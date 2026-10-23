#!/usr/bin/env python3
"""voice-bridge.py — the agent-API shim that hermes-wa-voice expects.

The sidecar (hermes-wa-voice) is a Hermes plugin: mid-call it POSTs a request
to HERMES_API_URL and polls for the answer. sudo has no Hermes, so this process
impersonates one and forwards each request to the device's own agent.

Why not talk to the gateway directly: a device runs OpenClaw *or* picoclaw,
and the dashboard already knows which and how to reach it (its /api/chat powers
the chat box and the existing WhatsApp bridge). Going through the dashboard
means one code path for both backends, and no gateway token to store here.

Contract, reverse-engineered from the sidecar binary:
  GET  /health
  POST /v1/runs       -> {"run_id": "..."}            (async)
  GET  /v1/runs/{id}  -> {"status": "completed", "output": "..."}

Everything the voice agent says must come from either this briefing or the real
agent. If the agent is unreachable we say so -- a voice that invents answers is
worse than one that admits it cannot look something up.
"""
import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = os.environ.get("VOICE_BRIDGE_LISTEN", "127.0.0.1:8642")
STATE_DIR = os.environ.get("SUDO_VOICE_STATE", "/var/lib/sudo-voice")
TOKEN_FILE = os.environ.get("BRIDGE_TOKEN_FILE", f"{STATE_DIR}/bridge.token")
RUN_LOG = os.environ.get("BRIDGE_RUN_LOG", f"{STATE_DIR}/runs.jsonl")
BRIEFING = os.environ.get("BRIDGE_BRIEFING", "/opt/sudo-voice/voice-briefing.md")
AGENT_URL = os.environ.get("VOICE_AGENT_URL", "http://127.0.0.1/api/chat")
SUDO_CONFIG = os.environ.get("SUDO_CONFIG", "/opt/sudo/config.json")
OPEN_LOOPS = os.environ.get("SUDO_OPEN_LOOPS", "/opt/sudo/agent/PENDING.md")
TIMEOUT = int(os.environ.get("BRIDGE_TIMEOUT", "240"))
SNAPSHOT_TTL = 300

# One session per day. A call's back-and-forth belongs to that day's thread; a
# fresh key each morning keeps history bounded and stops one long-lived session
# from accumulating every call the device has ever made.
SESSION_KEY = os.environ.get("BRIDGE_SESSION_KEY", "") or (
    "sudo-voice:" + dt.datetime.now().strftime("%Y%m%d"))

RUNS = {}
LOCK = threading.Lock()
_SNAP = {"at": 0.0, "text": ""}


def _token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return os.environ.get("BRIDGE_TOKEN", "")


TOKEN = _token()


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def device_timezone():
    """The owner's clock, not UTC: 'it's 8pm' must be true on their table."""
    tz = os.environ.get("TZ") or (_read("/etc/timezone").strip())
    if tz:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(tz)
        except Exception:
            pass
    return None


def live_snapshot():
    """Warm context, so a simple question costs zero agent round-trips.

    Each ask_hermes is 1-4s of dead air on a call, which is the difference
    between a conversation and a walkie-talkie. Anything answerable here is
    answered without one.
    """
    if time.time() - _SNAP["at"] < SNAPSHOT_TTL and _SNAP["text"]:
        return _SNAP["text"]

    parts = []
    try:
        now = dt.datetime.now(device_timezone())
        parts.append(f"Right now: {now.strftime('%A %d %B %Y, %H:%M')}.")
    except Exception:
        parts.append("Right now: " + dt.datetime.now().strftime("%A %d %B %Y, %H:%M"))

    cfg = _read_json(SUDO_CONFIG, {}) or {}
    owner = (cfg.get("owner_name") or "").strip()
    agent = (cfg.get("agent_name") or cfg.get("name") or "").strip()
    if agent:
        parts.append(f"You are {agent}, the agent that lives on this device.")
    if owner:
        parts.append(f"The person on the call is {owner} — the owner of this device.")

    loops = _read(OPEN_LOOPS).strip()
    if loops:
        # The open-loops list, minus the header noise, is exactly what "what's
        # on my plate?" means. Cap it: a call cannot absorb an essay.
        lines = [ln.strip(" -*\t") for ln in loops.splitlines()
                 if ln.strip().startswith("- [")]
        if lines:
            parts.append("Open loops the owner is tracking:\n" +
                         "\n".join(f"- {ln}" for ln in lines[:6]))

    text = "\n".join(parts)
    text += ("\n\nIf a question can be answered from the block above, answer it directly, "
             "with no tool call. Otherwise call ask_hermes and say a few words first.")
    _SNAP.update(at=time.time(), text=text)
    return text


def briefing_text():
    """The voice persona. Personalised here rather than per-device at install
    time, so renaming the agent in the dashboard also renames the voice.
    """
    template = _read(BRIEFING).strip()
    if not template:
        return ""
    cfg = _read_json(SUDO_CONFIG, {}) or {}
    agent = (cfg.get("agent_name") or cfg.get("name") or "your agent").strip()
    owner = (cfg.get("owner_name") or "the owner").strip()
    return (template.replace("{{agent_name}}", agent)
                    .replace("{{owner_name}}", owner))


def ask_agent(message):
    """One turn through the dashboard's chat API — the same path the chat box
    and the WhatsApp bridge use, so OpenClaw and picoclaw devices both work."""
    payload = json.dumps({"message": message, "history": [], "session": SESSION_KEY}).encode()
    req = urllib.request.Request(
        AGENT_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode() or "{}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"the agent is not reachable on this device ({exc.reason})")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"the agent did not answer ({exc})")
    if not isinstance(data, dict):
        raise RuntimeError("the agent returned something that is not JSON")
    return (data.get("reply") or "").strip()


def speakable(text):
    """Voice output: markdown punctuation reads as noise."""
    for ch in ("*", "#", "`", "_"):
        text = text.replace(ch, "")
    return " ".join(text.split())


def log_run(**fields):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        fields.setdefault("ts", time.time())
        with open(RUN_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields) + "\n")
    except OSError:
        pass


def extract_request(body):
    """The sidecar sends a Hermes run payload; the prompt can be in several
    shapes depending on version. Accept the ones we have seen."""
    if isinstance(body, str):
        return body.strip()
    if not isinstance(body, dict):
        return ""
    for key in ("message", "input", "prompt", "text"):
        val = body.get(key)
        if isinstance(key, str) and isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            inner = val.get("text") or body.get("content")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    msgs = body.get("messages")
    if isinstance(msgs, list) and msgs:
        last = msgs[-1]
        if isinstance(last, dict):
            content = last.get("content")
            if isinstance(content, str):
                return content.strip()
    return ""


def run_agent(run_id, request_text):
    try:
        if briefing_text() and "voice version of you" in request_text.lower():
            # The sidecar's own persona-briefing request: answer it ourselves so
            # every call is briefed identically, and add the live snapshot.
            output = briefing_text() + "\n\n=== LIVE SNAPSHOT ===\n" + live_snapshot()
        else:
            reply = ask_agent(request_text)
            if not reply:
                raise RuntimeError("the agent returned an empty answer")
            output = speakable(reply)
        with LOCK:
            RUNS[run_id].update(status="completed", output=output, finished_at=time.time())
        log_run(run_id=run_id, event="finish", status="completed",
                prompt=request_text[:500], output=output[:2000])
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or "the agent could not answer"
        with LOCK:
            RUNS[run_id] = {"status": "failed", "output": "", "error": msg,
                            "started_at": RUNS.get(run_id, {}).get("started_at", time.time())}
        print(f"[bridge] run {run_id} failed: {msg}", flush=True)
        log_run(run_id=run_id, event="error", status="error", error=msg[:400])


class Handler(BaseHTTPRequestHandler):
    server_version = "sudo-voice-bridge"

    def log_message(self, fmt, *args):
        print("[bridge] " + fmt % args, flush=True)

    def _send(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        # Loopback only, but the sidecar still proves itself with the token the
        # installer generated. No token file means misconfigured, not open.
        return bool(TOKEN) and self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send(200, {"status": "ok"})
        if self.path.startswith("/v1/runs/"):
            run_id = self.path.rstrip("/").rsplit("/", 1)[-1]
            entry = RUNS.get(run_id)
            if not entry:
                return self._send(404, {"error": "no such run"})
            out = {"run_id": run_id, "status": entry["status"],
                   "output": entry.get("output", "")}
            if entry.get("error"):
                out["error"] = entry["error"]
            return self._send(200, out)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = raw
        if not self.path.startswith("/v1/runs"):
            return self._send(404, {"error": "not found"})
        request_text = extract_request(body)
        if not request_text:
            return self._send(400, {"error": "no request text in body"})
        run_id = uuid.uuid4().hex
        print(f"[voice] run {run_id}: {request_text[:160]!r}", flush=True)
        with LOCK:
            RUNS[run_id] = {"status": "running", "output": "", "started_at": time.time()}
        log_run(run_id=run_id, event="start", status="running", prompt=request_text[:500])
        threading.Thread(target=run_agent, args=(run_id, request_text), daemon=True).start()
        return self._send(200, {"run_id": run_id})


def main():
    host, _, port = LISTEN.partition(":")
    if not TOKEN:
        print(f"[bridge] no token at {TOKEN_FILE} — set it up via install-sudo-voice.sh", flush=True)
    print(f"[bridge] listening on {host}:{port} agent={AGENT_URL} session={SESSION_KEY}", flush=True)
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()


if __name__ == "__main__":
    main()
