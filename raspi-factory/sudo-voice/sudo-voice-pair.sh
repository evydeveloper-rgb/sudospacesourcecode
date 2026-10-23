#!/bin/bash
# sudo-voice-pair.sh — link the bot's WhatsApp account to the sidecar.
#
# Only the owner can do this step: it needs the bot's phone in hand to scan a
# QR (WhatsApp > Linked devices > Link a device). The dashboard starts this
# script, shows the QR it produces, and polls the status file until the sidecar
# reports a session.
#
# Runs the pair command in the background and returns immediately — pairing is
# a human-speed action and the dashboard must stay responsive.
set -uo pipefail

OPT_DIR="/opt/sudo-voice"
BIN="$OPT_DIR/bin/hermes-wa-voice"
STATE_DIR="/var/lib/sudo-voice"
STATUS_FILE="/var/lib/sudo-voice-status.json"
LOG="/var/log/sudo-voice-pair.log"
QR="$STATE_DIR/pair-qr.svg"
PIDFILE="$STATE_DIR/pair.pid"

set_status() {
  python3 - "$1" "$2" "$STATUS_FILE" <<'PY'
import json, os, sys, time
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
}

[ -x "$BIN" ] || { set_status error "Install voice calls first."; exit 1; }

# A stale QR from a previous attempt would be scanned and look broken.
rm -f "$QR"

if [ -f "$PIDFILE" ]; then
  old="$(cat "$PIDFILE" 2>/dev/null)"
  if [ -n "${old:-}" ] && kill -0 "$old" 2>/dev/null; then
    set_status needs_pairing "Still waiting for the QR to be scanned."
    exit 0
  fi
fi

set_status needs_pairing "Preparing a QR code…"
cd "$STATE_DIR" || exit 1
HWV_DATA_DIR="$STATE_DIR" nohup "$BIN" pair >"$LOG" 2>&1 &
echo $! > "$PIDFILE"

# The QR appears within a few seconds. Correct the message rather than claiming
# success — our job is only to make the code appear somewhere they can see it.
for _ in $(seq 1 20); do
  [ -s "$QR" ] && break
  sleep 0.5
done

if [ -s "$QR" ]; then
  set_status needs_pairing "Scan this with the bot's phone: WhatsApp > Linked devices > Link a device."
else
  set_status needs_pairing "Waiting for the QR code — if it does not appear, open the log and retry."
fi

# Pairing finishes when the phone confirms, which the QR cannot tell us. Watch
# for the session file instead, then start the line ourselves: nobody should
# have to re-run setup after scanning, and the dashboard has no way to do it.
(
  for _ in $(seq 1 120); do
    if [ -s "$STATE_DIR/whatsapp.db" ]; then
      systemctl enable sudo-voice-sidecar.service >/dev/null 2>&1 || true
      systemctl restart sudo-voice-sidecar.service >/dev/null 2>&1 || true
      sleep 5
      if systemctl is-active --quiet sudo-voice-sidecar.service; then
        set_status running "Voice calls are live."
      else
        set_status error "Paired, but the voice line would not start — see the install log."
      fi
      rm -f "$QR"
      exit 0
    fi
    sleep 5
  done
  set_status needs_pairing "No confirmation yet. Open voice calls and show a fresh QR when you are ready."
) >/dev/null 2>&1 &
