#!/bin/bash
# Add OpenClaw's own WhatsApp channel (the @openclaw/whatsapp plugin, pinned to
# the core version) and retire the old Baileys bridge on this device. Run
# detached from the dashboard; progress goes to a status file it polls.
set -uo pipefail

PLUGIN_VERSION="2026.6.11"   # must match install-openclaw.sh's core version
STATUS_FILE="/var/lib/sudo-openclaw-whatsapp-status.json"
LOG="/var/log/sudo-openclaw-whatsapp.log"
OC_BIN="/opt/openclaw/node_modules/.bin/openclaw"
export OPENCLAW_STATE_DIR=/opt/sudo/openclaw OPENCLAW_CONFIG_PATH=/opt/sudo/openclaw/openclaw.json HOME=/root

exec >> "$LOG" 2>&1
echo "=== install-openclaw-whatsapp $(date) ==="

set_status() {
  python3 -c 'import json,sys,time
json.dump({"state": sys.argv[1], "message": sys.argv[2], "updated": int(time.time()*1000)}, open(sys.argv[3], "w"))' \
    "$1" "$2" "$STATUS_FILE"
}
fail() { set_status error "$1"; echo "FAILED: $1"; exit 1; }

[ -x "$OC_BIN" ] || fail "OpenClaw is not installed on this device yet"

if ls -d /opt/sudo/openclaw/npm/projects/openclaw-whatsapp-* >/dev/null 2>&1; then
  echo "WhatsApp plugin already installed"
else
  set_status installing "Downloading WhatsApp for your agent (about a minute)…"
  "$OC_BIN" plugins install "@openclaw/whatsapp@${PLUGIN_VERSION}" \
    || fail "Could not download WhatsApp — check the internet connection and try again"
fi

# Two WhatsApp sockets on one number knock each other off, so the old bridge
# goes the moment OpenClaw owns WhatsApp.
systemctl disable --now sudo-whatsapp-bridge.service 2>/dev/null || true

set_status installing "Starting WhatsApp…"
/usr/local/bin/sudo-configure-picoclaw.sh || true
set_status ready ""
echo "=== install-openclaw-whatsapp complete $(date) ==="
