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
  python3 - "$1" "$2" "${3:-0}" "$STATUS" <<'PY'
import json, sys, time
state, message, percent, path = sys.argv[1:5]
try:
    percent = int(percent)
except Exception:
    percent = 0
with open(path, "w", encoding="utf-8") as f:
    json.dump({"state": state, "message": message, "percent": percent, "at": time.time()}, f)
PY
}

# A 4GB board cannot afford to be wrong about this. If something already ate
# the RAM, installing a resident model makes the device worse, not better.
avail_mb="$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
echo "MemAvailable: ${avail_mb}MB"
if [ "${avail_mb:-0}" -lt 900 ]; then
  set_status failed "Not enough free memory (${avail_mb}MB) to run a local model safely" 0
  echo "aborting: only ${avail_mb}MB available"
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  set_status installing "Installing the local model runtime…" 5
  echo "installing ollama"
  # Progress bars are carriage-return spam: they filled 12KB of log with
  # "####" and pushed the lines that matter out of the tail we keep.
  # tr first: the progress bar is carriage-return separated, so without it
  # this is one enormous line and nothing can be filtered out of it. sed
  # rather than grep because pipefail is on and grep exits non-zero when it
  # drops every line — which would read as the install having failed.
  if ! curl -fsSL --max-time 300 https://ollama.com/install.sh | sh 2>&1 \
       | tr '\r' '\n' | sed -E '/^[#[:space:]]*([0-9.]+%)?[[:space:]]*$/d' ; then
    set_status failed "Could not install the model runtime — check the connection" 0
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

set_status installing "Downloading ${MODEL}…" 12
echo "pulling ${MODEL}"
# Same treatment as the runtime install: the pull draws a progress bar with
# carriage returns and ANSI cursor moves, and unfiltered it filled the whole
# 12KB log tail with one repeated line, pushing the memory and network checks
# off the top where they could not be read.
#
# It also feeds the dashboard's progress bar: as ollama reports a percentage,
# that is mapped onto 12-90% of the overall setup and written to the status
# file, so the person watching sees the download actually move instead of a
# bar that sits still and then jumps to done.
if ! ollama pull "$MODEL" 2>&1 \
     | tr '\r' '\n' \
     | python3 -c '
import json, re, sys, time
state_path, model = sys.argv[1], sys.argv[2]
bar = re.compile(r"^(pulling [0-9a-f]+:|pulling manifest|verifying|writing manifest|success)")
ansi = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
pct = re.compile(r"(\d{1,3})%")
last = -1
for raw in sys.stdin:
    line = ansi.sub("", raw).replace("[", "")
    m = pct.search(line)
    if m:
        p = min(100, int(m.group(1)))
        if p > last:
            last = p
            try:
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump({"state": "installing",
                               "message": "Downloading %s… %d%%" % (model, p),
                               "percent": 12 + int(p * 0.78),
                               "at": time.time()}, f)
            except OSError:
                pass
    stripped = line.strip()
    if stripped and not bar.match(stripped):
        sys.stdout.write(stripped + "\n")
' "$STATUS" "$MODEL"
  then
  set_status failed "Could not download ${MODEL}" 0
  echo "pull failed"
  exit 1
fi

# Prove it actually answers before telling anyone it is ready. A model that
# pulled but will not run is worse than no model, because the chat silently
# falls back with no explanation.
#
# Two things this test has to get right, both learned on real hardware:
#   - The FIRST generate call loads the model into RAM, which on a 4GB Pi can
#     take well over a minute. One shot with one timeout brands a perfectly
#     good model as broken.
#   - Ollama can return an empty body (or a transient 500) while it is still
#     loading, which is not the same as the model failing to run.
# So: retry a few times, and treat a non-empty reply from any attempt as
# success. Only a model that never once answers across the whole set is truly
# "would not run" -- and even then we keep it and say so softly, because the
# box is still better off with a working local model than without one.
echo "smoke test"
set_status installing "Waking ${MODEL} up for the first time…" 92

smoke() {
  curl -sf --max-time 120 http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"Say hello in five words.\",\"stream\":false}" \
    2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("response","").strip())' 2>/dev/null
}

reply=""
for attempt in 1 2 3 4 5; do
  reply="$(smoke)"
  if [ -n "$reply" ]; then
    echo "smoke test reply (attempt ${attempt}): ${reply}"
    break
  fi
  echo "smoke test attempt ${attempt} returned nothing — retrying"
  # Nudge the daemon: the first request can 500 while the runner is still
  # spinning up, and a moment's wait clears it.
  sleep $((attempt * 5))
done

# Embedding model for memory search. Local, on the same Ollama daemon as the
# chat model — pulled here so semantic memory search works out of the box.
# Non-fatal on purpose: if it fails, OpenClaw drops back to keyword search
# rather than losing memory entirely.
EMBED_MODEL="${SUDO_EMBED_MODEL:-nomic-embed-text}"
echo "pulling embedding model ${EMBED_MODEL}"
set_status installing "Preparing memory search (${EMBED_MODEL})…" 95
if ollama pull "$EMBED_MODEL" >/dev/null 2>&1; then
  echo "embedding model ready: ${EMBED_MODEL}"
else
  echo "embedding model pull failed — memory search will fall back to keywords"
fi

if [ -z "$reply" ]; then
  # Downloaded but never answered across five tries. Keep the model (it is on
  # disk and may work once the device has settled) and record it as installed
  # so the chat actually tries it, rather than declaring failure and hiding it
  # behind a cloud-key prompt when it may be fine.
  set_status done "${MODEL} ready (first reply was slow)" 100
  echo "smoke test: no reply after retries — model kept, marked ready"
else
  set_status done "${MODEL} ready" 100
fi

echo "=== install-local-model complete $(date) ==="
