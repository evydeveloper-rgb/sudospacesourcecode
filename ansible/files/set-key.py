#!/usr/bin/env python3
"""Save a provider key in sudo's settings and apply it (used by sudo.yml).

The key comes in the SUDO_KEY environment variable, never on the command line
(where any user could read it). Prints "changed" or "unchanged".
"""
import json
import os
import subprocess
import sys

CONFIG = "/opt/sudo/config.json"
provider = sys.argv[1] if len(sys.argv) > 1 else "openrouter"
key = os.environ.get("SUDO_KEY", "").strip()
if not key:
    sys.exit("SUDO_KEY is empty")
try:
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
except (OSError, ValueError):
    cfg = {}
keys = cfg.setdefault("provider_keys", {})
if keys.get(provider) == key:
    print("unchanged")
    sys.exit(0)
keys[provider] = key
os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
tmp = CONFIG + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
os.chmod(tmp, 0o600)
os.replace(tmp, CONFIG)
subprocess.run(["/usr/local/bin/sudo-configure-agent.sh"], check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print("changed")
