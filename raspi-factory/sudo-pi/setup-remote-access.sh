#!/bin/bash
# Cloudflare Quick Tunnel — public HTTPS without owning a domain.
# No API token. No TUNNEL_BASE_DOMAIN. No Cloudflare account required on device.
#
# URL: https://*.trycloudflare.com  (may change if tunnel restarts)
# User enables via onboarding toggle.
#
# Usage: sudo-remote-access.sh enable|disable|run
#
set -euo pipefail

LOG="/var/log/sudo-remote.log"
PUBLIC_URL_FILE="/etc/sudo/public_url"
REMOTE_MARKER="/var/lib/sudo-remote-enabled"
CLOUDFLARED="/usr/local/bin/cloudflared"
DASHBOARD_PORT="${SUDO_DASH_PORT:-80}"
# Quick tunnels fail if a named-tunnel config.yml is present in ~/.cloudflared
CF_HOME="/var/lib/sudo-cloudflared-quick"
export HOME="$CF_HOME"
export TUNNEL_ORIGIN_CERT=""

exec >>"$LOG" 2>&1

log() { echo "$(date -Iseconds) $*"; }

ensure_binary() {
  [ -x "$CLOUDFLARED" ] || { log "ERROR: cloudflared missing at $CLOUDFLARED"; exit 1; }
}

wait_for_internet() {
  for _ in $(seq 1 30); do
    ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

enable_remote() {
  [ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
  [ -f /var/lib/wifi-setup-configured ] || { echo "WiFi not configured"; exit 1; }
  ensure_binary
  wait_for_internet || { echo "no internet"; exit 1; }

  mkdir -p "$CF_HOME"
  chmod 700 "$CF_HOME"
  # Ensure no leftover named-tunnel config blocks quick mode
  rm -f "$CF_HOME/.cloudflared/config.yml" 2>/dev/null || true

  touch "$REMOTE_MARKER"
  rm -f "$PUBLIC_URL_FILE"
  systemctl daemon-reload
  systemctl enable sudo-remote-access.service 2>/dev/null || true
  systemctl restart sudo-remote-access.service
  log "remote access enable requested (quick tunnel)"
}

disable_remote() {
  [ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
  rm -f "$REMOTE_MARKER" "$PUBLIC_URL_FILE"
  systemctl disable --now sudo-remote-access.service 2>/dev/null || true
  log "remote access disabled"
}

run_tunnel() {
  ensure_binary
  [ -f "$REMOTE_MARKER" ] || { log "remote not enabled"; exit 0; }

  wait_for_internet || { log "waiting for internet"; sleep 10; exit 1; }

  mkdir -p "$CF_HOME"
  chmod 700 "$CF_HOME"
  rm -f "$PUBLIC_URL_FILE"

  log "starting quick tunnel → http://127.0.0.1:${DASHBOARD_PORT}"

  # cloudflared prints trycloudflare.com URL to stdout/stderr
  "$CLOUDFLARED" tunnel --no-autoupdate --url "http://127.0.0.1:${DASHBOARD_PORT}" 2>&1 | while IFS= read -r line; do
    log "$line"
    url="$(printf '%s' "$line" | grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | head -1 || true)"
    if [ -n "${url:-}" ]; then
      printf '%s\n' "$url" > "$PUBLIC_URL_FILE"
      chmod 644 "$PUBLIC_URL_FILE"
      log "public URL saved: $url"
    fi
  done

  # If cloudflared exits, clear URL so UI shows "starting" again
  rm -f "$PUBLIC_URL_FILE"
  log "quick tunnel exited"
  exit 1
}

case "${1:-}" in
  enable)  enable_remote ;;
  disable) disable_remote ;;
  run)     run_tunnel ;;
  *)
    echo "Usage: sudo-remote-access.sh enable|disable|run"
    exit 1
    ;;
esac
