#!/bin/bash
# Register this Pi with Sudo cloud API after WiFi is online.
# Runs once via systemd after first successful WiFi setup.
# Sends device_secret — generated offline at factory install.
set -euo pipefail

LOG="/var/log/sudo-cloud-init.log"
CONFIG="/opt/sudo/config.json"
API_URL_FILE="/etc/sudo/api.url"
DEVICE_ID_SCRIPT="/usr/local/bin/sudo-device-id.sh"
DEVICE_SECRET_FILE="/etc/sudo/device_secret"
MARKER="/var/lib/sudo-cloud-registered"

exec >> "$LOG" 2>&1
echo "=== sudo-cloud-init $(date) ==="

if [ -f "$MARKER" ]; then
  echo "Already registered — skipping"
  exit 0
fi

# Opt-in guard: builds that did not enable Sudo credits never register.
if [ ! -f /etc/sudo/billing-enabled ] || [ "$(tr -d '[:space:]' < /etc/sudo/billing-enabled)" != "billing=1" ]; then
  echo "Billing disabled on this build — cloud registration is opt-in, skipping"
  exit 0
fi

if [ ! -f /var/lib/wifi-setup-configured ]; then
  echo "WiFi not configured yet — skipping"
  exit 0
fi

# Ensure secrets exist (offline-generated)
if [ -x /usr/local/bin/sudo-device-secrets.sh ]; then
  /usr/local/bin/sudo-device-secrets.sh || true
fi

if [ ! -f "$DEVICE_SECRET_FILE" ]; then
  echo "ERROR: device_secret missing"
  exit 1
fi

API_URL="https://YOUR-API-URL.vercel.app"
if [ -f "$API_URL_FILE" ]; then
  API_URL="$(tr -d '[:space:]' < "$API_URL_FILE")"
fi

DEVICE_ID="$("$DEVICE_ID_SCRIPT")"
DEVICE_SECRET="$(tr -d '[:space:]' < "$DEVICE_SECRET_FILE")"
HOSTNAME="$(hostname 2>/dev/null || echo raspberrypi)"

echo "Registering device_id=$DEVICE_ID api=$API_URL"

for attempt in $(seq 1 30); do
  if curl -sf --max-time 5 "${API_URL}/health" >/dev/null 2>&1; then
    break
  fi
  echo "Waiting for network/API (attempt $attempt)..."
  sleep 5
done

RESPONSE="$(curl -sf --max-time 30 \
  -X POST "${API_URL}/v1/devices/register" \
  -H "Content-Type: application/json" \
  -H "X-Device-Id: ${DEVICE_ID}" \
  -H "X-Device-Secret: ${DEVICE_SECRET}" \
  -d "{\"hostname\":\"${HOSTNAME}\"}" \
  2>&1)" || {
    echo "Registration failed: $RESPONSE"
    exit 1
  }

echo "API response: $RESPONSE"

mkdir -p /opt/sudo
python3 - "$CONFIG" "$API_URL" "$DEVICE_ID" "$RESPONSE" << 'PY'
import json, sys, os
config_path, api_url, device_id, raw = sys.argv[1:5]
data = {}
if os.path.isfile(config_path):
    with open(config_path) as f:
        data = json.load(f)
try:
    api = json.loads(raw)
except json.JSONDecodeError:
    api = {}
data["device_id"] = device_id
data["api_base"] = api_url
data["user_id"] = api.get("user_id")
data["balance_usd"] = api.get("balance_usd", 0)
data["subscription_status"] = api.get("subscription_status", "inactive")
if api.get("openrouter_key"):
    data["openrouter_key"] = api["openrouter_key"]
with open(config_path, "w") as f:
    json.dump(data, f, indent=2)
os.chmod(config_path, 0o600)
PY

touch "$MARKER"
echo "=== sudo-cloud-init complete $(date) ==="

if [ -x /usr/local/bin/sudo-configure-picoclaw.sh ]; then
  /usr/local/bin/sudo-configure-picoclaw.sh || true
fi
