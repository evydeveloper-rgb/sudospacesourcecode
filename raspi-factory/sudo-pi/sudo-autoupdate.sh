#!/bin/bash
# sudo-autoupdate.sh on|off|default — automatic version updates.
#
# Writes its own units rather than relying on install-factory.sh, because a
# device that only ever updates over Wi-Fi never re-runs the installer, and
# the OTA updater does not write unit files. "default" turns updates on only
# if the owner has never chosen; the choice is remembered in CHOICE.
set -euo pipefail

CHOICE=/etc/sudo/autoupdate
UNIT_DIR=/etc/systemd/system

write_units() {
  cat > "$UNIT_DIR/sudo-autoupdate.service" << 'EOF'
[Unit]
Description=Sudo automatic update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/sudo-update.sh
EOF
  # Overnight, spread over a few hours so a household of devices (and every
  # device on a release day) does not hit GitHub at the same minute.
  cat > "$UNIT_DIR/sudo-autoupdate.timer" << 'EOF'
[Unit]
Description=Sudo automatic update (daily)

[Timer]
OnCalendar=*-*-* 03:00
RandomizedDelaySec=3h
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
}

mkdir -p /etc/sudo
case "${1:-}" in
  on)
    write_units
    systemctl enable --now sudo-autoupdate.timer
    echo on > "$CHOICE"
    ;;
  off)
    systemctl disable --now sudo-autoupdate.timer 2>/dev/null || true
    echo off > "$CHOICE"
    ;;
  default)
    if [ "$(cat "$CHOICE" 2>/dev/null)" != "off" ]; then
      write_units
      systemctl enable --now sudo-autoupdate.timer
      echo on > "$CHOICE"
    fi
    ;;
  *)
    echo "usage: sudo-autoupdate.sh on|off|default" >&2
    exit 2
    ;;
esac
