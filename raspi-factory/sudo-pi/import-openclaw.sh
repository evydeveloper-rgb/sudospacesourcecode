#!/bin/bash
# import-openclaw.sh — move an existing OpenClaw agent into this Sudo.
#
# For someone who already runs an OpenClaw agent on a Pi and moves to Sudo:
# the agent keeps its memory, personality, chat history, scheduled jobs, its
# WhatsApp login and the scripts it runs, instead of starting over.
#
# Input: a package made on another machine by scripts/make-openclaw-import.py
# from the old device's ~/.openclaw, placed on the card's boot partition at
# import/openclaw-import.tar.gz (prepare-sd.sh --import). Runs once, from
# sudo-openclaw-import.service, after OpenClaw itself is installed.
#
#   phase 1 (offline): unpack, put everything in Sudo's places, prefill the
#           owner's settings, then delete the package from the boot partition
#           -- it holds API keys and the WhatsApp login, and the boot
#           partition is FAT: anyone with the card can read it.
#   phase 2 (online):  once the Pi is on Wi-Fi, install what needs a download
#           (WhatsApp plugin, apt packages the scripts use, Tailscale), then
#           apply the settings and start the agent.
#
# What is deliberately NOT carried over: the old gateway settings (Sudo owns
# them), the old plugin installs (reinstalled at Sudo's pinned version),
# messages still queued for delivery (they would arrive weeks late), and the
# memory search index (rebuilt from the files).
set -uo pipefail

PKG="${SUDO_IMPORT_PKG:-/boot/firmware/import/openclaw-import.tar.gz}"
WORK=/var/lib/sudo-openclaw-import
DONE=/var/lib/sudo-openclaw-imported
LOG=/var/log/sudo-openclaw-import.log
OC_BIN=/opt/openclaw/node_modules/.bin/openclaw

exec >> "$LOG" 2>&1
echo "=== import-openclaw $(date) ==="
[ -f "$DONE" ] && { echo "Already imported"; exit 0; }

# OpenClaw has to be on the device first: its state directory is what we fill.
for _ in $(seq 1 120); do [ -x "$OC_BIN" ] && break; sleep 15; done
[ -x "$OC_BIN" ] || { echo "OpenClaw is not installed yet -- will retry"; exit 1; }

umask 077
mkdir -p "$WORK"

# ── Phase 1: offline ────────────────────────────────────────────────────────
if [ ! -f "$WORK/phase1-done" ]; then
  [ -f "$PKG" ] || { echo "No package at $PKG and phase 1 never ran -- nothing to do"; touch "$DONE"; exit 0; }
  rm -rf "$WORK/pkg" && mkdir -p "$WORK/pkg"
  tar -xzf "$PKG" -C "$WORK/pkg" || { echo "Could not unpack $PKG"; exit 1; }
  systemctl stop sudo-openclaw-gateway.service 2>/dev/null || true

  WORK="$WORK" python3 << 'PY' || { echo "phase 1 failed"; exit 1; }
import json, os, re, shutil, sqlite3, subprocess, time

work = os.environ["WORK"]
pkg = os.path.join(work, "pkg")
m = json.load(open(os.path.join(pkg, "manifest.json"), encoding="utf-8"))
STATE, WS, CONFIG = "/opt/sudo/openclaw", "/opt/sudo/agent-workspace", "/opt/sudo/config.json"
SUDO_BEGIN, SUDO_END = "<!-- sudo:begin -->", "<!-- sudo:end -->"
os.makedirs(STATE, exist_ok=True)
os.makedirs(WS, exist_ok=True)


def copy_tree(src, dst):
    if os.path.isdir(src):
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
        print(f"  {os.path.relpath(src, pkg)} -> {dst}")


# The agent's workspace, as it was. AGENTS.md and TOOLS.md become the owner's
# part of Sudo's files: Sudo's own guidance goes between the markers above
# them on the next configure, and theirs is never touched.
copy_tree(os.path.join(pkg, "workspace"), WS)
for name in ("AGENTS.md", "TOOLS.md", "HEARTBEAT.md"):
    p = os.path.join(WS, name)
    if os.path.isfile(p):
        text = open(p, encoding="utf-8").read()
        if SUDO_BEGIN not in text:
            open(p, "w", encoding="utf-8").write(f"{SUDO_BEGIN}\n{SUDO_END}\n\n{text}")
# An existing agent is past its first conversation, and its own files are its.
json.dump({"agent_owned": True, "bootstrap_written": True,
           "agent_name": m.get("agent_name") or "", "user_name": m.get("user_name") or ""},
          open("/var/lib/sudo-agent-files.json", "w"))
bootstrap = os.path.join(WS, "BOOTSTRAP.md")
if os.path.exists(bootstrap) and not os.path.exists(os.path.join(pkg, "workspace", "BOOTSTRAP.md")):
    os.remove(bootstrap)

# Chat history, the agent's own database (model auth), media, plugin skills.
for d in ("agents", "media", "plugin-skills"):
    copy_tree(os.path.join(pkg, "state", d), os.path.join(STATE, d))
# The WhatsApp login, as Sudo's "agent's own number" account.
src_acct = m.get("whatsapp_account")
if src_acct:
    copy_tree(os.path.join(pkg, "state", "credentials", "whatsapp", src_acct),
              os.path.join(STATE, "credentials", "whatsapp", "agent"))

# Scheduled jobs and their history live in the state database. Keep it, minus
# what belongs to the old device: queued deliveries, inbound replay queue,
# plugin install records (reinstalled here), restart bookkeeping.
db_src = os.path.join(pkg, "state", "state", "openclaw.sqlite")
if os.path.isfile(db_src):
    os.makedirs(os.path.join(STATE, "state"), exist_ok=True)
    db_dst = os.path.join(STATE, "state", "openclaw.sqlite")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(db_dst + ext):
            os.replace(db_dst + ext, db_dst + ext + f".pre-import-{int(time.time())}")
    shutil.copy2(db_src, db_dst)
    c = sqlite3.connect(db_dst)
    tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    for t in ("delivery_queue_entries", "channel_ingress_events", "installed_plugin_index",
              "gateway_restart_handoff", "gateway_restart_intent", "gateway_restart_sentinel"):
        if t in tables:
            n = c.execute(f'delete from "{t}"').rowcount
            print(f"  state db: cleared {n} row(s) from {t}")
    # Jobs are filed under the old device's state path ("<state>/cron/jobs.json"),
    # and the gateway only reads the ones under its own.
    old_state_dir = (m.get("old_state_dir") or "").rstrip("/")
    if old_state_dir:
        for t in tables:
            cols = [r[1] for r in c.execute(f'pragma table_info("{t}")')]
            if "store_key" in cols:
                n = c.execute(f'update "{t}" set store_key = replace(store_key, ?, ?) where store_key like ?',
                              (old_state_dir, STATE, old_state_dir + "%")).rowcount
                if n:
                    print(f"  state db: re-filed {n} row(s) in {t} under {STATE}")
    if "cron_jobs" in tables:
        jobs = [r[0] for r in c.execute("select name from cron_jobs")]
        print(f"  state db: {len(jobs)} scheduled job(s): {', '.join(jobs)}")
    c.commit()
    c.close()

# Their openclaw.json, minus what Sudo owns, becomes the file Sudo's configure
# merges into: their extra plugins, models and search settings carry on.
oc_src = os.path.join(pkg, "openclaw.json")
if os.path.isfile(oc_src):
    oc = json.load(open(oc_src, encoding="utf-8"))
    oc.pop("gateway", None)
    (oc.get("channels") or {}).pop("whatsapp", None)
    oc.setdefault("agents", {}).setdefault("defaults", {})["workspace"] = WS
    plugins = oc.setdefault("plugins", {})
    enabled = [k for k, v in (plugins.get("entries") or {}).items() if (v or {}).get("enabled", True)]
    plugins["allow"] = list(dict.fromkeys((plugins.get("allow") or []) + enabled))
    out = os.path.join(STATE, "openclaw.json")
    try:
        mine = json.load(open(out, encoding="utf-8"))
        for k in ("meta", "wizard", "gateway"):
            if k in mine:
                oc[k] = mine[k]
    except (OSError, ValueError):
        pass
    json.dump(oc, open(out, "w", encoding="utf-8"), indent=2)
    print(f"  openclaw.json seeded from the old device ({len(plugins['allow'])} plugin(s) allowed)")

# Settings the owner would otherwise type again during setup. Onboarding still
# runs and shows these, so they can change them.
try:
    cfg = json.load(open(CONFIG, encoding="utf-8"))
except (OSError, ValueError):
    cfg = {}
for key in ("agent_name", "user_name"):
    if m.get(key) and not cfg.get(key):
        cfg[key] = m[key]
if m.get("openrouter_key"):
    cfg.setdefault("provider_keys", {})["openrouter"] = m["openrouter_key"]
if m.get("model_alias"):
    cfg["model"] = m["model_alias"]
if m.get("owner_number"):
    cfg["whatsapp_owner_number"] = m["owner_number"]
if m.get("agent_number"):
    cfg.setdefault("whatsapp_numbers", {})["agent"] = m["agent_number"]
os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
json.dump(cfg, open(CONFIG + ".tmp", "w", encoding="utf-8"), indent=2)
os.chmod(CONFIG + ".tmp", 0o600)
os.replace(CONFIG + ".tmp", CONFIG)
print("  owner settings prefilled: " + ", ".join(k for k in ("agent_name", "user_name", "provider_keys", "model", "whatsapp_owner_number", "whatsapp_numbers") if k in cfg))

# Scripts and notes written on the old device name its paths. Point those
# paths at where things live now, instead of rewriting anyone's files.
old_state = (m.get("old_state_dir") or "").rstrip("/")
if old_state and old_state.startswith("/home/") and not os.path.exists(old_state):
    os.makedirs(old_state, exist_ok=True)
    for name, target in (("workspace", WS), ("agents", f"{STATE}/agents"), ("media", f"{STATE}/media")):
        link = os.path.join(old_state, name)
        if not os.path.lexists(link):
            os.symlink(target, link)
    print(f"  {old_state}/{{workspace,agents,media}} now point at Sudo's folders")

# Their own scheduled scripts (crontab), run as root like the agent itself.
lines = [l for l in (m.get("crontab") or []) if l.strip() and not l.lstrip().startswith("#")]
if lines:
    with open("/etc/cron.d/sudo-imported", "w") as f:
        f.write("# Brought over from the previous OpenClaw device by import-openclaw.sh\n")
        f.write("SHELL=/bin/bash\nPATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n")
        for l in lines:
            parts = l.split()
            if parts[0].startswith("@"):
                f.write(f"{parts[0]} root {' '.join(parts[1:])}\n")
            else:
                f.write(f"{' '.join(parts[:5])} root {' '.join(parts[5:])}\n")
    os.chmod("/etc/cron.d/sudo-imported", 0o644)
    print(f"  {len(lines)} crontab line(s) -> /etc/cron.d/sudo-imported")

# Downloads wait for phase 2.
json.dump({"apt": m.get("apt") or [], "tailscale": os.path.isdir(os.path.join(pkg, "tailscale")),
           "whatsapp": bool(src_acct)}, open(os.path.join(work, "phase2.json"), "w"))
PY

  if [ -d "$WORK/pkg/tailscale" ]; then
    rm -rf "$WORK/tailscale" && cp -a "$WORK/pkg/tailscale" "$WORK/tailscale"
  fi
  rm -rf "$WORK/pkg"
  rm -f "$PKG"
  rmdir "$(dirname "$PKG")" 2>/dev/null || true
  sync
  touch "$WORK/phase1-done"
  echo "Phase 1 done; package removed from the boot partition"
fi

# ── Phase 2: online ─────────────────────────────────────────────────────────
# Testing the move without touching the network (no plugin, apt or Tailscale).
[ -n "${SUDO_IMPORT_PHASE1_ONLY:-}" ] && { echo "Phase 1 only (test run)"; exit 0; }
echo "Waiting for internet (the owner finishes Wi-Fi setup first)…"
until curl -sfI --max-time 10 https://registry.npmjs.org/ >/dev/null 2>&1; do sleep 30; done

read_p2() { python3 -c "import json,sys;print(json.load(open('$WORK/phase2.json')).get(sys.argv[1]) or '')" "$1"; }
apt_pkgs=$(python3 -c "import json;print(' '.join(json.load(open('$WORK/phase2.json')).get('apt') or []))")
if [ -n "$apt_pkgs" ]; then
  echo "apt: $apt_pkgs"
  DEBIAN_FRONTEND=noninteractive apt-get install -y $apt_pkgs \
    || { apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y $apt_pkgs; } \
    || echo "WARNING: could not install $apt_pkgs"
fi

if [ "$(read_p2 tailscale)" = "True" ] && [ -f "$WORK/tailscale/tailscaled.state" ]; then
  if ! command -v tailscaled >/dev/null 2>&1; then
    echo "Installing Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh || echo "WARNING: Tailscale install failed"
  fi
  if command -v tailscaled >/dev/null 2>&1; then
    systemctl stop tailscaled 2>/dev/null || true
    install -d -m 700 /var/lib/tailscale
    install -m 600 "$WORK/tailscale/tailscaled.state" /var/lib/tailscale/tailscaled.state
    systemctl enable --now tailscaled && echo "Tailscale: same identity as the old device"
    # The old device may have sent everything through an exit node with LAN
    # access off. Kept as is, replies to the home network leave through the
    # tunnel and the dashboard can't be reached from the Wi-Fi it is on. Keep
    # the exit node, but always let the local network through.
    for _ in 1 2 3 4 5 6; do
      tailscale set --exit-node-allow-lan-access=true 2>/dev/null && { echo "Tailscale: local network allowed"; break; }
      sleep 5
    done
  fi
fi

# Plugins the old config uses that this OpenClaw does not ship with (some
# moved out of the core between versions, e.g. the Parallel web search).
# OpenClaw names the official package for each; install it at the core's own
# version so it matches.
export OPENCLAW_STATE_DIR=/opt/sudo/openclaw OPENCLAW_CONFIG_PATH=/opt/sudo/openclaw/openclaw.json HOME=/root
core_ver=$(python3 -c "import json;print(json.load(open('/opt/openclaw/node_modules/openclaw/package.json'))['version'])")
for spec in $("$OC_BIN" config validate 2>&1 | grep -oE "openclaw plugins install @openclaw/[a-z0-9-]+" | awk '{print $4}' | sort -u); do
  echo "Installing missing plugin $spec@$core_ver"
  "$OC_BIN" plugins install "$spec@$core_ver" || echo "WARNING: could not install $spec"
done

if [ "$(read_p2 whatsapp)" = "True" ]; then
  # Installs the plugin at Sudo's pinned version, then runs configure.
  /usr/local/bin/sudo-install-openclaw-whatsapp.sh || echo "WARNING: WhatsApp plugin install reported a problem"
else
  /usr/local/bin/sudo-configure-agent.sh || true
fi

rm -rf "$WORK"
touch "$DONE"
echo "=== import-openclaw complete $(date) ==="
