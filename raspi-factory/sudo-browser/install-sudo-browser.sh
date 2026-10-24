#!/usr/bin/env bash
# Install the browser-login worker: a private Chromium + Playwright on the device.
#
# Why a separate installer: this is the one optional piece that is genuinely big
# (~450 MB with the browser), and most owners will never need it. The dashboard
# asks before calling this, and says what it costs in disk.
#
# What it does NOT do:
#   * no apt packages unless --with-deps is passed (that needs root and pulls
#     system libraries; on a stock Pi OS image they are already there)
#   * no telemetry, no downloads at runtime beyond the browser once
#   * no service: the worker runs on demand, when the dashboard asks
#
# Usage:
#   sudo ./install-sudo-browser.sh                # worker + chromium
#   sudo ./install-sudo-browser.sh --with-deps    # also install system libs
#   sudo ./install-sudo-browser.sh --worker-only  # no chromium (fast, for dev)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${SUDO_BROWSER_ROOT:-/opt/sudo-browser}"
BIN_DIR="/usr/local/bin"
STATE_DIR="/var/lib/sudo-browser"
STATUS_FILE="$STATE_DIR/install-status.json"
WITH_DEPS=0
WORKER_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --with-deps) WITH_DEPS=1 ;;
    --worker-only) WORKER_ONLY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
write_status() {
  local state="$1" message="$2"
  mkdir -p "$STATE_DIR"
  printf '{"state":"%s","message":"%s","updated":%s}\n' \
    "$state" "${message//\"/\'}" "$(date +%s000)" > "$STATUS_FILE"
  chmod 644 "$STATUS_FILE"
}

case "$(uname -m)" in
  aarch64|arm64|x86_64|amd64) ;;
  *) write_status error "unsupported architecture $(uname -m)"; say "32-bit / unknown ARM is not supported: Playwright has no browser build for it."; exit 1 ;;
esac

write_status installing "Installing the browser worker…"

# 1. code
mkdir -p "$DEST"
install -m 0755 "$HERE/browser-worker.py" "$DEST/browser-worker.py"
install -m 0644 "$HERE/sites.json" "$DEST/sites.json"
install -m 0755 "$HERE/sudo-browser" "$BIN_DIR/sudo-browser"

# 2. state dirs, owned by whoever runs the dashboard
mkdir -p "$STATE_DIR/profiles"
chmod 700 "$STATE_DIR" "$STATE_DIR/profiles"
if id sudoagent >/dev/null 2>&1; then
  chown -R sudoagent:sudoagent "$STATE_DIR" || true
fi

# 3. python venv + playwright
say "creating venv in $DEST/venv"
python3 -m venv "$DEST/venv" >/dev/null
"$DEST/venv/bin/pip" install --quiet --upgrade pip >/dev/null
say "installing playwright (this is the slow bit)"
"$DEST/venv/bin/pip" install --quiet playwright

if [ "$WORKER_ONLY" -eq 0 ]; then
  if [ "$WITH_DEPS" -eq 1 ]; then
    "$DEST/venv/bin/playwright" install --with-deps chromium
  else
    "$DEST/venv/bin/playwright" install chromium
  fi
fi

# 4. prove it can import, not just that files landed
if "$DEST/venv/bin/python3" -c "import playwright" 2>/dev/null; then
  write_status ready "Browser worker installed."
  say "done. Ask sudo to check a site from Connectors."
else
  write_status error "playwright import failed"
  say "install finished but playwright will not import — check the log above."
  exit 1
fi
