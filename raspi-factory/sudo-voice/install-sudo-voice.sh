#!/bin/bash
# install-sudo-voice.sh — install WhatsApp voice calls on a sudo device.
#
# Triggered from the dashboard (Settings -> Channels -> Voice calls), never at
# boot: like the messaging bridge, most devices may never use this, so it must
# cost nothing until someone asks for it. That is also why it cannot run during
# install-factory.sh — there is no internet at that point.
#
# Writes progress to /var/lib/sudo-voice-status.json, the same
# {state, message, updated} contract the dashboard already polls for WhatsApp.
# Deliberately NOT set -e: a failure must land in that file as a sentence the
# owner can read, not vanish with a shell exit.
set -uo pipefail

VOICE_VERSION="v0.1.12"          # pinned: a moving target on customer hardware is a support queue
VOICE_REPO="ashwanthbalakrishnan5/hermes-whatsapp-voice"
OPT_DIR="/opt/sudo-voice"
BIN="$OPT_DIR/bin/hermes-wa-voice"
STATE_DIR="/var/lib/sudo-voice"
ENV_FILE="/opt/sudo/voice.env"
STATUS_FILE="/var/lib/sudo-voice-status.json"
LOG="/var/log/sudo-voice-install.log"
BRIDGE_SRC="$OPT_DIR/voice-bridge.py"
BRIDGE_BIN="/usr/local/bin/sudo-voice-bridge.py"
CALL_BIN="/usr/local/bin/sudo-voice-call"

exec >> "$LOG" 2>&1
echo "=== install-sudo-voice $(date) ==="

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
  echo "[$1] $2"
}

fail() { set_status error "$1"; exit 1; }

# ── What hardware are we on? ────────────────────────────────────────────────
# Upstream publishes arm64 and amd64 only. Saying so plainly beats a download
# that 404s or, worse, an illegal-instruction crash on a 32-bit Pi.
set_status installing "Checking this device…"
case "$(uname -m)" in
  aarch64|arm64) ASSET="hermes-wa-voice_linux_arm64" ;;
  x86_64|amd64)  ASSET="hermes-wa-voice_linux_amd64" ;;
  *) fail "Voice calls need a 64-bit device (Raspberry Pi 4/5 or an x86 mini PC). This one is $(uname -m)." ;;
esac

ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 || fail "No internet. Connect to Wi-Fi and try again."

# ── The two things voice calls cannot work without ──────────────────────────
# Read from the file the dashboard wrote. Never echoed, never logged.
gemini=""
owner_number=""
if [ -f "$ENV_FILE" ]; then
  gemini="$(grep -E '^GEMINI_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
  owner_number="$(grep -E '^SUDO_VOICE_OWNER_NUMBER=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
fi
[ -n "$gemini" ] || fail "Voice calls need your own Google Gemini API key. Add one in the dashboard and run this again."
case "$owner_number" in
  +[0-9]*) : ;;
  *) fail "Add the phone number the agent should call, in international format (e.g. +15551234567)." ;;
esac

# ── Download the sidecar, checksum first ────────────────────────────────────
mkdir -p "$OPT_DIR/bin" "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

BASE="https://github.com/$VOICE_REPO/releases/download/$VOICE_VERSION"
if [ ! -x "$BIN" ]; then
  set_status installing "Downloading the voice engine (about 35 MB)…"
  tmp="$(mktemp -d)"
  curl -fsSL "$BASE/$ASSET" -o "$tmp/sidecar" \
    || { rm -rf "$tmp"; fail "Could not download the voice engine. Check the connection and retry."; }
  curl -fsSL "$BASE/SHA256SUMS" -o "$tmp/SHA256SUMS" \
    || { rm -rf "$tmp"; fail "Could not download the checksum file."; }

  expected="$(grep " $ASSET\$" "$tmp/SHA256SUMS" | awk '{print $1}' | head -1)"
  actual="$(sha256sum "$tmp/sidecar" | cut -d' ' -f1)"
  if [ -z "$expected" ] || [ "$actual" != "$expected" ]; then
    rm -rf "$tmp"
    fail "The download was corrupted (checksum mismatch). Nothing was installed."
  fi
  install -m 755 "$tmp/sidecar" "$BIN" || { rm -rf "$tmp"; fail "Could not install the voice engine."; }
  cp "$tmp/SHA256SUMS" "$STATE_DIR/SHA256SUMS" 2>/dev/null || true
  rm -rf "$tmp"
fi

# ── Bridge + CLI ────────────────────────────────────────────────────────────
[ -f "$BRIDGE_SRC" ] || fail "Bridge files are missing — update this device and try again."
cp "$BRIDGE_SRC" "$BRIDGE_BIN" || fail "Could not install the voice bridge."
chmod 755 "$BRIDGE_BIN"
if [ -f "$OPT_DIR/sudo-voice-call" ]; then
  cp "$OPT_DIR/sudo-voice-call" "$CALL_BIN" && chmod 755 "$CALL_BIN"
fi

# The token both halves share. Generated once; a re-install must not orphan a
# running bridge's token and silently 401 every call.
if [ ! -s "$STATE_DIR/hwv.token" ]; then
  ( umask 077; head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32 > "$STATE_DIR/hwv.token" )
fi
cp "$STATE_DIR/hwv.token" "$STATE_DIR/bridge.token" 2>/dev/null || true
chmod 600 "$STATE_DIR/hwv.token" "$STATE_DIR/bridge.token" 2>/dev/null || true

[ -f "$OPT_DIR/voice-briefing.md" ] || fail "Voice persona file is missing — update this device and try again."

# ── Units ───────────────────────────────────────────────────────────────────
# Same hardening shape as sudo-whatsapp-bridge.service: the state dir is the
# only writable place, and it is marked optional so a reset cannot wedge the
# unit in 226/NAMESPACE.
set_status installing "Starting the voice line…"

# Secrets belong in a root-only file, not in a unit that anyone can read with
# `systemctl cat`. The owner-editable file stays separate from the generated one
# so re-running the installer never eats a key the owner typed in.
( umask 077
  cat > "$STATE_DIR/sidecar.env" <<ENV
HWV_OWNER=$owner_number
HERMES_API_KEY=$(cat "$STATE_DIR/hwv.token")
ENV
) || fail "Could not write the sidecar environment file."
chmod 600 "$STATE_DIR/sidecar.env"

cat > /etc/systemd/system/sudo-voice-sidecar.service <<EOF
[Unit]
Description=sudo voice sidecar (WhatsApp calls)
After=network-online.target sudo-openclaw-gateway.service picoclaw-gateway.service
Wants=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=-$ENV_FILE
EnvironmentFile=-$STATE_DIR/sidecar.env
Environment=HWV_DATA_DIR=$STATE_DIR
Environment=HWV_LISTEN=127.0.0.1:8796
Environment=HWV_MODEL=gemini-3.8-live
Environment=HWV_ASSISTANT_NAME=sudo
Environment=HWV_VOICE_INSTRUCTIONS=You are the owner's sudo agent, speaking on a phone call. Say what is true: you cannot book, monitor or watch anything, and if you do not know something yet, say you are still checking. For any action or fact you do not already have, call ask_hermes.
Environment=HERMES_API_URL=http://127.0.0.1:8642
ExecStart=$BIN serve
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=-$STATE_DIR
ReadWritePaths=-/var/log

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/sudo-voice-bridge.service <<EOF
[Unit]
Description=sudo voice bridge (agent shim for voice calls)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=SUDO_VOICE_STATE=$STATE_DIR
Environment=BRIDGE_BRIEFING=$OPT_DIR/voice-briefing.md
ExecStart=/usr/bin/python3 $BRIDGE_BIN
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=-$STATE_DIR
ReadWritePaths=-/var/log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable sudo-voice-bridge.service >/dev/null 2>&1
systemctl restart sudo-voice-bridge.service || fail "The voice bridge would not start — see $LOG"

# ── Pairing, not starting ───────────────────────────────────────────────────
# The sidecar cannot serve until the bot's WhatsApp account is linked, and only
# the owner can do that (scan a QR from the bot's phone). So stop here in a
# state the dashboard knows how to act on, rather than crash-looping a service
# that has no session.
if [ ! -s "$STATE_DIR/whatsapp.db" ]; then
  systemctl disable --now sudo-voice-sidecar.service >/dev/null 2>&1
  set_status needs_pairing "Voice calls are installed. Link the bot's WhatsApp number to finish."
  echo "=== install-sudo-voice waiting for pairing $(date) ==="
  exit 0
fi

systemctl enable sudo-voice-sidecar.service >/dev/null 2>&1
systemctl restart sudo-voice-sidecar.service || fail "The voice line would not start — see $LOG"

# `enable --now` succeeds the moment systemd accepts the job, even if the
# process dies immediately. Watch it briefly so a crash shows up as an error
# instead of an endless "Starting…".
sleep 8
if ! systemctl is-active --quiet sudo-voice-sidecar.service; then
  journalctl -u sudo-voice-sidecar.service -n 20 --no-pager >> "$LOG" 2>&1 || true
  fail "The voice line keeps stopping — see $LOG"
fi

set_status running "Voice calls are live."
echo "=== install-sudo-voice done $(date) ==="
