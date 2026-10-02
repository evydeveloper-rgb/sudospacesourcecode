#!/bin/bash
# install-devtools.sh — install Node.js + Claude Code on demand.
#
# Triggered from the dashboard (Settings -> Developer access), never at boot.
# Writes progress to a status file the dashboard polls, so the user can watch
# it from their phone instead of staring at a terminal.
#
# Deliberately NOT set -e: every failure should land in the status file as a
# readable message rather than killing the script silently.
set -uo pipefail

STATUS_BASE="/var/lib/sudo-devtools-status"
LOG="/var/log/sudo-devtools.log"
NODE_MAJOR="${NODE_MAJOR:-22}"

# Which CLI to install. Adding another is a one-line entry here.
TOOL="${1:-claude}"
case "$TOOL" in
  claude) NPM_PKG="@anthropic-ai/claude-code"; BIN_NAME="claude"; LABEL="Claude Code" ;;
  codex)  NPM_PKG="@openai/codex";             BIN_NAME="codex";  LABEL="Codex" ;;
  *) echo "unknown tool: $TOOL"; exit 1 ;;
esac

STATUS="${STATUS_BASE}-${TOOL}"
exec >> "$LOG" 2>&1
echo "=== install-devtools $(date) ==="

set_status() {
  # $1 state (installing|done|failed)  $2 human message
  python3 - "$1" "$2" "$STATUS" <<'PY'
import json, sys, time, os
state, message, path = sys.argv[1:4]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"state": state, "message": message, "updated": int(time.time())}, f)
os.chmod(path, 0o644)
PY
  echo "[$1] $2"
}

fail() { set_status failed "$1"; exit 1; }

DEV_USER="$(getent passwd 1000 | cut -d: -f1)"
DEV_HOME="$(getent passwd 1000 | cut -d: -f6)"
[ -n "$DEV_USER" ] || fail "No user account found on this device"

set_status installing "Checking internet…"
ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 || fail "No internet. Connect to Wi-Fi and try again."

# ── Node.js ─────────────────────────────────────────────────────────────
current="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/')"
if [ -n "${current:-}" ] && [ "${current:-0}" -ge 18 ] 2>/dev/null; then
  set_status installing "Node $(node -v) already installed, skipping…"
else
  set_status installing "Installing Node.js (this is the slow part, ~2 min)…"
  # Debian's packaged node is far too old for Claude Code.
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    || fail "Could not reach the Node.js repository"
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs \
    || fail "Node.js installation failed — see /var/log/sudo-devtools.log"
fi
command -v node >/dev/null 2>&1 || fail "Node installed but not on PATH"

# ── git (so work can go back to the repo) ──────────────────────────────
command -v git >/dev/null 2>&1 || {
  set_status installing "Installing git…"
  DEBIAN_FRONTEND=noninteractive apt-get install -y git || true
}

# ── Claude Code ─────────────────────────────────────────────────────────
# Install under the user's own npm prefix so it runs without sudo and does
# not leave root-owned files in ~/.npm.
set_status installing "Installing ${LABEL}…"
NPM_PREFIX="${DEV_HOME}/.npm-global"
sudo -u "$DEV_USER" mkdir -p "$NPM_PREFIX" || fail "Could not create $NPM_PREFIX"
sudo -u "$DEV_USER" npm config set prefix "$NPM_PREFIX" || fail "npm config failed"

PROFILE="${DEV_HOME}/.bashrc"
if ! grep -q ".npm-global/bin" "$PROFILE" 2>/dev/null; then
  echo 'export PATH="$HOME/.npm-global/bin:$PATH"' >> "$PROFILE"
  chown "${DEV_USER}:${DEV_USER}" "$PROFILE" 2>/dev/null || true
fi

sudo -u "$DEV_USER" env PATH="${NPM_PREFIX}/bin:${PATH}" HOME="$DEV_HOME" \
  npm install -g "$NPM_PKG" \
  || fail "${LABEL} installation failed — see /var/log/sudo-devtools.log"

[ -x "${NPM_PREFIX}/bin/${BIN_NAME}" ] || fail "${LABEL} installed but the command is missing"

set_status done "${LABEL} is ready."
echo "=== install-devtools complete $(date) ==="
