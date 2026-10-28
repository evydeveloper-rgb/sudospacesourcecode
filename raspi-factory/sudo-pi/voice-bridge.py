#!/usr/bin/env python3
"""voice-bridge.py — lets the WhatsApp voice sidecar ask the Sudo agent.

The voice calls come from hermes-whatsapp-voice (MIT, a whatsapp-rust
sidecar + Gemini Live), which was written for the Hermes agent: during a call
the voice hands real work ("what's on my calendar", "remind me at 5") to the
agent over the Hermes API. This is that API, answered by Sudo's own OpenClaw
agent through its gateway:

  GET  /health
  POST /v1/runs             {input, instructions, session_id} -> {run_id}
  GET  /v1/runs/<id>        -> {status, output, error}
  POST /v1/runs/<id>/stop   the call ended or moved on

Loopback only, bearer-token protected (HERMES_API_KEY, shared with the
sidecar). Everything stays on the device; Gemini only hears the call itself.
"""

import json
import os
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = os.environ.get("BRIDGE_LISTEN", "127.0.0.1:8642")
TOKEN = os.environ.get("HERMES_API_KEY", "")
GATEWAY = os.environ.get("SUDO_GATEWAY_URL", "http://127.0.0.1:18790")
GATEWAY_TOKEN_FILE = os.environ.get("SUDO_GATEWAY_TOKEN_FILE", "/etc/sudo/openclaw_gateway_token")
TIMEOUT = int(os.environ.get("BRIDGE_TIMEOUT", "170"))   # under the sidecar's 180s

RUNS = {}
LOCK = threading.Lock()


def session_key():
    """One voice conversation per day: the agent remembers earlier calls that
    day, and no single session grows without end (on an earlier setup one ran
    for three days, grew past 100k tokens and stalled calls)."""
    return "agent:main:voice-call-" + time.strftime("%Y%m%d")


def ask_agent(text):
    token = open(GATEWAY_TOKEN_FILE, encoding="utf-8").read().strip()
    body = json.dumps({"model": "openclaw/default", "messages": [{"role": "user", "content": text}]}).encode()
    req = urllib.request.Request(GATEWAY + "/v1/chat/completions", data=body, method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json",
        "x-openclaw-session-key": session_key()})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choice = (data.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    # Read aloud: markdown would be spoken as symbols.
    for ch in ("*", "#", "`"):
        text = text.replace(ch, "")
    return text


def run(run_id, text):
    try:
        output = ask_agent(text) or "I could not get an answer."
        status, error = "completed", None
    except Exception as exc:  # noqa: BLE001 - told to the voice, which says so on the call
        output, status, error = "", "failed", str(exc)[:300]
    with LOCK:
        entry = RUNS.get(run_id)
        if entry and entry["status"] == "running":
            entry.update(status=status, output=output, error=error, finished=time.time())
    # Keep the last hour of runs only.
    with LOCK:
        for rid in [r for r, e in RUNS.items() if time.time() - e["started"] > 3600]:
            RUNS.pop(rid, None)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return bool(TOKEN) and self.headers.get("Authorization", "") == "Bearer " + TOKEN

    def do_GET(self):
        if self.path.startswith("/health"):
            return self.send(200, {"status": "ok"})
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"})
        if self.path.startswith("/v1/runs/"):
            with LOCK:
                entry = RUNS.get(self.path.rstrip("/").rsplit("/", 1)[-1])
            if not entry:
                return self.send(404, {"error": "no such run"})
            return self.send(200, {"status": entry["status"], "output": entry["output"], "error": entry["error"]})
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}") if length else {}
        except ValueError:
            return self.send(400, {"error": "not JSON"})
        path = self.path.rstrip("/")
        if path.endswith("/stop") and path.startswith("/v1/runs/"):
            with LOCK:
                entry = RUNS.get(path.split("/")[-2])
                if entry and entry["status"] == "running":
                    entry.update(status="cancelled", finished=time.time())
            return self.send(200, {"ok": True})
        if path == "/v1/runs":
            text = str(body.get("input") or "").strip()
            if not text:
                return self.send(400, {"error": "input is required"})
            instructions = str(body.get("instructions") or "").strip()
            if instructions:
                text = instructions + "\n\n" + text
            run_id = uuid.uuid4().hex
            with LOCK:
                RUNS[run_id] = {"status": "running", "output": "", "error": None, "started": time.time()}
            threading.Thread(target=run, args=(run_id, text), daemon=True).start()
            return self.send(200, {"run_id": run_id})
        return self.send(404, {"error": "not found"})


if __name__ == "__main__":
    host, _, port = LISTEN.partition(":")
    ThreadingHTTPServer((host, int(port)), Handler).serve_forever()
