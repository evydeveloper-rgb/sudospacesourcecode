#!/usr/bin/env python3
"""composio-relay — a loopback relay between OpenClaw and Composio's MCP server.

Composio's endpoint (connect.composio.dev/mcp) answers roughly half of
authenticated requests with a Cloudflare 502, independently per request,
from every network tested. OpenClaw connects once per conversation and, on a
failure, carries on with no connector tools at all. This relay retries the
requests that are safe to repeat, so a conversation almost always gets its
tools.

Safe to repeat: everything except tools/call (the session handshake, tool
listing, notifications, GETs/DELETEs), plus tools/call for the read-only
discovery tools. A tool call that acts on the owner's apps -- send an email,
create an event -- is sent once; if it fails the agent sees the error and can
choose to try again, so nothing is ever done twice behind its back.

The Composio key is read from /opt/sudo/config.json on every request, so
OpenClaw's own config never holds it and a new key applies immediately.
Listens on 127.0.0.1 only.
"""
import http.client
import http.server
import json
import socketserver
import sys
import time

LISTEN = ("127.0.0.1", 18791)
UPSTREAM_HOST = "connect.composio.dev"
UPSTREAM_PATH = "/mcp"
CONFIG = "/opt/sudo/config.json"
# Measured: some requests needed all 6 of an earlier budget and one failed
# all 6, so failure runs are longer than a coin flip suggests. ~10s total.
ATTEMPTS = 10
BACKOFF = (0.3, 0.5, 0.8, 1.0, 1.0, 1.2, 1.2, 1.5, 1.5)
RETRY_STATUS = {502, 503, 504}
READ_ONLY_TOOLS = {"COMPOSIO_SEARCH_TOOLS", "COMPOSIO_GET_TOOL_SCHEMAS", "COMPOSIO_SEARCH_SKILLS"}
FORWARD_REQUEST = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version", "last-event-id")
DROP_RESPONSE = {"connection", "keep-alive", "transfer-encoding", "content-length", "server", "cf-ray",
                 "set-cookie", "alt-svc", "nel", "report-to"}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def composio_key():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return str(json.load(f).get("composio_api_key") or "").strip()
    except (OSError, ValueError):
        return ""


def retryable(method, body):
    """Whether repeating this request can never do something twice."""
    if method != "POST":
        return True
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return False
    messages = payload if isinstance(payload, list) else [payload]
    for msg in messages:
        if not isinstance(msg, dict):
            return False
        if msg.get("method") == "tools/call":
            name = ((msg.get("params") or {}).get("name") or "")
            if name.split("__")[-1] not in READ_ONLY_TOOLS:
                return False
    return True


class Relay(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.relay()

    def do_GET(self):
        self.relay()

    def do_DELETE(self):
        self.relay()

    def relay(self):
        if self.path.split("?")[0] != "/mcp":
            self.send_error(404)
            return
        key = composio_key()
        if not key:
            self.send_error(503, "No Composio key saved")
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {h: self.headers[h] for h in FORWARD_REQUEST if self.headers.get(h)}
        headers["x-consumer-api-key"] = key
        if body is not None:
            headers["Content-Length"] = str(len(body))
        attempts = ATTEMPTS if retryable(self.command, body) else 1
        streaming = self.command == "GET"

        for attempt in range(1, attempts + 1):
            conn = http.client.HTTPSConnection(UPSTREAM_HOST, timeout=None if streaming else 130)
            try:
                conn.request(self.command, UPSTREAM_PATH, body=body, headers=headers)
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                conn.close()
                if attempt < attempts:
                    time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
                    continue
                log(f"upstream unreachable after {attempt} attempt(s): {exc}")
                self.send_error(502, "Composio unreachable")
                return
            if resp.status in RETRY_STATUS and attempt < attempts:
                resp.read()
                conn.close()
                time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
                continue
            if attempt > 1 or resp.status in RETRY_STATUS:
                log(f"{self.command} -> {resp.status} after {attempt} attempt(s)")
            self.pass_through(resp)
            conn.close()
            return

    def pass_through(self, resp):
        """Stream the upstream response as it arrives (it may be SSE)."""
        self.send_response(resp.status)
        for name, value in resp.getheaders():
            if name.lower() not in DROP_RESPONSE:
                self.send_header(name, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(16384)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    log(f"composio relay on {LISTEN[0]}:{LISTEN[1]} -> https://{UPSTREAM_HOST}{UPSTREAM_PATH}")
    try:
        Server(LISTEN, Relay).serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
