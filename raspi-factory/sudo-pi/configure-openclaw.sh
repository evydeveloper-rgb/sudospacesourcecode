#!/bin/bash
# Configure OpenClaw from Sudo device config. Called by configure-picoclaw.sh
# after it has rendered the shared agent workspace, whenever OpenClaw is the
# active brain (see agent_backend in server.py for the same rule).
set -euo pipefail

LOG="/var/log/sudo-openclaw-config.log"
OC_BIN="/opt/openclaw/node_modules/.bin/openclaw"
STATE_DIR="/opt/sudo/openclaw"
OC_CONFIG="$STATE_DIR/openclaw.json"
TOKEN_FILE="/etc/sudo/openclaw_gateway_token"
KEY_HASH_FILE="$STATE_DIR/.openrouter-key.sha256"
UNIT="sudo-openclaw-gateway.service"

exec >> "$LOG" 2>&1
echo "=== configure-openclaw $(date) ==="

if [ ! -x "$OC_BIN" ]; then
  echo "OpenClaw not installed at $OC_BIN — nothing to configure"
  exit 0
fi

mkdir -p "$STATE_DIR" /etc/sudo
chmod 700 "$STATE_DIR"
if [ ! -s "$TOKEN_FILE" ]; then
  python3 -c 'import secrets;print(secrets.token_urlsafe(32))' > "$TOKEN_FILE"
fi
chmod 600 "$TOKEN_FILE"

export OPENCLAW_STATE_DIR="$STATE_DIR" OPENCLAW_CONFIG_PATH="$OC_CONFIG" HOME=/root

# The key goes into OpenClaw's own auth store, piped on stdin. Not argv (other
# processes can read that) and not the gateway's environment: every shell
# command the agent runs inherits that environment, so `env` would print it.
key=$(python3 - << 'PY'
import json
sudo = json.load(open("/opt/sudo/config.json", encoding="utf-8"))
# Must match configure-picoclaw.sh and effective_openrouter_key() in server.py.
pk = sudo.get("provider_keys") or {}
if pk.get("openrouter"):
    print(pk["openrouter"])
elif sudo.get("openrouter_key_source", "sudo") == "own" and sudo.get("user_openrouter_key"):
    print(sudo["user_openrouter_key"])
else:
    print(sudo.get("openrouter_key") or "")
PY
)
if [ -z "$key" ]; then
  echo "No OpenRouter key yet — skip"
  exit 0
fi
key_hash=$(printf '%s' "$key" | sha256sum | cut -d' ' -f1)
if [ ! -f "$OC_CONFIG" ] || [ "$(cat "$KEY_HASH_FILE" 2>/dev/null)" != "$key_hash" ]; then
  # paste-api-key needs a config to update; seed one from the base if missing.
  [ -f "$OC_CONFIG" ] || cp /opt/sudo-openclaw/openclaw.base.json "$OC_CONFIG"
  if printf '%s\n' "$key" | "$OC_BIN" models auth paste-api-key \
       --provider openrouter --profile-id openrouter:default >/dev/null 2>&1; then
    printf '%s' "$key_hash" > "$KEY_HASH_FILE"
    chmod 600 "$KEY_HASH_FILE"
    echo "OpenRouter key stored in OpenClaw's auth store"
  else
    echo "Could not store the OpenRouter key in OpenClaw"
  fi
fi
unset key

python3 << 'PY'
import json, os

sudo = json.load(open("/opt/sudo/config.json", encoding="utf-8"))
cfg = json.load(open("/opt/sudo-openclaw/openclaw.base.json", encoding="utf-8"))
token = open("/etc/sudo/openclaw_gateway_token", encoding="utf-8").read().strip()

# Model aliases are shared with picoclaw's template so the Settings picker and
# both brains agree on what "sudo-default" means.
template = json.load(open("/opt/picoclaw/config.template.json", encoding="utf-8"))
real = {m["model_name"]: m.get("model", "") for m in template.get("model_list", [])}
alias = sudo.get("model") or "sudo-default"
primary = real.get(alias) or real["sudo-default"]

defaults = cfg.setdefault("agents", {}).setdefault("defaults", {})
defaults["workspace"] = "/opt/sudo/agent-workspace"
defaults["model"] = {"primary": primary}
# agents.defaults.models is an allowlist once set, so every alias the picker
# offers has to be in it or switching models silently does nothing.
defaults["models"] = {m: {} for m in real.values() if m.startswith("openrouter/")}

cfg["gateway"]["auth"]["token"] = token

tools = cfg.setdefault("tools", {})
deny = set(tools.get("deny", []))

if not sudo.get("exec_enabled", True):
    deny |= {"exec", "process"}
    print("Shell commands: off")
else:
    print("Shell commands: on")

# Full device access off keeps read/write/edit inside the workspace. Shell
# commands are not path-restricted by OpenClaw, so this switch is about the
# file tools; the Settings warning covers what exec can reach.
full_access = bool(sudo.get("full_access_enabled", False))
tools["fs"] = {"workspaceOnly": not full_access}
print(f"Full device access: {'on' if full_access else 'off (file tools workspace-only)'}")

if not sudo.get("subagent_enabled", False):
    deny |= {"sessions_spawn", "subagents"}
print(f"Helpers (sub-agents): {'on' if sudo.get('subagent_enabled') else 'off'}")

if sudo.get("skills_enabled", False):
    cfg.get("skills", {}).pop("allowBundled", None)
    print("Skills: on")
else:
    print("Skills: off (weather only)")

if sudo.get("composio_api_key") and sudo.get("composio_enabled"):
    print("Connectors (Composio): not wired to OpenClaw yet")

tools["deny"] = sorted(deny)

out = "/opt/sudo/openclaw/openclaw.json"
# OpenClaw stamps its config with a meta block and treats a file without one
# as clobbered, silently restoring its last-good copy (which has no gateway
# token, so every chat then 401s). Carry the stamp over.
try:
    previous = json.load(open(out, encoding="utf-8"))
    for key in ("meta", "wizard"):
        if key in previous:
            cfg[key] = previous[key]
except (OSError, ValueError):
    previous = {}

# Restart only when something actually changed. Do not lean on the gateway's
# hot reload for this: on 2026.6.11 it logs "config change detected
# (tools.deny)" and keeps running exec anyway -- measured on a Pi, the Shell
# switch in Settings only took effect after a restart.
strip = lambda c: {k: v for k, v in c.items() if k not in ("meta", "wizard")}
restart = strip(previous) != strip(cfg)
open("/opt/sudo/openclaw/.restart-needed", "w").write("1" if restart else "0")
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
os.chmod(tmp, 0o600)
os.replace(tmp, out)
print(f"Wrote {out} (model {primary})")
PY

"$OC_BIN" config validate || echo "WARNING: OpenClaw rejected the generated config"

# GitHub access for git under exec, same env file configure-picoclaw.sh keeps.
DROPIN="/etc/systemd/system/${UNIT}.d"
if [ -f /etc/sudo/agent.env ]; then
  mkdir -p "$DROPIN"
  printf '[Service]\nEnvironmentFile=-/etc/sudo/agent.env\n' > "$DROPIN/10-github.env.conf"
else
  rm -f "$DROPIN/10-github.env.conf" 2>/dev/null || true
fi
systemctl daemon-reload 2>/dev/null || true

# One brain at a time: both would otherwise answer the same channels.
systemctl disable --now picoclaw-gateway.service 2>/dev/null || true
systemctl enable "$UNIT" 2>/dev/null || true
if ! systemctl is-active --quiet "$UNIT"; then
  systemctl start "$UNIT" 2>/dev/null || true
elif [ "$(cat "$STATE_DIR/.restart-needed" 2>/dev/null)" = "1" ]; then
  systemctl restart "$UNIT" 2>/dev/null || true
else
  echo "Nothing changed — gateway left running"
fi
echo "=== configure-openclaw complete $(date) ==="
