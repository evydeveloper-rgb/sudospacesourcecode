#!/bin/bash
# Generate or read persistent device ID for this Pi.
set -euo pipefail

DEVICE_ID_FILE="/etc/sudo/device_id"

mkdir -p /etc/sudo

if [ -f "$DEVICE_ID_FILE" ]; then
  cat "$DEVICE_ID_FILE"
  exit 0
fi

if command -v uuidgen >/dev/null 2>&1; then
  ID="$(uuidgen)"
else
  ID="sudo-$(cat /proc/cpuinfo 2>/dev/null | grep Serial | awk '{print $3}' | tr -d '\n')"
  [ -z "$ID" ] || [ "$ID" = "sudo-" ] && ID="sudo-$(date +%s)-$RANDOM"
fi

echo "$ID" > "$DEVICE_ID_FILE"
chmod 644 "$DEVICE_ID_FILE"
echo "$ID"
