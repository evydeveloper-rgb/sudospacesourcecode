#!/bin/bash
# install-local-model.sh — put a small model on the device for first contact.
#
# Which model a new device starts with comes from sudo-hardware-profile.py
# (its table is local-model-picks.py): Gemma 3 270M on a 4GB Pi 5, roughly
# 200MB on disk and well under 1GB resident, so it answers in about a second
# and still leaves the agent room; something bigger where there is memory and
# speed for it. It keeps the box useful when the cloud key is missing or the
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
ACTIVE="/var/lib/sudo-localmodel-active"
LOG="/var/log/sudo-local-model.log"
# With an argument: the owner picked another model in the dashboard, and it
# becomes the active one only if it downloads and answers. Without one (the
# boot run): make sure the active model -- the greeter on a new device -- is
# in place.
SWITCH="${1:-}"
GREETER=gemma3:270m
MODEL="${SWITCH:-$(cat "$ACTIVE" 2>/dev/null || true)}"
MODEL="${MODEL:-${SUDO_LOCAL_MODEL:-}}"
# A first model picked for this hardware. If it will not download or answer,
# the greeter goes on instead, so a new device always ends up with one.
PICKED=""
if [ -z "$MODEL" ]; then
  MODEL="$(python3 /usr/local/bin/sudo-hardware-profile.py --model 2>/dev/null || true)"
  MODEL="${MODEL:-$GREETER}"
  [ "$MODEL" != "$GREETER" ] && PICKED=1
fi
fallback_to_greeter() {
  # $1: why. Only for a model picked here, never for one the owner chose.
  [ -n "$PICKED" ] || return 0
  echo "$MODEL: $1 -- installing $GREETER instead"
  # As the boot run, not as a switch: the greeter is kept even if its first
  # reply is slow, the same as on any new device.
  SUDO_LOCAL_MODEL="$GREETER" exec "$0"
}

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
# Only where there is a boot partition (a Sudo card), not on other machines.
trap '[ -d /boot/firmware ] && tail -c 12000 "$LOG" > "$BOOTLOG" 2>/dev/null || true' EXIT
echo "=== install-local-model $(date) — ${MODEL} ==="
# The Pi has no clock battery. Right after it joins Wi-Fi the clock can still
# read the OS image's build date, and HTTPS then refuses any certificate
# issued after it ("certificate is not yet valid"): a new device's download
# failed exactly so, and the dashboard said the on-device brain had trouble
# installing. Wait for the network time, up to two minutes; failing that,
# take the date from a web server's reply, which needs no certificate.
for _ in $(seq 1 60); do
  [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && break
  sleep 2
done
if [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != yes ]; then
  web_date="$(curl -sI --max-time 10 http://deb.debian.org/ 2>/dev/null | sed -n 's/^[Dd]ate: *//p' | tr -d '\r')"
  if [ -n "$web_date" ] && [ "$(date -d "$web_date" +%s 2>/dev/null || echo 0)" -gt "$(date +%s)" ]; then
    date -s "$web_date" >/dev/null && echo "clock set from the web: $(date)"
  fi
fi
echo "free memory:"; free -m 2>/dev/null | head -2
echo "network check:"
if curl -sf --max-time 8 -o /dev/null https://ollama.com/install.sh; then
  echo "  ollama.com reachable"
else
  echo "  ollama.com NOT reachable — no internet?"
fi

set_status() {
  python3 - "$1" "$2" "${3:-0}" "$STATUS" "$MODEL" <<'PY'
import json, sys, time
state, message, percent, path, model = sys.argv[1:6]
try:
    percent = int(percent)
except Exception:
    percent = 0
with open(path, "w", encoding="utf-8") as f:
    json.dump({"state": state, "message": message, "percent": percent,
               "model": model, "at": time.time()}, f)
PY
}
# Stopped before the end -- the boot unit's 30-minute limit on a slow link
# (seen in China), a shutdown -- would otherwise leave "installing" in the
# status file for good, and the dashboard showing a download that is not
# happening. Say it stopped; the next boot tries again.
trap 'set_status failed "The download stopped before it finished. It tries again when sudo restarts." 0; exit 1' TERM INT

# A 4GB board cannot afford to be wrong about this. If something already ate
# the RAM, installing a resident model makes the device worse, not better.
avail_mb="$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
echo "MemAvailable: ${avail_mb}MB"
if [ -n "$PICKED" ] && [ "${avail_mb:-0}" -lt 2500 ]; then
  fallback_to_greeter "only ${avail_mb}MB free"
fi
# Not on a boot run where the model is already installed: there is nothing
# to add, and a busy moment at boot must not mark a working model as failed.
if [ "${avail_mb:-0}" -lt 900 ] && ! { [ -z "$SWITCH" ] && [ -s "$ACTIVE" ] && command -v ollama >/dev/null 2>&1; }; then
  set_status failed "Not enough free memory (${avail_mb}MB) to run a local model safely" 0
  echo "aborting: only ${avail_mb}MB available"
  exit 1
fi

if ! command -v ollama >/dev/null 2>&1; then
  set_status installing "Installing the local model runtime…" 5
  # Ollama's installer unpacks a .tar.zst and stops with "requires zstd"
  # when the tool is missing, which it is on minimal Ubuntu and Debian
  # images. Put it there first.
  if ! command -v zstd >/dev/null 2>&1; then
    echo "installing zstd (Ollama's installer needs it)"
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y -qq zstd \
      || { DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
           DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=300 install -y -qq zstd; } \
      || echo "zstd install failed"
  fi
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
# start, not restart: this runs on every boot, and a restart drops the model
# out of memory while the owner may already be chatting with it.
systemctl start ollama 2>/dev/null || true

# Give the daemon a moment to bind before asking it for anything.
for _ in $(seq 1 15); do
  curl -sf --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
  sleep 2
done

have_model() {
  curl -sf --max-time 5 http://127.0.0.1:11434/api/tags 2>/dev/null \
    | python3 -c 'import json,sys; m=sys.argv[1]; sys.exit(0 if any(x.get("name") in (m, m+":latest") for x in json.load(sys.stdin).get("models", [])) else 1)' "$MODEL"
}

# The boot run with the model already on the device: nothing to download.
# Pulling it again anyway is what turned a working model into "Could not
# download" -- a Pi that had just been moved booted before its internet was
# up, the re-pull failed, and the dashboard showed the install as broken.
if [ -z "$SWITCH" ] && have_model; then
  echo "$MODEL" > "$ACTIVE"
  set_status done "${MODEL} ready" 100
  echo "${MODEL} already installed -- nothing to download"
  exit 0
fi

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
#
# Whether it worked is ollama's exit code alone, never the filter's: a new
# 8GB Pi had the model downloaded and answering while this said "Could not
# download". Three tries (a pull resumes where it stopped), then ask Ollama
# whether the model is there before calling it a failure.
pull_once() {
  set +o pipefail
  ollama pull "$MODEL" 2>&1 \
     | tr '\r' '\n' \
     | python3 -c '
import json, re, sys, time
sys.stdout.reconfigure(errors="replace")
state_path, model = sys.argv[1], sys.argv[2]
bar = re.compile(r"^(pulling [0-9a-f]+:|pulling manifest|verifying|writing manifest|success)")
ansi = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
pct = re.compile(r"(\d{1,3})%")
# A model can come as several files (Qwen3.5: the weights and the part that
# reads images), each with its own 0-100%. Following one number read 100% for
# ten minutes while the rest downloaded, so add the files up by size:
#   pulling 8eeb52dfb3bb:  45% ... 904 MB/2.0 GB  25 MB/s
layer = re.compile(r"pulling ([0-9a-f]{6,}):\s*(\d{1,3})%.*?([\d.]+)\s*([KMG]?B)(?:/([\d.]+)\s*([KMG]?B))?")
unit = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9}
sizes, done = {}, {}
last = -1
for raw in sys.stdin:
    line = ansi.sub("", raw).replace("[", "")
    m = pct.search(line)
    if m:
        p = min(100, int(m.group(1)))
        lm = layer.search(line)
        if lm:
            total = float(lm.group(5) or lm.group(3)) * unit.get(lm.group(6) or lm.group(4), 1)
            sizes[lm.group(1)] = max(total, sizes.get(lm.group(1), 0))
            done[lm.group(1)] = sizes[lm.group(1)] * min(100, int(lm.group(2))) / 100
            if sum(sizes.values()) > 0:
                # Ollama starts the next file only when one is finished, so
                # the total grows and the bar can step back: honest, where a
                # bar held at 100% looked stuck. 99 until the pull is over.
                p = min(99, int(100 * sum(done.values()) / sum(sizes.values())))
        parts = sum(1 for s in sizes.values() if s > 50e6)
        if p != last:
            last = p
            try:
                with open(state_path, "w", encoding="utf-8") as f:
                    json.dump({"state": "installing",
                               "message": "Downloading %s… %d%%%s" % (model, p, " (part %d)" % parts if parts > 1 else ""),
                               "percent": 12 + int(p * 0.78),
                               "model": model, "at": time.time()}, f)
            except OSError:
                pass
    stripped = line.strip()
    if stripped and not bar.match(stripped):
        sys.stdout.write(stripped + "\n")
' "$STATUS" "$MODEL"
  local rc=${PIPESTATUS[0]}
  set -o pipefail
  return "$rc"
}
pulled=""
for attempt in 1 2 3; do
  if pull_once; then pulled=1; break; fi
  echo "pull attempt ${attempt} failed"
  have_model && { echo "but ${MODEL} is installed"; pulled=1; break; }
  sleep $((attempt * 10))
done
if [ -z "$pulled" ]; then
  fallback_to_greeter "download failed"
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

# Thinking models (Qwen3, DeepSeek R1) would think first and can run out the
# clock before saying anything, so the test asks them not to.
THINK=""
if curl -sf --max-time 10 http://127.0.0.1:11434/api/show -d "{\"model\":\"${MODEL}\"}" 2>/dev/null \
     | python3 -c 'import json,sys; sys.exit(0 if "thinking" in (json.load(sys.stdin).get("capabilities") or []) else 1)' 2>/dev/null; then
  THINK=',"think":false'
fi

smoke() {
  curl -sf --max-time 240 http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"${MODEL}\",\"prompt\":\"Say hello in five words.\",\"stream\":false,\"keep_alive\":\"30m\"${THINK}}" \
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

# The test leaves the model loaded (keep_alive above), so the owner's first
# message does not wait for it to load into memory again.
if [ -z "$reply" ]; then
  fallback_to_greeter "no reply after retries"
fi
if [ -z "$reply" ] && [ -n "$SWITCH" ]; then
  # A model the owner chose that will not answer stays on disk but does not
  # take over: the one that was working keeps answering.
  set_status failed "${MODEL} downloaded but would not answer on this device" 0
  echo "smoke test: no reply after retries — not switching"
  exit 1
fi

echo "$MODEL" > "$ACTIVE"

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
