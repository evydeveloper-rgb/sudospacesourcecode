#!/bin/bash
# install-local-model.sh — put a small model on the device for first contact.
#
# Gemma 3 270M via Ollama. Chosen for a 4GB Pi 5: roughly 200MB on disk and
# well under 1GB resident, so it answers in about a second and still leaves
# the agent room. It is not smart, and it is not meant to be -- it greets,
# acknowledges, and keeps the box useful when the cloud key is missing or the
# internet is down. Real questions go to the cloud model.
#
# Runs after the Wi-Fi join, never at boot: this needs a network and at boot
# there isn't one. Progress goes to a status file the dashboard polls, the
# same way install-devtools.sh works.
#
# Deliberately NOT set -e: a failure should land in the status file as
# something readable, not kill the script and leave the UI waiting forever.
set -uo pipefail

STATUS="/var/lib/sudo-localmodel-status"
LOG="/var/log/sudo-local-model.log"
MODEL="${SUDO_LOCAL_MODEL:-gemma3:270m}"

# systemd gives a service almost no environment, and the ollama CLI reads
# $HOME to find its model directory. Without it, `ollama pull` does not fail
# politely -- it panics:
#     panic: $HOME is not defined
# Set both HOME and the model path explicitly so nothing depends on inheriting
# a login environment this will never have.
export HOME="${HOME:-/root}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-/usr/share/ollama/.ollama/models}"

BOOTLOG=/boot/firmware/localmodel.log
# Mirrored to the boot partition. When this fails the owner has no shell and
# /var/log is on ext4, so the only way anyone sees why is to pull the card and
# read it from another machine. Copied on exit, never piped -- a pipe here
# would take the script down with SIGPIPE if the FAT write failed.
exec >> "$LOG" 2>&1
trap 'tail -c 12000 "$LOG" > "$BOOTLOG" 2>/dev/null || true' EXIT
echo "=== install-local-model $(date) — ${MODEL} ==="
echo "free memory:"; free -m 2>/dev/null | head -2
echo "network check:"
if curl -sf --max-time 8 -o /dev/null https://ollama.com/install.sh; then
  echo "  ollama.com reachable"
else
  echo "  ollama.com NOT reachable — no internet?"
fi

set_status() {
  python3 - "$1" "$2" "$STATUS" <<'PY'
import json, sys, time
state, message, path = sys.argv[1:4]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"state": state, "message": message, "at": time.time()}, f)
PY
}

# A 4GB board cannot afford to be wrong about this. If something already ate
# the RAM, installing a resident model makes the device worse, not better.
avail_mb="$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
echo "MemAvailable: ${avail_mb}MB"
if [ "${avail_mb:-0}" -lt 900 ]; then
  set_status failed "Not enough free memory (${avail_mb}MB) to run a local model safely"
  echo "aborting: only ${avail_mb}MB available"
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  set_status installing "Installing the local model runtime…"
  echo "installing ollama"
  # Progress bars are carriage-return spam: they filled 12KB of log with
  # "####" and pushed the lines that matter out of the tail we keep.
  # tr first: the progress bar is carriage-return separated, so without it
  # this is one enormous line and nothing can be filtered out of it. sed
  # rather than grep because pipefail is on and grep exits non-zero when it
  # drops every line — which would read as the install having failed.
  if ! curl -fsSL --max-time 300 https://ollama.com/install.sh | sh 2>&1 \
       | tr '\r' '\n' | sed -E '/^[#[:space:]]*([0-9.]+%)?[[:space:]]*$/d' ; then
    set_status failed "Could not install the model runtime — check the connection"
    echo "ollama install failed"
    exit 1
  fi
fi

# The upstream installer prints "systemd is not running" when it cannot see a
# live systemd from inside this unit, and then skips creating its own service.
# Write one ourselves in that case, or the daemon dies with this script and
# the greeter is gone again after the next reboot.
if [ ! -f /etc/systemd/system/ollama.service ]; then
  echo "ollama.service missing — creating it"
  cat > /etc/systemd/system/ollama.service << 'OLLAMAUNIT'
[Unit]
Description=Ollama Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/ollama serve
Environment=HOME=/root
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_MODELS=/usr/share/ollama/.ollama/models
Restart=always
RestartSec=3
User=root

[Install]
WantedBy=multi-user.target
OLLAMAUNIT
  systemctl daemon-reload
fi

systemctl enable ollama 2>/dev/null || true
systemctl restart ollama 2>/dev/null || true

# Give the daemon a moment to bind before asking it for anything.
for _ in $(seq 1 15); do
  curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 2
done

set_status installing "Downloading ${MODEL} (about 200MB)…"
echo "pulling ${MODEL}"
# Same treatment as the runtime install: the pull draws a progress bar with
# carriage returns and ANSI cursor moves, and unfiltered it filled the whole
# 12KB log tail with one repeated line, pushing the memory and network checks
# off the top where they could not be read.
if ! ollama pull "$MODEL" 2>&1 \
     | tr '\r' '\n' \
     | sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g; s/\[[0-9;?]+[a-zA-Z]//g; /^[[:space:]]*$/d; /^pulling [0-9a-f]+:/d; /^pulling manifest/d; /^verifying/d'; then
  set_status failed "Could not download ${MODEL}"
  echo "pull failed"
  exit 1
fi

# Prove it actually answers before telling anyone it is ready. A model that
# pulled but will not run is worse than no model, because the chat silently
# falls back with no explanation.
echo "smoke test"
reply="$(curl -sf --max-time 90 http://127.0.0.1:11434/api/generate \
  -d "{\"model\":\"${MODEL}\",\"prompt\":\"Say hello in five words.\",\"stream\":false}" \
  2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("response","").strip())' 2>/dev/null)"

if [ -z "$reply" ]; then
  set_status failed "${MODEL} downloaded but did not respond"
  echo "smoke test failed"
  exit 1
fi

echo "smoke test reply: ${reply}"
set_status done "${MODEL} ready"
echo "=== install-local-model complete $(date) ==="
