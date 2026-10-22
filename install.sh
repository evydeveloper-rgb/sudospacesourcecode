#!/bin/bash
# install.sh — put sudo on a Linux machine that is already running.
#
# For a PC, a mini PC, a home server, a Jetson, or a Raspberry Pi you would
# rather not reflash: anything with Debian or Ubuntu (arm64 or x86_64) and
# systemd. The Sudo box itself is set up from its card instead
# (raspi-factory/prepare-sd.sh); both install the same thing.
#
#   curl -fsSL https://raw.githubusercontent.com/evydeveloper-rgb/sudospacesourcecode/main/install.sh | sudo bash
#
# Options (after `bash -s --` when piped):
#   --version X.Y.Z   a release (default: the latest)
#   --from DIR        install from a raspi-factory folder already on this
#                     machine (a checkout or an unpacked release; Ansible uses this)
#   --model ID|auto   the on-device model (default: auto, picked for this hardware)
#   --hostname NAME   rename the machine, so the dashboard is at NAME.local
#   --port N          dashboard port (default 80; 8080 when 80 is taken)
#
# Safe to run again: it repairs or updates the app and keeps the owner's
# settings, chats and agent. Updates also arrive from the dashboard, as on the
# Sudo box. Unlike the box there is no setup hotspot: the machine is already
# on its network, so the dashboard opens straight to the welcome.
set -euo pipefail

REPO="${SUDO_REPO:-evydeveloper-rgb/sudospacesourcecode}"
TAG_PREFIX="raspi-factory-v"
WORK=/var/cache/sudo
LOG=/var/log/sudo-install.log
VERSION=latest FROM="" MODEL=auto NEW_HOSTNAME="" PORT=""

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf 'sudo install: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?}"; shift 2 ;;
    --from) FROM="${2:?}"; shift 2 ;;
    --model) MODEL="${2:?}"; shift 2 ;;
    --hostname) NEW_HOSTNAME="${2:?}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0" 2>/dev/null || true; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done

# ── Can it run here? ───────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "run it as root (with sudo)"
[ "$(uname -s)" = "Linux" ] || die "sudo needs Linux"
[ -d /run/systemd/system ] || die "sudo needs systemd (not found: is this a container?)"
command -v apt-get >/dev/null || die "this installer is for Debian and Ubuntu (apt-get not found)"
case "$(uname -m)" in
  aarch64|arm64) ARCH=arm64 ;;
  x86_64|amd64) ARCH=amd64 ;;
  *) die "unsupported processor $(uname -m): sudo runs on arm64 and x86_64" ;;
esac
mkdir -p "$WORK" /etc/sudo /var/lib
exec > >(tee -a "$LOG") 2>&1
echo "=== sudo install.sh $(date -Is) ==="

# A Sudo box (set up from its card) updates itself; this would turn it into a
# plain Linux install and switch off its Wi-Fi recovery.
if [ -d /boot/firmware/sudo-dashboard ] && [ ! -f /etc/sudo/install-kind ]; then
  die "this looks like a Sudo box set up from its card -- it updates itself from the dashboard"
fi

say "Getting the basics (python3, curl, avahi)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 curl ca-certificates tar rsync avahi-daemon >/dev/null

# ── The app ────────────────────────────────────────────────────────────────────
if [ -n "$FROM" ]; then
  SRC="$(cd "$FROM" && pwd)"
  [ -f "$SRC/install-factory.sh" ] || die "--from $FROM is not a raspi-factory folder"
else
  if [ "$VERSION" = latest ]; then
    TAG="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
           | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')" \
      || die "could not reach GitHub to find the latest release"
  else
    TAG="${TAG_PREFIX}${VERSION}"
  fi
  say "Downloading sudo ${TAG#"$TAG_PREFIX"}…"
  BASE="https://github.com/$REPO/releases/download/$TAG"
  curl -fsSL "$BASE/sudo-factory.tar.gz" -o "$WORK/sudo-factory.tar.gz" || die "download failed ($TAG)"
  curl -fsSL "$BASE/sudo-factory.tar.gz.sha256" -o "$WORK/sudo-factory.tar.gz.sha256" || die "checksum download failed"
  (cd "$WORK" && sha256sum -c sudo-factory.tar.gz.sha256 >/dev/null) || die "the download does not match its checksum"
  rm -rf "$WORK/src" && mkdir -p "$WORK/src"
  tar -xzf "$WORK/sudo-factory.tar.gz" -C "$WORK/src"
  SRC="$(find "$WORK/src" -mindepth 1 -maxdepth 1 -type d | head -1)"
fi
VER="$(tr -d ' \t\r\n' < "$SRC/VERSION")"

# The remote-link tool for this processor (the release carries the arm64 one).
if [ "$ARCH" = amd64 ] || [ ! -x "$SRC/cloudflared/cloudflared" ]; then
  say "Getting cloudflared for $ARCH…"
  CLOUDFLARED_ARCH="$ARCH" bash "$SRC/scripts/download-cloudflared.sh" >/dev/null
fi

STAGE="$WORK/stage"
rm -rf "$STAGE"
bash "$SRC/prepare-sd.sh" --stage "$STAGE" >/dev/null
cp "$SRC/install-factory.sh" "$STAGE/install-factory.sh"

# ── This machine ───────────────────────────────────────────────────────────────
echo linux > /etc/sudo/install-kind
# Already online, so setup starts at the dashboard's welcome, not a hotspot.
[ -f /var/lib/wifi-setup-configured ] || echo "installed by install.sh" > /var/lib/wifi-setup-configured

if [ -n "$NEW_HOSTNAME" ]; then
  hostnamectl set-hostname "$NEW_HOSTNAME"
  sed -i "s/^127\.0\.1\.1\s.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts 2>/dev/null || true
fi

PROFILE="$(python3 "$SRC/sudo-pi/hardware-profile.py")"
echo "$PROFILE"
if [ ! -s /var/lib/sudo-localmodel-active ]; then
  [ "$MODEL" = auto ] && MODEL="$(printf '%s' "$PROFILE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["model"])')"
  echo "$MODEL" > /var/lib/sudo-localmodel-active
elif [ "$MODEL" != auto ]; then
  echo "$MODEL" > /var/lib/sudo-localmodel-active
fi

say "Installing sudo $VER…"
SUDO_SRC="$STAGE" bash "$STAGE/install-factory.sh"

# Port 80 by default; 8080 when something else already serves 80.
if [ -z "$PORT" ]; then
  PORT=80
  if command -v ss >/dev/null && ss -ltnp 2>/dev/null | grep -E ':80\s' | grep -vq 'python3'; then
    PORT=8080
  fi
fi
DROPIN=/etc/systemd/system/sudo-dashboard.service.d
if [ "$PORT" != 80 ]; then
  mkdir -p "$DROPIN"
  printf '[Service]\nEnvironment=SUDO_DASH_PORT=%s\n' "$PORT" > "$DROPIN/20-port.conf"
else
  rm -f "$DROPIN/20-port.conf"
fi

systemctl daemon-reload
systemctl enable --now avahi-daemon.service >/dev/null 2>&1 || true
systemctl enable sudo-dashboard.service >/dev/null 2>&1
systemctl restart sudo-dashboard.service
# The agent and the on-device model download in the background (minutes).
systemctl start --no-block sudo-openclaw-install.service sudo-local-model.service 2>/dev/null || true

HOST="$(hostname)"
SUFFIX=$([ "$PORT" = 80 ] && echo "" || echo ":$PORT")
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
say "sudo $VER is installed."
echo "  Open  http://$HOST.local$SUFFIX   (or http://$IP$SUFFIX)"
echo "  The agent and the on-device model ($(cat /var/lib/sudo-localmodel-active)) finish downloading in the"
echo "  background over the next few minutes. The welcome walks you through the rest."
echo "  Log: $LOG"
