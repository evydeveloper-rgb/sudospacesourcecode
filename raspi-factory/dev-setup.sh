#!/bin/bash
# dev-setup.sh — install Node.js + Claude Code on a Sudo dev unit.
#
# Run MANUALLY over SSH, never at boot:
#   ssh <user>@raspberrypi.local
#   sudo bash /boot/firmware/dev-setup.sh
#
# Staged onto the boot partition only by ./prepare-sd.sh --dev, so this
# never lands on a customer device. Needs working internet (finish Wi-Fi
# setup first) and pulls ~200MB.
set -euo pipefail

NODE_MAJOR="${NODE_MAJOR:-22}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo: sudo bash $0"
  exit 1
fi

# The Pi runs the agent as root; install for the login user so `claude`
# is on their PATH without sudo.
DEV_USER="$(getent passwd 1000 | cut -d: -f1)"
if [ -z "$DEV_USER" ]; then
  echo "ERROR: no uid-1000 user to install for"
  exit 1
fi

echo "=== Sudo dev setup — user: $DEV_USER ==="

if ! ping -c1 -W3 1.1.1.1 >/dev/null 2>&1; then
  echo "ERROR: no internet. Complete Wi-Fi setup first, then re-run."
  exit 1
fi

# ── Node.js ─────────────────────────────────────────────────────────────
# Debian's packaged node is far too old for Claude Code, so use NodeSource.
current_major="$(node -v 2>/dev/null | sed 's/^v\([0-9]*\).*/\1/' || echo 0)"
if [ "${current_major:-0}" -ge 18 ]; then
  echo "Node $(node -v) already present — skipping"
else
  echo "Installing Node.js ${NODE_MAJOR}.x..."
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
  apt-get install -y nodejs
fi

# ── Claude Code ─────────────────────────────────────────────────────────
# Global npm dir under the user's home avoids sudo-owned files in ~/.npm.
NPM_PREFIX="/home/${DEV_USER}/.npm-global"
sudo -u "$DEV_USER" mkdir -p "$NPM_PREFIX"
sudo -u "$DEV_USER" npm config set prefix "$NPM_PREFIX"

PROFILE="/home/${DEV_USER}/.bashrc"
if ! grep -q ".npm-global/bin" "$PROFILE" 2>/dev/null; then
  echo "export PATH=\"\$HOME/.npm-global/bin:\$PATH\"" >> "$PROFILE"
fi

echo "Installing Claude Code..."
sudo -u "$DEV_USER" env PATH="$NPM_PREFIX/bin:$PATH" \
  npm install -g @anthropic-ai/claude-code

# ── git, so changes can go back to the repo ─────────────────────────────
command -v git >/dev/null 2>&1 || apt-get install -y git

cat <<EOF

=== done ===
Node:        $(node -v 2>/dev/null || echo 'not found')
Claude Code: ${NPM_PREFIX}/bin/claude

Open a NEW shell (or: source ~/.bashrc), then:
  claude

The live files the agent actually uses are:
  /opt/sudo-dashboard      dashboard (server.py, index.html, app.css)
  /opt/wifi-setup          Wi-Fi captive portal
  /opt/picoclaw            config template + workspace templates
  /opt/sudo/config.json    this device's settings (0600)
  /opt/sudo/agent-workspace  rendered SOUL.md / AGENTS.md / memory

Editing those changes THIS device only. To keep a change, copy it back
into the sudosourcecode repo and commit — reflashing overwrites /opt.

After editing dashboard or picoclaw files:
  sudo systemctl restart sudo-dashboard
  sudo /usr/local/bin/sudo-configure-picoclaw.sh
EOF
