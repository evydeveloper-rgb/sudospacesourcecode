#!/bin/bash
# notify-owner.sh "<text>" — send the owner a short WhatsApp (or Telegram) message.
#
# For Sudo's own notices (Home: a new device on the Wi-Fi, someone arriving),
# which need no model: the text is fixed, so asking the agent to write it
# would only add cost and delay. Run through systemd-run from the dashboard,
# whose sandbox (ProtectHome) keeps the OpenClaw CLI from starting.
#
# Goes from the agent's own number when that is linked, else into the owner's
# "Message yourself" chat; with no WhatsApp, to their Telegram bot chat. Does
# nothing if neither is set up.
set -uo pipefail
LOG=/var/log/sudo-notify.log
OC_BIN=/opt/openclaw/node_modules/.bin/openclaw
export OPENCLAW_STATE_DIR=/opt/sudo/openclaw OPENCLAW_CONFIG_PATH=/opt/sudo/openclaw/openclaw.json HOME=/root
exec >> "$LOG" 2>&1

text="${1:-}"
[ -n "$text" ] || exit 0
[ -x "$OC_BIN" ] || { echo "$(date -Is) no OpenClaw -- not sent"; exit 0; }

read -r owner account tg_owner < <(python3 - <<'PY'
import json
c = json.load(open("/opt/sudo/config.json"))
nums = c.get("whatsapp_numbers") or {}
owner = (c.get("whatsapp_owner_number") or nums.get("owner") or "").strip()
account = "agent" if nums.get("agent") else ("owner" if nums.get("owner") else "")
tg = c.get("telegram") or {}
tg_owner = str(tg.get("owner_id") or "").strip() if tg.get("token") else ""
print(owner or "-", account or "-", tg_owner or "-")
PY
)
if [ "$owner" = "-" ] || [ "$account" = "-" ]; then
  if [ "$tg_owner" != "-" ]; then
    if "$OC_BIN" message send --channel telegram --target "$tg_owner" --message "$text" >/dev/null; then
      echo "$(date -Is) sent via telegram"
    else
      echo "$(date -Is) send failed via telegram"
    fi
    exit 0
  fi
  echo "$(date -Is) no chat app set up -- not sent"
  exit 0
fi
if "$OC_BIN" message send --channel whatsapp --account "$account" --target "$owner" --message "$text" >/dev/null; then
  echo "$(date -Is) sent via $account"
else
  echo "$(date -Is) send failed via $account"
fi
