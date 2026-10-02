#!/bin/bash
# set-hostname.sh — rename the device, and with it the address people use.
#
# Run through systemd-run from the dashboard: that service has
# ProtectSystem=full, which mounts /etc read-only, so it cannot write
# /etc/hostname or /etc/hosts itself.
#
#   set-hostname.sh <new-name>
#
# The name is validated again here rather than trusted from the caller. This
# writes /etc/hosts, and a name containing whitespace or newlines could
# otherwise inject extra entries into it.
set -uo pipefail

LOG=/var/log/sudo-set-hostname.log
exec >> "$LOG" 2>&1
echo "=== set-hostname $(date) ==="

NEW="${1:-}"
OLD="$(hostname)"

# RFC 1123: letters, digits and hyphens, not starting or ending with one,
# 63 characters at most. Lowercased because mDNS is case-insensitive and a
# mixed-case name only makes the printed address look wrong.
NEW="$(printf '%s' "$NEW" | tr '[:upper:]' '[:lower:]')"
if ! printf '%s' "$NEW" | grep -qE '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$'; then
    echo "rejected: '${NEW}' is not a valid hostname"
    exit 2
fi

if [ "$NEW" = "$OLD" ]; then
    echo "unchanged: already ${OLD}"
    exit 0
fi

echo "renaming ${OLD} -> ${NEW}"

if command -v hostnamectl >/dev/null 2>&1; then
    hostnamectl set-hostname "$NEW" || { echo "hostnamectl failed"; exit 1; }
else
    printf '%s\n' "$NEW" > /etc/hostname || { echo "could not write /etc/hostname"; exit 1; }
    hostname "$NEW" || true
fi

# /etc/hosts must agree, or sudo itself cannot resolve its own name and
# anything doing a reverse lookup stalls until it times out.
if grep -qE '^127\.0\.1\.1' /etc/hosts; then
    sed -i -E "s/^(127\.0\.1\.1[[:space:]]+).*/\1${NEW}/" /etc/hosts
else
    printf '127.0.1.1\t%s\n' "$NEW" >> /etc/hosts
fi

# avahi advertises <hostname>.local and only re-reads the name on restart, so
# without this the old address keeps being published and the new one does not
# resolve at all.
systemctl restart avahi-daemon 2>/dev/null || true

echo "now: $(hostname)"
grep -E '^127\.0\.1\.1' /etc/hosts || true
echo "=== set-hostname complete ==="
