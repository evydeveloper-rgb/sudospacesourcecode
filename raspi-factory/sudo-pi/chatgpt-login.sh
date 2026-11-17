#!/bin/bash
# chatgpt-login.sh -- "Sign in with ChatGPT" from the dashboard.
#
# Runs OpenClaw's own ChatGPT (OpenAI Codex) device-code sign-in, which shows
# a short code to type at auth.openai.com/codex/device; OpenClaw keeps the
# resulting sign-in in its own auth store, as it would from its terminal. The
# command insists on a terminal, so `script` gives it one and keeps what it
# prints in OUT, where the dashboard reads the code. STATUS says how it went.
# Run through systemd-run: the dashboard's sandbox cannot write OpenClaw's
# state as the gateway's user.
#   chatgpt-login.sh          start a sign-in (a running one is replaced)
#   chatgpt-login.sh cancel   stop one that is waiting
umask 077
OUT=/var/lib/sudo-chatgpt-login.out
STATUS=/var/lib/sudo-chatgpt-login.status
OC=/opt/openclaw/node_modules/.bin/openclaw

stop_running() {
  pkill -f "models auth login --provider openai" 2>/dev/null || true
}

if [ "$1" = "cancel" ]; then
  stop_running
  echo "cancelled" > "$STATUS"
  rm -f "$OUT"
  exit 0
fi

stop_running
rm -f "$OUT"
echo "waiting" > "$STATUS"
export HOME=/root OPENCLAW_STATE_DIR=/opt/sudo/openclaw OPENCLAW_CONFIG_PATH=/opt/sudo/openclaw/openclaw.json TERM=dumb
cd /opt/sudo/openclaw || exit 1
# 15 minutes to type the code; OpenAI's own code expires before that.
if timeout 900 script -qefc "$OC models auth login --provider openai --device-code --force" "$OUT" </dev/null >/dev/null 2>&1; then
  echo "ok" > "$STATUS"
else
  # A cancel already said so; do not overwrite it.
  [ "$(cat "$STATUS" 2>/dev/null)" = "cancelled" ] || echo "failed" > "$STATUS"
fi
chmod 600 "$OUT" 2>/dev/null || true
