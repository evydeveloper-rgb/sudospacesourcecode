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

fresh=0
if ls -d /opt/sudo/openclaw/npm/projects/openclaw-whatsapp-* >/dev/null 2>&1; then
  echo "WhatsApp plugin already installed"
else
  fresh=1
  set_status installing "Downloading WhatsApp for your agent (about a minute)…"
  "$OC_BIN" plugins install "@openclaw/whatsapp@${PLUGIN_VERSION}" \
    || fail "Could not download WhatsApp — check the internet connection and try again"
fi

# The old bridge (if this device had one) keeps running until the agent has
# its own WhatsApp link: configure-openclaw.sh switches it off then. Switching
# it off here cut owners off from WhatsApp entirely when they stopped before
# scanning the new code. The two are separate linked devices, so they can
# overlap for the few seconds between the scan and the configure run.

set_status installing "Starting WhatsApp…"
/usr/local/bin/sudo-configure-agent.sh || true
# A new plugin only loads when the gateway starts, and the configure step
# leaves a running gateway alone when the config itself did not change.
# Also restart when WhatsApp is linked but not connected -- the case of an
# agent moved in from another device, whose plugin arrived after the gateway.
if [ "$fresh" = 1 ] || ! systemctl is-active --quiet sudo-openclaw-gateway.service; then
  systemctl restart sudo-openclaw-gateway.service 2>/dev/null || true
else
  systemctl try-restart sudo-openclaw-gateway.service 2>/dev/null || true
fi
set_status ready ""
echo "=== install-openclaw-whatsapp complete $(date) ==="
