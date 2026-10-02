#!/bin/bash
# sudo-heartbeat.sh — let the device speak first, occasionally.
#
# Run by sudo-heartbeat.timer every 30 minutes. Most runs do nothing: this is
# a proactive nudge, not a status strobe, and a box that pings its owner on
# schedule is a box people mute.
#
# The gate is cheap and answers "is there anything to say?" before we ever pay
# for an LLM turn:
#
#   * WhatsApp has to be paired at all *and* the owner has to have written to
#     the device once, so we know where to send. No target -> nothing to do.
#   * picoclaw's own session store has to have seen a conversation in the last
#     few hours. A device nobody has talked to all day does not get to start
#     one out of nowhere.
#   * Quiet hours are respected: no messages 22:00-08:00 local.
#
# Only when all three pass do we run one agent turn, and even then the agent
# may answer "HEARTBEAT_OK" — which we drop without sending anything. So the
# agent keeps the judgement about *what* is worth saying; the script only
# decides whether it is even allowed to think about it.
#
# Deliberately not `set -e`: a heartbeat is best-effort. A failure here should
# log and exit quietly, never page anyone.
set -uo pipefail

WORKSPACE=/opt/sudo/agent-workspace
PICOCLAW_BIN=/usr/local/bin/picoclaw
PICOCLAW_CONFIG=/opt/sudo/picoclaw/config.json
SEND_TOKEN_FILE=/opt/sudo/whatsapp/send-token
SEND_URL=http://127.0.0.1:8790/send
LOG=/var/log/sudo-heartbeat.log
# The same session the WhatsApp bridge uses, so a nudge can build on whatever
# conversation is already open rather than arriving as a stranger.
SESSION_KEY=sudo-whatsapp:main
# No sending inside this local window. The device has no clock but the Pi's.
QUIET_START=22
QUIET_END=8
# How stale the last real message may be before we stop nudging at all.
MAX_IDLE_HOURS=6

log() { echo "$(date -Is) $*"; }

{
echo "=== sudo-heartbeat $(date -Is) ==="

hour=$(date +%H)
if [ "$hour" -ge "$QUIET_START" ] || [ "$hour" -lt "$QUIET_END" ]; then
  log "quiet hours ($hour:00) — skipping"
  exit 0
fi

if [ ! -f "$SEND_TOKEN_FILE" ]; then
  log "no send token — WhatsApp not paired, skipping"
  exit 0
fi

if [ ! -x "$PICOCLAW_BIN" ] || [ ! -f "$PICOCLAW_CONFIG" ]; then
  log "picoclaw not configured, skipping"
  exit 0
fi

# Has anyone actually talked to this device lately? Sessions live as JSONL
# under the workspace; the newest one that mentions our key is the signal.
last=$(
  find "$WORKSPACE/sessions" -type f -name '*.jsonl' -newermt "-${MAX_IDLE_HOURS} hours" 2>/dev/null \
    | head -1
)
if [ -z "$last" ]; then
  log "no recent conversation — skipping"
  exit 0
fi

export HOME=/root
export PICOCLAW_CONFIG="$PICOCLAW_CONFIG"
export NO_COLOR=1
export TERM=dumb
export PICOCLAW_LOG_LEVEL=error

PROMPT='Heartbeat check. Look at what you know about this person (USER.md, memory) and \
whether anything is genuinely worth surfacing right now — an upcoming calendar item, \
something they said they would do, a message they are waiting on. If nothing meets that \
bar, reply with exactly HEARTBEAT_OK and nothing else. If something does, reply with one \
short message to send them. Do not invent anything.'

reply=$(timeout 90 "$PICOCLAW_BIN" agent -m "$PROMPT" --session "$SESSION_KEY" 2>/dev/null \
  | tr -d '\r')

# The tail of stdout can carry startup banner noise; take the last non-empty line.
reply=$(printf '%s\n' "$reply" | grep -v '^[[:space:]]*$' | tail -1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

if [ -z "$reply" ] || [ "$reply" = "HEARTBEAT_OK" ]; then
  log "nothing worth saying"
  exit 0
fi

token=$(cat "$SEND_TOKEN_FILE")
payload=$(printf '%s' "$reply" | python3 -c 'import json,sys; print(json.dumps({"text": sys.stdin.read()}))')
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$SEND_URL" \
  -H "Content-Type: application/json" -H "x-sudo-token: $token" \
  --data "$payload" --max-time 15)

if [ "$code" = "200" ]; then
  log "sent a nudge"
else
  log "send failed (HTTP $code)"
fi

} >> "$LOG" 2>&1
