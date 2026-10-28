#!/bin/bash
# tailscale.sh — reach sudo from anywhere through the owner's own Tailscale.
#
# The quick tunnel's public address changes on every restart. With Tailscale
# the device joins the owner's own private network (their account, not ours)
# and keeps one address, https://<name>.<their-tailnet>.ts.net, that their
# phone reaches with the Tailscale app. Optionally (Funnel) the same address
# also opens without the app, from anywhere. Sudo runs nothing in between.
#
#   tailscale.sh connect        install if needed, start, wait for the sign-in
#   tailscale.sh serve          give the dashboard its https address (after sign-in)
#   tailscale.sh funnel on|off  also reachable without the Tailscale app
#   tailscale.sh disconnect     sign out and stop
#
# Progress for the dashboard: /var/lib/sudo-tailscale-status.json (the
# sign-in link, and any "turn this on in Tailscale" link it gives).
set -uo pipefail

STATUS=/var/lib/sudo-tailscale-status.json
LOG=/var/log/sudo-tailscale.log
exec >>"$LOG" 2>&1
echo "=== tailscale $* $(date -Is) ==="

status() {
  python3 -c 'import json,sys,time; json.dump({"state": sys.argv[1], "message": sys.argv[2], "link": sys.argv[3], "at": int(time.time())}, open(sys.argv[4], "w"))' \
    "$1" "$2" "${3:-}" "$STATUS"
}
fail() { status failed "$1" "${2:-}"; echo "FAILED: $1"; exit 1; }

# A "please enable this in your Tailscale admin" link Tailscale prints when a
# feature (HTTPS certificates, Funnel) is off for the tailnet.
admin_link() { grep -oE 'https://login\.tailscale\.com/f/[A-Za-z0-9/?=&._-]+' "$1" | head -1; }

connect() {
  if ! command -v tailscale >/dev/null 2>&1; then
    status installing "Installing Tailscale…"
    # Tailscale's own installer: adds their package repository and installs.
    curl -fsSL --max-time 120 https://tailscale.com/install.sh -o /tmp/tailscale-install.sh \
      || fail "Could not download Tailscale — check the internet connection"
    sh /tmp/tailscale-install.sh || fail "Tailscale did not install"
    rm -f /tmp/tailscale-install.sh
  fi
  systemctl enable --now tailscaled
  status signin "Waiting for you to sign in to Tailscale…"
  # Blocks until the owner has signed in (or gives up after ten minutes); the
  # dashboard reads the sign-in link from `tailscale status --json` meanwhile.
  if timeout 600 tailscale up --hostname="$(hostname)" --accept-dns=false; then
    serve
  else
    fail "Sign-in did not finish. Try again."
  fi
}

serve() {
  local out=/tmp/sudo-tailscale-serve.out
  if timeout 30 tailscale serve --bg --https=443 http://127.0.0.1:${SUDO_DASH_PORT:-80} >"$out" 2>&1 </dev/null; then
    status ready "Connected."
  else
    # Usually: HTTPS certificates are off for this tailnet; Tailscale prints
    # the page that turns them on.
    cat "$out"
    status needs_https "Turn on HTTPS for your Tailscale network, then press Try again." "$(admin_link "$out")"
  fi
}

funnel() {
  local out=/tmp/sudo-tailscale-funnel.out
  if [ "${1:-}" = on ]; then
    if timeout 30 tailscale funnel --bg --https=443 http://127.0.0.1:${SUDO_DASH_PORT:-80} >"$out" 2>&1 </dev/null; then
      status ready "Connected. Also reachable without the Tailscale app."
    else
      cat "$out"
      status needs_funnel "Allow Funnel for your Tailscale network, then switch this on again." "$(admin_link "$out")"
    fi
  else
    timeout 30 tailscale funnel --https=443 off >/dev/null 2>&1 || true
    serve
  fi
}

disconnect() {
  command -v tailscale >/dev/null 2>&1 || { rm -f "$STATUS"; return 0; }
  timeout 30 tailscale serve reset >/dev/null 2>&1 || true
  timeout 30 tailscale logout >/dev/null 2>&1 || true
  systemctl disable --now tailscaled 2>/dev/null || true
  rm -f "$STATUS"
  echo "disconnected"
}

case "${1:-}" in
  connect)    connect ;;
  serve)      serve ;;
  funnel)     funnel "${2:-}" ;;
  disconnect) disconnect ;;
  *) echo "usage: tailscale.sh connect|serve|funnel on|off|disconnect"; exit 2 ;;
esac
