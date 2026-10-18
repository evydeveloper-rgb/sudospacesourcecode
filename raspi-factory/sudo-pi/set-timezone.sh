#!/bin/bash
# set-timezone.sh — put the device clock in its owner's time zone.
#
# Run through systemd-run from the dashboard: that service has
# ProtectSystem=full, so it cannot change /etc/localtime itself.
#
#   set-timezone.sh <Area/City>
#
# Reminders, check-in hours and "what time is it" all read this clock, and a
# card flashed in one country and used in another kept the first one's zone.
# The name is checked again here rather than trusted from the caller: it has
# to be a real zone file, so nothing else can be pointed at.
set -uo pipefail

LOG=/var/log/sudo-set-timezone.log
exec >> "$LOG" 2>&1
echo "=== set-timezone $(date) ==="

TZNAME="${1:-}"
if ! printf '%s' "$TZNAME" | grep -qE '^[A-Za-z]+(/[A-Za-z0-9_+-]+){0,2}$' \
   || [ ! -f "/usr/share/zoneinfo/$TZNAME" ]; then
    echo "rejected: '${TZNAME}' is not a time zone"
    exit 2
fi

if timedatectl set-timezone "$TZNAME" 2>/dev/null; then
    :
else
    # No systemd-timedated (or it is masked): do what it would have done.
    ln -sf "/usr/share/zoneinfo/$TZNAME" /etc/localtime
fi
printf '%s\n' "$TZNAME" > /etc/timezone
echo "time zone: $TZNAME ($(date +%Z))"
