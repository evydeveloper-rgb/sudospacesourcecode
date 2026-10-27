#!/usr/bin/env python3
"""remote-link.py — the Sudo's link that does not change.

The public address (a Cloudflare quick tunnel) changes whenever the tunnel
restarts, so a home-screen app saved at it went white after a reboot, and one
saved at sudo.local only worked at home. Each device has a fixed link,
https://sudospace.tech/go/<code>, and tells the link service (links-service/)
its current address whenever it changes; the link page forwards there, at home
or away. Over the tunnel the dashboard still asks for its password.

  remote-link.py publish <url>   the tunnel has a new address (sudo-remote-access.sh)
  remote-link.py clear           remote access switched off
  remote-link.py link            print the fixed link, if published

The key and code live in /etc/sudo/link.json (root only); what the dashboard
shows is in /var/lib/sudo-remote-link.json. A new-user reset removes both, so
the next owner gets a new link and the old one stops working.
"""

import json
import os
import secrets
import sys
import time
import urllib.request

SERVICE = "https://gluklijoptuvampxmtbl.supabase.co/functions/v1/links"
LINK_BASE = "https://sudospace.tech/go/"
KEY_FILE = "/etc/sudo/link.json"
STATE_FILE = "/var/lib/sudo-remote-link.json"
ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"   # no 0/o, 1/l: read aloud and typed


def write_json(path, data, mode):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(path + ".tmp", mode)
    os.replace(path + ".tmp", path)


def identity():
    try:
        data = json.load(open(KEY_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not data.get("key") or not data.get("code"):
        data = {"key": secrets.token_urlsafe(32),
                "code": "".join(secrets.choice(ALPHABET) for _ in range(12)),
                "created_at": int(time.time())}
        write_json(KEY_FILE, data, 0o600)
    return data


def send(url):
    me = identity()
    body = json.dumps({"code": me["code"], "url": url}).encode()
    req = urllib.request.Request(SERVICE + "/link", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Sudo-Link-Key": me["key"]})
    # Right after the tunnel comes up the network can still be settling.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status == 200:
                    return me
        except Exception as exc:  # noqa: BLE001 - offline: the next tunnel start tries again
            print(f"link service: {exc}", file=sys.stderr)
        time.sleep(5 * (attempt + 1))
    return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "publish" and len(sys.argv) > 2:
        me = send(sys.argv[2].rstrip("/"))
        if not me:
            return 1
        write_json(STATE_FILE, {"link": LINK_BASE + me["code"], "url": sys.argv[2], "at": int(time.time())}, 0o644)
        print(LINK_BASE + me["code"])
        return 0
    if cmd == "clear":
        if os.path.isfile(KEY_FILE):
            send(None)
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass
        return 0
    if cmd == "link":
        try:
            print(json.load(open(STATE_FILE, encoding="utf-8"))["link"])
            return 0
        except (OSError, ValueError, KeyError):
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
