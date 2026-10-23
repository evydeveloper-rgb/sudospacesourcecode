#!/usr/bin/env python3
"""test_bridge.py — does voice-bridge.py honour the Hermes API contract?

Runs the bridge against a fake dashboard chat endpoint, with no phone, no
WhatsApp and no sidecar, so this is the part of voice calling that can be
proven on any machine.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


class FakeDashboard(BaseHTTPRequestHandler):
    """Stands in for the device dashboard's /api/chat."""
    reply: str = "It is 8pm and you have two open loops."

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not body.get("message"):
            self.send_response(400)
            self.end_headers()
            return
        out = json.dumps({"reply": self.reply}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sudo-voice-test-")
        cls.agent_port = free_port()
        cls.bridge_port = free_port()

        cls.dash = ThreadingHTTPServer(("127.0.0.1", cls.agent_port), FakeDashboard)
        threading.Thread(target=cls.dash.serve_forever, daemon=True).start()

        token_file = os.path.join(cls.tmp, "bridge.token")
        with open(token_file, "w") as fh:
            fh.write("testtoken")
        briefing = os.path.join(cls.tmp, "voice-briefing.md")
        with open(briefing, "w") as fh:
            fh.write("You are the voice version of a sudo agent.")
        loops = os.path.join(cls.tmp, "PENDING.md")
        with open(loops, "w") as fh:
            fh.write("# PENDING\n\n- [added 2026-10-23] chase the invoice\n")
        cfg = os.path.join(cls.tmp, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"owner_name": "Sam", "agent_name": "Pi"}, fh)

        env = dict(os.environ,
                   VOICE_BRIDGE_LISTEN=f"127.0.0.1:{cls.bridge_port}",
                   SUDO_VOICE_STATE=cls.tmp,
                   BRIDGE_TOKEN_FILE=token_file,
                   BRIDGE_BRIEFING=briefing,
                   VOICE_AGENT_URL=f"http://127.0.0.1:{cls.agent_port}/api/chat",
                   SUDO_CONFIG=cfg,
                   SUDO_OPEN_LOOPS=loops)
        cls.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "voice-bridge.py")],
                                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.bridge_port}/health", timeout=0.5)
                break
            except Exception:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.dash.shutdown()

    def _post(self, path, body, token="testtoken"):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.bridge_port}{path}", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"} if token else {})},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode() or "{}")

    def _poll(self, run_id):
        for _ in range(200):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.bridge_port}/v1/runs/{run_id}", timeout=5) as r:
                data = json.loads(r.read().decode())
            if data["status"] in ("completed", "failed"):
                return data
            time.sleep(0.05)
        self.fail("run never finished")

    def test_health(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.bridge_port}/health", timeout=5) as r:
            self.assertEqual(json.loads(r.read().decode())["status"], "ok")

    def test_run_round_trip(self):
        code, created = self._post("/v1/runs", {"message": "what's on my plate?"})
        self.assertEqual(code, 200)
        self.assertIn("run_id", created)
        done = self._poll(created["run_id"])
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["output"])

    def test_briefing_request_is_answered_locally(self):
        """The sidecar's persona-briefing run must not become a chat turn."""
        code, run = self._post("/v1/runs",
                               {"input": "A voice version of you is about to talk with the owner"})
        self.assertEqual(code, 200)
        done = self._poll(run["run_id"])
        self.assertIn("You are the voice version", done["output"])
        self.assertIn("LIVE SNAPSHOT", done["output"])
        self.assertIn("Sam", done["output"])

    def test_unauthorized_is_rejected(self):
        code, _ = self._post("/v1/runs", {"message": "hello"}, token=None)
        self.assertEqual(code, 401)

    def test_empty_request_is_rejected(self):
        code, _ = self._post("/v1/runs", {"message": "   "})
        self.assertEqual(code, 400)

    def test_run_log_is_written(self):
        code, run = self._post("/v1/runs", {"message": "log me"})
        self.assertEqual(code, 200)
        self._poll(run["run_id"])
        with open(os.path.join(self.tmp, "runs.jsonl")) as fh:
            self.assertTrue(any("log me" in line for line in fh))


if __name__ == "__main__":
    unittest.main(verbosity=2)
