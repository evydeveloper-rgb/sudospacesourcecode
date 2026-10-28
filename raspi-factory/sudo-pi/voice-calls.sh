#!/bin/bash
# voice-calls.sh — WhatsApp voice calls with the agent (call it, or it calls you).
#
# Uses hermes-whatsapp-voice (MIT, github.com/ashwanthbalakrishnan5/
# hermes-whatsapp-voice): a whatsapp-rust sidecar that answers the owner's
# calls with Gemini Live and hands real work to the agent through
# voice-bridge.py. The sidecar is one more linked device on the agent's OWN
# WhatsApp number (it cannot be the owner's own number: you cannot call
# yourself), and it only ever answers or rings the owner's number.
#
#   voice-calls.sh install           download the pinned sidecar, write the units
#   voice-calls.sh pair [+number]    link it to the agent's WhatsApp (QR, or a pair
#                                    code for that number); starts calls once linked
#   voice-calls.sh start | stop      run or stop the two services
#   voice-calls.sh remove            stop, unlink, delete everything
#
# Progress for the dashboard: /var/lib/sudo-voice-status.json. Needs a Gemini
# API key (config: provider_keys.gemini, or voice_gemini_key).
set -uo pipefail

VERSION="0.2.1"
REPO="ashwanthbalakrishnan5/hermes-whatsapp-voice"
# Pinned to this release (the project's own sidecar/release-checksums.txt).
declare -A SUMS=(
  [linux_arm64]="c71a9cd6a41a8687aa030b537a844d000a60e04feee8c952927982f99e55e458"
  [linux_amd64]="02779e77e7a1079e7dcd81cb22f2cb1e750c42ed3e4eebd9515a2a41d9ed36dc"
)
BIN_DIR=/opt/sudo-voice
BIN="$BIN_DIR/hermes-wa-voice"
DATA=/var/lib/sudo-voice             # the sidecar's WhatsApp link lives here
ENV_FILE=/etc/sudo/voice.env
STATUS=/var/lib/sudo-voice-status.json
LOG=/var/log/sudo-voice.log
exec >>"$LOG" 2>&1
echo "=== voice-calls $* $(date -Is) ==="

status() {
  python3 -c 'import json,sys,time; json.dump({"state": sys.argv[1], "message": sys.argv[2], "at": int(time.time())}, open(sys.argv[3], "w"))' \
    "$1" "$2" "$STATUS"
}
fail() { status failed "$1"; echo "FAILED: $1"; exit 1; }

write_env() {
  # Secrets and settings from Sudo's own config; root only.
  python3 - "$ENV_FILE" <<'PY' || return 1
import json, os, secrets, sys
path = sys.argv[1]
c = json.load(open("/opt/sudo/config.json", encoding="utf-8"))
key = ((c.get("provider_keys") or {}).get("gemini") or c.get("voice_gemini_key") or "").strip()
owner = (c.get("whatsapp_owner_number") or (c.get("whatsapp_numbers") or {}).get("owner") or "").strip()
if not key or not owner:
    sys.exit("missing " + ("Gemini key" if not key else "owner number"))
old = {}
if os.path.isfile(path):
    for line in open(path, encoding="utf-8"):
        k, _, v = line.strip().partition("=")
        old[k] = v
langs = {"es": "es-ES", "zh": "cmn-CN", "ar": "ar-XA", "en": "en-US"}
env = {
    "GEMINI_API_KEY": key,
    "HWV_OWNER": owner,
    "HWV_ASSISTANT_NAME": (c.get("agent_name") or "sudo").strip()[:32],
    "HWV_LANGUAGES": langs.get(c.get("agent_language") or "", ""),
    "HWV_DATA_DIR": "/var/lib/sudo-voice",
    "HWV_LISTEN": "127.0.0.1:8796",
    "HWV_TOKEN": old.get("HWV_TOKEN") or secrets.token_urlsafe(24),
    "HERMES_API_URL": "http://127.0.0.1:8642",
    "HERMES_API_KEY": old.get("HERMES_API_KEY") or secrets.token_urlsafe(24),
    "HWV_MAX_DURATION": "15m",
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write("".join(f"{k}={v}\n" for k, v in env.items()))
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}

install_sidecar() {
  local arch asset sum tmp
  case "$(uname -m)" in aarch64|arm64) arch=linux_arm64 ;; x86_64|amd64) arch=linux_amd64 ;; *) fail "No voice build for this processor" ;; esac
  sum="${SUMS[$arch]}"
  if [ -x "$BIN" ] && [ "$(sha256sum "$BIN" | cut -d' ' -f1)" = "$sum" ]; then
    echo "sidecar $VERSION already installed"
  else
    status installing "Downloading voice calls (about 35 MB)…"
    mkdir -p "$BIN_DIR"
    tmp="$BIN.download"
    curl -fsSL --max-time 300 -o "$tmp" "https://github.com/$REPO/releases/download/v$VERSION/hermes-wa-voice_$arch" \
      || fail "Could not download voice calls — check the internet connection"
    [ "$(sha256sum "$tmp" | cut -d' ' -f1)" = "$sum" ] || { rm -f "$tmp"; fail "The download did not match its checksum, so it was not installed"; }
    chmod 755 "$tmp" && mv "$tmp" "$BIN"
  fi
  install -m 0755 /usr/local/bin/sudo-voice-bridge.py "$BIN_DIR/voice-bridge.py" 2>/dev/null \
    || install -m 0755 "$(dirname "$0")/voice-bridge.py" "$BIN_DIR/voice-bridge.py"
  write_env || fail "Add a Gemini API key first (voice calls use Google's Gemini Live)"
  # The sidecar runs unprivileged; its WhatsApp link is in its StateDirectory.
  cat > /etc/systemd/system/sudo-voice.service <<EOF
[Unit]
Description=Sudo voice calls (WhatsApp + Gemini Live)
After=network-online.target sudo-voice-bridge.service
Wants=network-online.target

[Service]
EnvironmentFile=$ENV_FILE
ExecStart=$BIN serve
DynamicUser=yes
StateDirectory=sudo-voice
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/systemd/system/sudo-voice-bridge.service <<EOF
[Unit]
Description=Sudo voice calls: the bridge to the agent
After=network-online.target sudo-openclaw-gateway.service

[Service]
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $BIN_DIR/voice-bridge.py
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  status installed "Voice calls installed. Link them to your agent's WhatsApp next."
}

pair() {
  [ -x "$BIN" ] || install_sidecar
  write_env || fail "Add a Gemini API key first (voice calls use Google's Gemini Live)"
  systemctl stop sudo-voice.service 2>/dev/null || true
  rm -f "$DATA/pair-qr.svg" /var/lib/private/sudo-voice/pair-qr.svg
  status pairing "Waiting for the agent's phone to link…"
  # Same user and state directory as the service, so the link it makes is the
  # one the service then uses.
  local args=(pair)
  [ -n "${1:-}" ] && args+=(--phone "$1")
  if systemd-run --quiet --collect --wait --pipe --unit=sudo-voice-pair -p DynamicUser=yes \
       -p StateDirectory=sudo-voice -p EnvironmentFile="$ENV_FILE" -p RuntimeMaxSec=300 \
       "$BIN" "${args[@]}" | tee /var/lib/sudo-voice-pair.out; then
    if grep -q "Linked" /var/lib/sudo-voice-pair.out; then
      start_calls
      return 0
    fi
  fi
  fail "The phone did not link in time. Try again."
}

start_calls() {
  systemctl enable --now sudo-voice-bridge.service sudo-voice.service
  status ready "Voice calls are on."
  /usr/local/bin/sudo-configure-agent.sh >/dev/null 2>&1 || true   # tell the agent it can call
}

stop_calls() {
  systemctl disable --now sudo-voice.service sudo-voice-bridge.service 2>/dev/null || true
  status stopped "Voice calls are off."
  [ "${1:-}" = quiet ] || /usr/local/bin/sudo-configure-agent.sh >/dev/null 2>&1 || true
}

remove_all() {
  stop_calls quiet
  rm -f /etc/systemd/system/sudo-voice.service /etc/systemd/system/sudo-voice-bridge.service "$ENV_FILE"
  rm -rf "$BIN_DIR" /var/lib/private/sudo-voice "$DATA" /var/lib/sudo-voice-pair.out
  systemctl daemon-reload
  rm -f "$STATUS"
  /usr/local/bin/sudo-configure-agent.sh >/dev/null 2>&1 || true
  echo "voice calls removed (the linked device is gone; remove it from the agent's phone too)"
}

case "${1:-}" in
  install) install_sidecar ;;
  pair)    pair "${2:-}" ;;
  start)   write_env && start_calls ;;
  stop)    stop_calls ;;
  remove)  remove_all ;;
  *) echo "usage: voice-calls.sh install|pair [+number]|start|stop|remove"; exit 2 ;;
esac
