#!/bin/bash
# sudo-update.sh — update this device in place, no SD-card reflash.
#
# The problem this solves: the only way to move a device to a new build used
# to be pulling the card, reflashing and putting it back -- which wipes
# nothing if you are careful, but requires hands on the hardware and a second
# machine. For a box sitting on someone's shelf that is a non-starter.
#
# How it works. Releases are published on GitHub from the public source repo
# (sudospacesourcecode). A release carries a tarball of the factory bundle and
# a matching .sha256. This script:
#
#   1. asks GitHub for the latest release tag and compares it to the version
#      recorded on the device
#   2. downloads the tarball and its checksum, and refuses to touch anything
#      unless the bytes match
#   3. copies the app directories into place -- /opt/sudo-dashboard,
#      /opt/wifi-setup, /opt/picoclaw, the /usr/local/bin helper scripts
#   4. re-runs configure-picoclaw.sh, which rebuilds the agent's config from
#      the (updated) template + the owner's own saved choices
#   5. restarts the services
#
# What it deliberately does NOT touch -- the owner's data:
#   /opt/sudo/config.json            keys, names, model, every toggle
#   /opt/sudo/agent-workspace/       the agent's memory and session history
#   /etc/sudo/*                      device secrets, dashboard password
#   /opt/sudo/whatsapp-auth/         the paired WhatsApp session
#   /etc/wifi-* , NetworkManager     Wi-Fi, country, hostname
# An update changes the app. It never changes the person using it.
#
# Modes:
#   sudo-update.sh                 check + apply if a newer version exists
#   sudo-update.sh --check         check only, write status, change nothing
#   sudo-update.sh --apply         apply the latest release even if we're unsure
#   sudo-update.sh --local <bundle> apply a tarball already on disk (dev loop)
#
# The install also drops a copy of whatever bundle it was built from at
# /usr/local/bin/sudo-factory.tar.gz (with a .sha256 beside it). That is the
# baseline this script compares against, and it is what makes a hands-free
# local dev loop possible: flash once, then keep applying new builds (and
# rolling back) over Wi-Fi, without ever touching the SD card again.
#
# Best-effort by design: every failure path writes a status file the dashboard
# can show and then exits non-zero, rather than leaving a half-updated device
# with no explanation.
set -uo pipefail

REPO_SLUG="${SUDO_UPDATE_REPO:-evydeveloper-rgb/sudospacesourcecode}"
# Release tag prefix, so this repo can later host releases for more than one
# product without them colliding on "latest".
TAG_PREFIX="${SUDO_UPDATE_TAG_PREFIX:-raspi-factory-v}"
RELEASES_API="https://api.github.com/repos/${REPO_SLUG}/releases/latest"
VERSION_FILE=/opt/sudo/version
STATUS_FILE=/var/lib/sudo-update-status.json
STAGE=/var/tmp/sudo-update
LOG=/var/log/sudo-update.log

# The bundle the device was built from, kept on-device so an update can be
# applied and rolled back without a network round-trip.
BUNDLE_DIR=/usr/local/bin
BUNDLE_TGZ="${BUNDLE_DIR}/sudo-factory.tar.gz"
BUNDLE_SHA="${BUNDLE_DIR}/sudo-factory.sha256"

# Directories replaced wholesale by an update. Everything NOT in this list is
# either preserved user data or a system unit owned by install-factory.
DASHBOARD_DIR=/opt/sudo-dashboard
WIFI_DIR=/opt/wifi-setup
PICOCLAW_DIR=/opt/picoclaw
WHATSAPP_DIR=/opt/whatsapp-bridge
BIN_DIR=/usr/local/bin

MODE="auto"
LOCAL_BUNDLE=""
case "${1:-}" in
  --check) MODE="check" ;;
  --apply) MODE="apply" ;;
  --local) MODE="local"; LOCAL_BUNDLE="${2:-}" ;;
  "")      MODE="auto" ;;
  *) echo "usage: sudo-update.sh [--check|--apply|--local <bundle.tar.gz>]" >&2; exit 2 ;;
esac
if [ "$MODE" = "local" ] && [ -z "$LOCAL_BUNDLE" ]; then
  echo "usage: sudo-update.sh --local <bundle.tar.gz>" >&2
  exit 2
fi

log() { echo "$(date -Is) $*" >> "$LOG"; }

write_status() {
  # state, message, current, latest
  python3 - "$STATUS_FILE" "$1" "$2" "${3:-}" "${4:-}" <<'PY'
import json, os, sys, time
path, state, message, current, latest = sys.argv[1:6]
data = {
    "state": state,
    "message": message,
    "current": current or None,
    "latest": latest or None,
    "at": int(time.time()),
}
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f)
os.replace(tmp, path)
PY
}

current_version() {
  if [ -f "$VERSION_FILE" ]; then
    tr -d ' \t\r\n' < "$VERSION_FILE"
  else
    echo "0.0.0"
  fi
}

# Newer-than test that works for plain dotted versions (1.2.10 > 1.2.9) without
# pulling in a version-compare tool that may not exist on a minimal image.
is_newer() {
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$2" ] && [ "$1" != "$2" ]
}

echo "=== sudo-update $(date -Is) mode=$MODE ===" >> "$LOG"

CURRENT="$(current_version)"
write_status "checking" "Checking for updates…" "$CURRENT" ""

# ── Resolve what to apply ───────────────────────────────────────────────────
# Three sources feed the same apply path below: a local tarball (--local), the
# latest GitHub release (normal), or nothing (already current). Set TGZ/SHA and
# LATEST, then fall through to apply.
TGZ=""
SHA_FILE=""
LATEST=""

if [ "$MODE" = "local" ]; then
  if [ ! -f "$LOCAL_BUNDLE" ]; then
    write_status "error" "No bundle at ${LOCAL_BUNDLE}." "$CURRENT" ""
    exit 1
  fi
  mkdir -p "$STAGE"
  cp -f "$LOCAL_BUNDLE" "$STAGE/sudo-factory.tar.gz"
  TGZ="$STAGE/sudo-factory.tar.gz"
  # A checkable version: prefer a sibling VERSION next to the bundle.
  ver_file="$(dirname "$LOCAL_BUNDLE")/VERSION"
  [ -f "$ver_file" ] && LATEST="$(tr -d ' \t\r\n' < "$ver_file")"
  [ -z "$LATEST" ] && LATEST="$CURRENT"
  log "local apply from $LOCAL_BUNDLE (version ${LATEST})"
else
  if ! command -v curl >/dev/null 2>&1; then
    write_status "error" "curl is missing on this device — cannot check for updates." "$CURRENT" ""
    exit 1
  fi

  release_json="$(curl -fsSL --max-time 20 \
    -H "Accept: application/vnd.github+json" \
    -H "User-Agent: sudo-updater" \
    "$RELEASES_API" 2>>"$LOG")"
  if [ -z "$release_json" ]; then
    # No releases published yet, or offline. Not an error worth alarming about:
    # the device simply stays on its current version.
    write_status "idle" "No published releases found — staying on ${CURRENT}." "$CURRENT" ""
    log "no release metadata"
    exit 0
  fi

  read -r LATEST TARBALL SHA_URL <<EOF
$(printf '%s' "$release_json" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit
tag = (d.get("tag_name") or "")
# Strip the product prefix if present, then a bare leading v.
if tag.startswith("raspi-factory-v"):
    tag = tag[len("raspi-factory-v"):]
elif tag.startswith("v"):
    tag = tag[1:]
tarball = ""
sha = ""
for a in d.get("assets", []):
    name = a.get("name", "")
    if name == "sudo-factory.tar.gz":
        tarball = a.get("browser_download_url", "")
    elif name == "sudo-factory.tar.gz.sha256":
        sha = a.get("browser_download_url", "")
print(tag, tarball, sha)
')
EOF

  if [ -z "${LATEST:-}" ]; then
    write_status "error" "Release found but no version tag — skipping." "$CURRENT" ""
    exit 1
  fi

  if [ ! "$(printf '%s' "$LATEST" | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$')" ]; then
    write_status "error" "Unrecognised version '${LATEST}' — skipping." "$CURRENT" "$LATEST"
    exit 1
  fi

  # Newer than what we run? ("--apply" skips the question and forces it.)
  if [ "$MODE" != "apply" ]; then
    if ! is_newer "$CURRENT" "$LATEST"; then
      write_status "up-to-date" "Already up to date (${CURRENT})." "$CURRENT" "$LATEST"
      log "up to date ($CURRENT)"
      exit 0
    fi
  fi

  if [ "$MODE" = "check" ]; then
    write_status "available" "Version ${LATEST} is available." "$CURRENT" "$LATEST"
    log "update available: $CURRENT -> $LATEST"
    exit 0
  fi

  if [ -z "${TARBALL:-}" ]; then
    write_status "error" "Release ${LATEST} has no bundle attached — cannot update." "$CURRENT" "$LATEST"
    exit 1
  fi

  # ── Download ──────────────────────────────────────────────────────────────
  write_status "downloading" "Downloading ${LATEST}…" "$CURRENT" "$LATEST"
  rm -rf "$STAGE"
  mkdir -p "$STAGE"
  TGZ="$STAGE/sudo-factory.tar.gz"

  if ! curl -fsSL --max-time 120 -H "User-Agent: sudo-updater" -o "$TGZ" "$TARBALL" 2>>"$LOG"; then
    write_status "error" "Download failed — check the device's internet connection." "$CURRENT" "$LATEST"
    exit 1
  fi

  # The checksum is the whole safety net: a truncated download or a tampered
  # bundle must never be applied. If we cannot get the expected hash, we stop.
  expected=""
  if [ -n "${SHA_URL:-}" ]; then
    expected="$(curl -fsSL --max-time 20 -H "User-Agent: sudo-updater" "$SHA_URL" 2>>"$LOG" | awk '{print $1}' | head -1)"
    [ -n "$expected" ] && printf '%s  sudo-factory.tar.gz\n' "$expected" > "$STAGE/sudo-factory.sha256"
  fi
  if [ -z "$expected" ]; then
    write_status "error" "Release ${LATEST} has no checksum — refusing to update." "$CURRENT" "$LATEST"
    exit 1
  fi
  actual="$(sha256sum "$TGZ" | awk '{print $1}')"
  if [ "$actual" != "$expected" ]; then
    write_status "error" "Checksum mismatch — download corrupt, update aborted." "$CURRENT" "$LATEST"
    log "sha mismatch expected=$expected actual=$actual"
    exit 1
  fi
  SHA_FILE="$STAGE/sudo-factory.sha256"
fi

# ── Unpack ──────────────────────────────────────────────────────────────────
write_status "applying" "Installing ${LATEST}…" "$CURRENT" "$LATEST"
extract="$STAGE/extract"
mkdir -p "$extract"
if ! tar -xzf "$TGZ" -C "$extract" 2>>"$LOG"; then
  write_status "error" "Could not unpack the update bundle — nothing changed." "$CURRENT" "$LATEST"
  exit 1
fi

# The bundle roots at a single directory (raspi-factory/), whatever it is
# called; find it so a rename upstream does not break the updater.
root="$(find "$extract" -mindepth 1 -maxdepth 1 -type d | head -1)"
if [ -z "$root" ]; then
  write_status "error" "Update bundle was empty — nothing changed." "$CURRENT" "$LATEST"
  exit 1
fi

# ── Apply (apps only; owner data untouched) ─────────────────────────────────
apply_dir() {
  # $1 = source under bundle, $2 = destination on device
  local src="$root/$1" dst="$2"
  [ -d "$src" ] || { log "skip missing $1"; return 0; }
  mkdir -p "$dst"
  # --delete so removed files actually go; the source is the truth for app code.
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "$src/" "$dst/" >>"$LOG" 2>&1
  else
    find "$dst" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>>"$LOG"
    cp -a "$src/." "$dst/" 2>>"$LOG"
  fi
}

apply_dir "sudo-dashboard" "$DASHBOARD_DIR"
apply_dir "wifi-setup"     "$WIFI_DIR"
apply_dir "picoclaw"       "$PICOCLAW_DIR"
apply_dir "openclaw"       /opt/sudo-openclaw

# The WhatsApp bridge is app code too. Its dependencies live in node_modules
# beside it, so a change to package.json (a version pin, a new lib) only takes
# effect after an install -- copy the files, then reinstall if the manifest
# actually moved.
if [ -d "$root/whatsapp-bridge" ]; then
  old_pkg=""
  [ -f "${WHATSAPP_DIR}/package.json" ] && old_pkg="$(cat "${WHATSAPP_DIR}/package.json" 2>/dev/null)"
  apply_dir "whatsapp-bridge" "$WHATSAPP_DIR"
  new_pkg="$(cat "${WHATSAPP_DIR}/package.json" 2>/dev/null)"
  if [ "$old_pkg" != "$new_pkg" ] && command -v npm >/dev/null 2>&1; then
    log "whatsapp deps changed -- reinstalling"
    ( cd "$WHATSAPP_DIR" && npm install --omit=dev --no-audit --no-fund ) >>"$LOG" 2>&1 \
      || log "whatsapp dependency install reported an issue"
    systemctl restart sudo-whatsapp-bridge.service >>"$LOG" 2>&1 || true
  fi
fi

# Helper scripts are flat files in sudo-pi/, installed by name. We copy them
# wholesale -- new helper scripts added upstream show up here too.
if [ -d "$root/sudo-pi" ]; then
  for f in "$root"/sudo-pi/*.sh "$root"/sudo-pi/*.py; do
    [ -e "$f" ] || continue
    install -m 0755 "$f" "$BIN_DIR/$(basename "$f")" 2>>"$LOG"
  done
fi

# ── Rebuild agent config from template + preserved owner choices ────────────
# configure-picoclaw.sh reads /opt/sudo/config.json (untouched by this script)
# and regenerates /opt/sudo/picoclaw/config.json + the identity files, leaving
# memory/ and sessions/ alone.
if [ -x "$BIN_DIR/sudo-configure-picoclaw.sh" ]; then
  "$BIN_DIR/sudo-configure-picoclaw.sh" >>"$LOG" 2>&1 || log "configure step reported an issue"
elif [ -x "$BIN_DIR/configure-picoclaw.sh" ]; then
  "$BIN_DIR/configure-picoclaw.sh" >>"$LOG" 2>&1 || log "configure step reported an issue"
fi

# ── Record + restart ────────────────────────────────────────────────────────
printf '%s\n' "$LATEST" > "$VERSION_FILE"
chmod 644 "$VERSION_FILE" 2>/dev/null || true

# Refresh the on-device baseline: this tarball is now what we are running, so a
# future update -- or a rollback to "what we had" -- can reach it without the
# network. Written atomically so a power cut cannot leave a half file behind.
if [ -f "$TGZ" ]; then
  cp -f "$TGZ" "${BUNDLE_TGZ}.tmp" && mv -f "${BUNDLE_TGZ}.tmp" "$BUNDLE_TGZ" || log "could not refresh baseline bundle"
fi
if [ -n "$SHA_FILE" ] && [ -f "$SHA_FILE" ]; then
  cp -f "$SHA_FILE" "${BUNDLE_SHA}.tmp" && mv -f "${BUNDLE_SHA}.tmp" "$BUNDLE_SHA" || true
else
  # A local apply has no published checksum; recompute so the baseline is
  # always self-consistent for the next comparison.
  sha256sum "$BUNDLE_TGZ" 2>/dev/null | awk '{print $1"  sudo-factory.tar.gz"}' > "${BUNDLE_SHA}.tmp" 2>/dev/null \
    && mv -f "${BUNDLE_SHA}.tmp" "$BUNDLE_SHA" || true
fi

systemctl restart sudo-dashboard.service >>"$LOG" 2>&1 || true
# try-restart: the configure step above already started whichever brain is
# active, and a plain restart would wake picoclaw on an OpenClaw device.
systemctl try-restart picoclaw-gateway.service >>"$LOG" 2>&1 || true

rm -rf "$STAGE"
write_status "done" "Updated to ${LATEST}." "$LATEST" "$LATEST"
log "updated $CURRENT -> $LATEST"
exit 0
