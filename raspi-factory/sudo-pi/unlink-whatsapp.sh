#!/bin/bash
# unlink-whatsapp.sh — forget the paired WhatsApp account and start fresh.
#
# Deleting the auth folder while the bridge is still holding an open
# connection to it would not cleanly disconnect anything -- Baileys keeps its
# session state in memory too. Stopping the service first is what actually
# ends the session; the folder is just where it would otherwise come back
# from on the next boot.
set -uo pipefail

STATUS_FILE="/var/lib/sudo-whatsapp-status.json"
AUTH_DIR="/opt/sudo/whatsapp-auth"

systemctl stop sudo-whatsapp-bridge.service 2>/dev/null || true
rm -rf "$AUTH_DIR"

python3 - "$STATUS_FILE" <<'PY'
import json, sys, time
path = sys.argv[1]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"state": "absent", "qr": None, "number": None,
               "message": "", "updated": int(time.time() * 1000)}, f)
PY

# Only restart if it had been set up before -- on a device that never
# installed the bridge at all, systemd start would be its OWN "installing"
# flow's job, not this one's.
if [ -d /opt/whatsapp-bridge/node_modules ]; then
    systemctl start sudo-whatsapp-bridge.service 2>/dev/null || true
fi
