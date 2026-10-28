#!/usr/bin/env python3
"""voice-call.py — the agent rings its owner on WhatsApp.

  voice-call.py --reason "what the call is for"   ring now
  voice-call.py --status                          linked? on a call?

The reason is the voice's brief for the call, written as an instruction
("Remind her the dentist is at 3 and ask if she wants a lift"). There is no
number to give: the voice sidecar only ever calls the owner (voice-calls.sh).
Prints JSON; exit 0 when the call was placed, 1 when it could not be.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

ENV_FILE = "/etc/sudo/voice.env"
BASE = "http://127.0.0.1:8796"


def token():
    for line in open(ENV_FILE, encoding="utf-8"):
        if line.startswith("HWV_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(json.dumps({"ok": False, "error": "voice calls are not set up"}))


def request(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode() or "{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    try:
        if a.status:
            print(json.dumps(request("GET", "/health")))
            return 0
        if not (a.reason or "").strip():
            print(json.dumps({"ok": False, "error": "--reason is required"}))
            return 1
        print(json.dumps({"ok": True, "call": request("POST", "/calls", {"reason": a.reason.strip()[:1000]})}))
        return 0
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("error")
        except ValueError:
            msg = str(exc)
        print(json.dumps({"ok": False, "error": msg}))
    except (OSError, urllib.error.URLError) as exc:
        print(json.dumps({"ok": False, "error": f"voice calls are not running ({exc})"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
