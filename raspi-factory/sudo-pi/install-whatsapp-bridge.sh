#!/bin/bash
# install-whatsapp-bridge.sh — install Node.js if needed, pull the bridge's
# dependencies, and start it.
#
# Triggered from the dashboard (Settings -> Channels -> WhatsApp -> Set up),
# never at boot: most devices will never use this, so nothing about it should
# cost anything -- disk, RAM, or a running process -- until someone actually
# asks for it. That is also why this cannot run during install-factory.sh:
# there is no internet yet at that point, and this needs npm's registry.
#
# Writes progress to the same status file the bridge itself writes once it is
# actually running, so the dashboard can poll one place through the whole
# install-then-pair sequence. Deliberately NOT set -e, matching
# install-devtools.sh: a failure here should land in that file as a readable
# message, not vanish with the script.
set -uo pipefail

STATUS_FILE="/var/lib/sudo-whatsapp-status.json"
LOG="/var/log/sudo-whatsapp-bridge.log"
NODE_MAJOR="${NODE_MAJOR:-22}"
BRIDGE_DIR="/opt/whatsapp-bridge"

exec >> "$LOG" 2>&1
echo "=== install-whatsapp-bridge $(date) ==="

set_status() {
  # $1 state (installing|error)  $2 human message
  python3 - "$1" "$2" "$STATUS_FILE" <<'PY'
import json, sys, time, os
state, message, path = sys.argv[1:4]
current = {}
if os.path.isfile(path):
    try:
        with open(path, encoding="utf-8") as f:
            current = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass
current.update(state=state, message=message, updated=int(time.time() * 1000))
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(current, f)
os.replace(tmp, path)
PY
  echo "[$1] $2"
}

fail() { set_status error "$1"; exit 1; }

set_status installing "Checking internet…"
ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 || fail "No internet. Connect to Wi-Fi and try again."

# ── Node.js — same check-then-install as install-devtools.sh ───────────────
current="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')"
if [ -n "${current:-}" ] && [ "${current:-0}" -ge 18 ] 2>/dev/null; then
  set_status installing "Node $(node -v) already installed, skipping…"
else
  set_status installing "Installing Node.js (this is the slow part, ~2 min)…"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    || fail "Could not reach the Node.js repository"
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs \
    || fail "Node.js installation failed — see ${LOG}"
fi
command -v node >/dev/null 2>&1 || fail "Node installed but not on PATH"

# ── Bridge's own dependencies ───────────────────────────────────────────────
[ -f "${BRIDGE_DIR}/package.json" ] || fail "Bridge files missing — re-flash this device to get them"

set_status installing "Downloading the WhatsApp connector…"
( cd "$BRIDGE_DIR" && npm install --omit=dev --no-audit --no-fund ) \
  || fail "Could not install dependencies — see ${LOG}"

# ── Hand off to systemd, which owns it from here ────────────────────────────
set_status installing "Starting…"
systemctl enable --now sudo-whatsapp-bridge.service \
  || fail "Installed, but the service would not start — see ${LOG}"

# No "done" here: the bridge process itself takes over status updates the
# moment it starts (qr, connected, error, …), and it would be lying to freeze
# the file on "installed" when what the owner is actually waiting on is a
# pairing code that has not been generated yet.
echo "=== install-whatsapp-bridge handed off to the service $(date) ==="
