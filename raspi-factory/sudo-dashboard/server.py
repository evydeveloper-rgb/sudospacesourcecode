#!/usr/bin/env python3
"""Sudo Dashboard — local UI (rate limits, CORS, safe bind).

Factory-safe: secrets generated offline; no internet required to serve.
Public mode (optional): bind 127.0.0.1:8080 behind Caddy HTTPS.
"""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie

PORT = int(os.environ.get("SUDO_DASH_PORT", "80"))
BIND = os.environ.get("SUDO_DASH_BIND", "0.0.0.0")
ROOT = "/opt/sudo-dashboard"
CONFIG = "/opt/sudo/config.json"
WIFI_MARKER = "/var/lib/wifi-setup-configured"
REMOTE_MARKER = "/var/lib/sudo-remote-enabled"
PUBLIC_URL_FILE = "/etc/sudo/public_url"
DEVICE_ID_FILE = "/etc/sudo/device_id"
DEVICE_SECRET_FILE = "/etc/sudo/device_secret"
PASS_HASH_FILE = "/etc/sudo/dashboard_password.hash"
PASS_FILE = "/etc/sudo/dashboard_password"
API_URL_FILE = "/etc/sudo/api.url"
WIFI_COUNTRY_FILE = "/etc/sudo/wifi_country"
SSH_PASSWORD_FILE = "/etc/sudo/ssh_password"
# Sorts before install-factory.sh's 10-sudo-dev.conf. sshd takes the FIRST
# value it sees for a keyword, so what the owner chooses in the dashboard
# always beats the dev-flash default.
SSHD_DROPIN = "/etc/ssh/sshd_config.d/05-sudo-user.conf"
SSH_KEY_PREFIXES = ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-", "sk-ssh-ed25519", "sk-ecdsa-sha2-")
DEVTOOLS_SCRIPT = "/usr/local/bin/sudo-install-devtools.sh"
DEVTOOLS_STATUS = "/var/lib/sudo-devtools-status"
# CLI tools the owner can install on demand. Keys must match the case in
# sudo-pi/install-devtools.sh.
DEVTOOLS = {"claude": ("Claude Code", "claude"), "codex": ("Codex", "codex")}
WORKSPACE = "/opt/sudo/agent-workspace"
# The files that make up the agent's identity and memory. Fixed list rather
# than a directory walk, so a stray file can never be served.
AGENT_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md", "HEARTBEAT.md",
               "BOOTSTRAP.md", "RUNTIME.md", "memory/MEMORY.md"]
# Installing Node takes minutes, so the job runs detached and the dashboard
# polls this status file rather than holding an HTTP request open.
DEVTOOLS_STALE_SECS = 15 * 60

# Model aliases defined in picoclaw/config.template.json. Keep this set, the
# dashboard's <select>, and the config template in sync — a value outside this
# set resolves to no model and the agent silently stops responding.
ALLOWED_MODELS = {"sudo-default", "sudo-fast", "sudo-smart", "sudo-budget"}
DEFAULT_MODEL = "sudo-default"
# Dashboard colour schemes. Must match the [data-theme=...] blocks in app.css
# and the options in the Settings picker.
ALLOWED_THEMES = {"default", "minimal", "terminal", "midnight"}
DEFAULT_THEME = "default"
REMOTE_SCRIPT = "/usr/local/bin/sudo-remote-access.sh"
PICOCLAW_BIN = "/usr/local/bin/picoclaw"
PICOCLAW_CONFIG = "/opt/sudo/picoclaw/config.json"
CONFIGURE_SCRIPT = "/usr/local/bin/sudo-configure-picoclaw.sh"

LOCALMODEL_STATUS = "/var/lib/sudo-localmodel-status"
LOCALMODEL_SCRIPT = "/usr/local/bin/sudo-install-local-model.sh"
OLLAMA_URL = "http://127.0.0.1:11434"
LOCAL_MODEL = "gemma3:270m"

WHATSAPP_STATUS = "/var/lib/sudo-whatsapp-status.json"
WHATSAPP_INSTALL_SCRIPT = "/usr/local/bin/sudo-install-whatsapp-bridge.sh"
WHATSAPP_UNLINK_SCRIPT = "/usr/local/bin/sudo-unlink-whatsapp.sh"
# Small enough to answer in about a second on a 4GB Pi. It greets and keeps
# the box talking when there is no cloud key; it is not asked to know things.
# How many turns of context the on-device model gets. Small models lose the
# thread instantly without history -- "but why" came back about something
# else entirely -- but a long window costs tokens it does not have to spare.
LOCAL_HISTORY_TURNS = 6
# Cloud replies keep their thread in picoclaw's own session store rather than
# in a transcript we reassemble on every call. picoclaw's `agent` CLI persists
# each --session key to disk, so passing the caller's session key lets it
# remember the conversation itself -- the same way the gateway path does. The
# old code called `agent -m` with no session, which is why every cloud reply
# was one-shot while the local model (which we do thread) kept context.
DASHBOARD_SESSION = "sudo-dashboard:web"
# The on-device model takes its brief from the block marked SHORT in
# BOOTSTRAP.md, so one file governs both models and they cannot drift
# into behaving like two different assistants.
BOOTSTRAP_FILE = "/opt/sudo/agent-workspace/BOOTSTRAP.md"
# The opening marker carries a note to whoever edits the file, so skip to the
# end of that comment rather than assuming the marker is followed by "-->".
BOOTSTRAP_SHORT = re.compile(r"SHORT-START.*?-->(.*?)<!--\s*SHORT-END", re.S)

PERSONALITY_TONE = {
    "friendly": "warm and easygoing",
    "professional": "crisp and measured",
    "concise": "short, only the words needed",
    "creative": "playful and curious",
}


SESSION_TTL = 7 * 24 * 3600
MAX_CHAT_LEN = 2000
MAX_BODY = 64 * 1024
LOGIN_WINDOW = 60
LOGIN_MAX = 8
CHAT_WINDOW = 60
CHAT_MAX = 20
COOKIE_NAME = "sudo_session"

# rate-limit state
_lock = threading.Lock()
_buckets: dict[str, list[float]] = {}


def read_json_file(path, default=None):
    if not os.path.isfile(path):
        return default if default is not None else {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default if default is not None else {}


def write_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_text(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def device_secret():
    return read_text(DEVICE_SECRET_FILE) or ""


def read_device_id():
    cfg = read_json_file(CONFIG)
    if cfg.get("device_id"):
        return cfg["device_id"]
    return read_text(DEVICE_ID_FILE)


def read_api_base():
    cfg = read_json_file(CONFIG)
    if cfg.get("api_base"):
        return cfg["api_base"].rstrip("/")
    url = read_text(API_URL_FILE)
    if url:
        return url.rstrip("/")
    return "https://YOUR-API-URL.vercel.app"


def remote_enabled():
    return os.path.isfile(REMOTE_MARKER)


def apply_wifi_country(code):
    """Set the wireless regulatory domain, whatever board this is.

    raspi-config is Raspberry Pi OS only; Orange Pi OS and Armbian have no
    equivalent. `iw reg set` applies now, the config files make it stick.
    Best-effort throughout: a wrong regdomain narrows the channel list, it
    does not stop the device working.
    """
    code = (code or "US").strip().upper()[:2]
    if not code.isalpha():
        return
    if shutil.which("raspi-config"):
        try:
            subprocess.run(["raspi-config", "nonint", "do_wifi_country", code],
                           capture_output=True, timeout=10)
            return
        except Exception:
            pass
    try:
        subprocess.run(["iw", "reg", "set", code], capture_output=True, timeout=10)
    except Exception:
        pass
    for path, body in (
        ("/etc/default/crda", "REGDOMAIN=%s\n" % code),
        ("/etc/modprobe.d/cfg80211.conf", "options cfg80211 ieee80211_regdom=%s\n" % code),
    ):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        except OSError:
            pass


PROVIDERS = ("openrouter", "openai", "anthropic", "gemini", "venice", "deepseek")
ROUTING_MODES = ("auto", "fixed")


# How a pasted key is recognised. Prefixes are the providers' own, and are
# checked longest-first so sk-ant-/sk-proj- cannot be swallowed by a bare sk-.
KEY_PATTERNS = (
    ("openrouter", ("sk-or-v1-", "sk-or-")),
    ("anthropic", ("sk-ant-",)),
    ("gemini", ("aizasy", "aiza")),
    ("deepseek", ("sk-ds-",)),
    ("venice", ("vn_", "sk-venice-")),
    ("openai", ("sk-proj-", "sk-svcacct-", "sk-")),
)


def detect_provider(key):
    """Which provider a pasted key belongs to, or None if unrecognised.

    One box beats six: nobody memorises which prefix belongs to whom, and
    picking the wrong row silently sends the key nowhere useful.
    """
    candidate = (key or "").strip()
    if not candidate:
        return None
    lowered = candidate.lower()
    best, best_len = None, 0
    for provider, prefixes in KEY_PATTERNS:
        for prefix in prefixes:
            if lowered.startswith(prefix) and len(prefix) > best_len:
                best, best_len = provider, len(prefix)
    return best


def effective_openrouter_key(cfg):
    """The key the agent will actually use.

    Must match the precedence in sudo-pi/configure-picoclaw.sh, or the UI
    reports the agent as not ready while it is in fact configured, or the
    reverse. Order: Provider keys page, then the older own-key card, then
    Sudo credits.
    """
    provider = (cfg.get("provider_keys") or {}).get("openrouter")
    if provider:
        return provider
    if cfg.get("openrouter_key_source") == "own" and cfg.get("user_openrouter_key"):
        return cfg["user_openrouter_key"]
    return cfg.get("openrouter_key") or ""


def provider_key_hints(cfg):
    """Last 4 characters of each stored key — never the key itself.

    Enough for someone to recognise which key is in there without the value
    ever leaving the device in a response.
    """
    stored = cfg.get("provider_keys") or {}
    return {
        name: str(value)[-4:]
        for name, value in stored.items()
        if name in PROVIDERS and value
    }


def read_wifi_country():
    return (read_text(WIFI_COUNTRY_FILE) or "US").strip().upper()


def login_user():
    """The human account on this Pi (uid 1000), or (None, None)."""
    try:
        out = subprocess.run(
            ["getent", "passwd", "1000"], capture_output=True, text=True, timeout=3
        )
        parts = out.stdout.strip().split(":")
        if len(parts) >= 6 and parts[0]:
            return parts[0], parts[5]
    except Exception:
        pass
    return None, None


def ssh_enabled():
    try:
        r = subprocess.run(
            ["systemctl", "is-enabled", "ssh"], capture_output=True, text=True, timeout=3
        )
        return r.stdout.strip() == "enabled"
    except Exception:
        return False


def gen_password(length=16):
    # Unambiguous alphabet — no 0/O/1/l/I, so it survives being read aloud
    # or copied off a screen. Same set device-secrets.sh uses.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


SSH_SETUP_SCRIPT = "/usr/local/bin/sudo-setup-ssh.sh"
HOSTNAME_SCRIPT = "/usr/local/bin/sudo-set-hostname.sh"
# RFC 1123, lowercased. Also the shape of a usable mDNS name.
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
SSH_PENDING_KEY = "/etc/sudo/ssh_key_pending"


def run_privileged(unit, script, *args, wait=True, timeout=60):
    """Run a helper outside this service's sandbox.

    The dashboard has ProtectSystem=full and ProtectHome=true, so it cannot
    write /etc/shadow, /etc/ssh or /home itself. systemd-run starts a
    transient unit that does not inherit those restrictions.
    """
    cmd = ["systemd-run", "--quiet", "--collect", f"--unit={unit}"]
    if wait:
        cmd.append("--wait")
    cmd += ["/bin/bash", script, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def set_ssh_password():
    r = run_privileged("sudo-ssh-setup", SSH_SETUP_SCRIPT, "enable-password")
    if r.returncode != 0:
        raise RuntimeError(
            "Could not turn on terminal access — see /var/log/sudo-ssh-setup.log"
        )
    user, _ = login_user()
    pw = read_text(SSH_PASSWORD_FILE)
    if not pw:
        raise RuntimeError("Terminal access is on but the password could not be read")
    return user, pw


def install_ssh_key(public_key: str):
    # Hand the key over on disk rather than argv, so it never shows in ps.
    os.makedirs(os.path.dirname(SSH_PENDING_KEY), exist_ok=True)
    with open(SSH_PENDING_KEY, "w", encoding="utf-8") as f:
        f.write(public_key.strip() + "\n")
    os.chmod(SSH_PENDING_KEY, 0o600)
    r = run_privileged("sudo-ssh-setup", SSH_SETUP_SCRIPT, "enable-key")
    if r.returncode != 0:
        raise RuntimeError("Could not install that key — see /var/log/sudo-ssh-setup.log")
    user, _ = login_user()
    return user


def disable_ssh():
    run_privileged("sudo-ssh-setup", SSH_SETUP_SCRIPT, "disable")


def read_agent_files():
    """The agent's own identity/memory files, for the owner to read."""
    out = []
    for rel in AGENT_FILES:
        path = os.path.join(WORKSPACE, rel)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError:
            continue
        out.append({"name": rel, "bytes": len(body.encode("utf-8")), "content": body})
    return out


def resolve_model(value):
    """Map a stored model alias to one this bundle actually defines.

    Devices flashed with an older bundle may still have a retired alias in
    config.json (e.g. "sudo-minimax"); report the default instead so the UI
    never shows an option that no longer exists.
    """
    return value if value in ALLOWED_MODELS else DEFAULT_MODEL


# What each alias actually is, for telling the owner plainly which model
# answered. Must stay in step with picoclaw/config.template.json.
MODEL_LABELS = {
    "sudo-default": "DeepSeek V4 Flash",
    "sudo-fast": "GPT-4o mini",
    "sudo-budget": "Claude Haiku 4.5",
    "sudo-smart": "Claude Sonnet 4.5",
}
PROVIDER_LABELS = {
    "openrouter": "OpenRouter", "openai": "OpenAI", "anthropic": "Anthropic",
    "gemini": "Google Gemini", "venice": "Venice AI", "deepseek": "DeepSeek",
}


def answering_with(cfg):
    """Which model is actually serving chat, and on whose account.

    The agent cannot inspect its own gateway -- asked what model it is, it
    guesses, and guessed wrong. This is the answer taken from configuration
    rather than from the agent.
    """
    local_ready = (local_model_state() or {}).get("state") == "done"
    # An explicit choice to stay on-device outranks having a key, but only
    # when there is actually something on the device to answer with.
    if cfg.get("prefer_local") and local_ready:
        return {"where": "local", "model": LOCAL_MODEL, "via": "on this device, by choice",
                "label": f"{LOCAL_MODEL} · on this device"}
    if effective_openrouter_key(cfg):
        alias = resolve_model(cfg.get("model"))
        label = MODEL_LABELS.get(alias, alias)
        keys = cfg.get("provider_keys") or {}
        if keys.get("openrouter"):
            whose = "your OpenRouter key"
        elif cfg.get("openrouter_key_source") == "own" and cfg.get("user_openrouter_key"):
            whose = "your own key"
        else:
            whose = "sudo credits"
        return {"where": "cloud", "model": label, "via": whose,
                "label": f"{label} · {whose}"}
    if local_ready:
        return {"where": "local", "model": LOCAL_MODEL, "via": "on this device",
                "label": f"{LOCAL_MODEL} · on this device"}
    return {"where": "none", "model": None, "via": None,
            "label": "No model yet — add a key in Settings"}


def public_url():
    return read_text(PUBLIC_URL_FILE)


def setup_status(include_sensitive=False):
    cfg = read_json_file(CONFIG)
    url = public_url()
    data = {
        "wifi_done": os.path.isfile(WIFI_MARKER),
        "profile_done": bool(cfg.get("profile_done")),
        "cloud_registered": os.path.isfile("/var/lib/sudo-cloud-registered"),
        "agent_ready": os.path.isfile(PICOCLAW_CONFIG) and bool(effective_openrouter_key(cfg)),
        "agent_name": cfg.get("agent_name", "sudo"),
        "user_name": cfg.get("user_name"),
        "personality": cfg.get("personality", "friendly"),
        "model": resolve_model(cfg.get("model")),
        "balance_usd": cfg.get("balance_usd", 0),
        "theme": (cfg.get("theme") if cfg.get("theme") in ALLOWED_THEMES else DEFAULT_THEME),
        # On by default, owner can switch it off. Absent means never set,
        # which is a new device, not a deliberate "no".
        "exec_enabled": bool(cfg.get("exec_enabled", True)),
        "subagent_enabled": bool(cfg.get("subagent_enabled", False)),
        "skills_enabled": bool(cfg.get("skills_enabled", False)),
        # Off by default, unlike exec: this is what lets the agent edit its
        # own dashboard and the rest of the device, not just run commands
        # inside its workspace -- and it is also what lets it read its own
        # config.json, which is where the live OpenRouter/pico/Composio keys
        # live. That is the owner's call to make deliberately, not a default.
        "full_access_enabled": bool(cfg.get("full_access_enabled", False)),
        # On by default, like exec -- the device speaking first is the point.
        # Reflects the systemd timer, which is the single source of truth.
        "heartbeat_enabled": heartbeat_enabled(),
        "openrouter_key_source": (
            "own" if cfg.get("openrouter_key_source") == "own" else "sudo"
        ),
        # Never return the key itself — only enough to recognise it.
        "has_own_key": bool(cfg.get("user_openrouter_key")),
        "own_key_hint": (cfg.get("user_openrouter_key") or "")[-4:] or None,
        "local_model": local_model_state(),
        # Just the state, not the QR image: this rides along on every status
        # poll (focus, visibility, the 15s welcome tick), and there is no
        # reason to ship a data-URI image on all of those. The channels page
        # polls whatsapp_state() directly for the real thing while it is open.
        "whatsapp_connected": whatsapp_state()["state"] == "connected",
        "prefer_local": bool(cfg.get("prefer_local")),
        "remote_password_enabled": bool(cfg.get("remote_password_enabled", True)),
        "has_dashboard_password": bool(
            read_text(PASS_HASH_FILE) or read_text(PASS_FILE)
        ),
        "answering_with": answering_with(cfg),
        "provider_key_hints": provider_key_hints(cfg),
        "has_composio": bool(cfg.get("composio_api_key")),
        "composio_enabled": bool(cfg.get("composio_enabled")),
        "routing_mode": (
            cfg.get("routing_mode") if cfg.get("routing_mode") in ROUTING_MODES else "auto"
        ),
        "country": read_wifi_country(),
        "remote_access": bool(cfg.get("remote_access", False)),
        "remote_enabled": remote_enabled(),
        "public_url": url,
        "remote_starting": remote_enabled() and not url,
        "auth_required": False,
    }
    if include_sensitive:
        data["device_id"] = read_device_id()
        data["api_base"] = read_api_base()
    return data


def run_remote_access(enable: bool):
    script = REMOTE_SCRIPT
    if not os.path.isfile(script):
        return
    cmd = "enable" if enable else "disable"
    subprocess.Popen(
        [script, cmd],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


HEARTBEAT_TIMER = "sudo-heartbeat.timer"
OWNER_FILE = "/opt/sudo/whatsapp-auth/owner.json"


def heartbeat_enabled():
    """Whether the proactive heartbeat timer is running right now.

    Reads systemd rather than a config flag, so the Settings switch and the
    unit can never disagree -- the state lives in exactly one place.
    """
    try:
        r = subprocess.run(
            ["systemctl", "is-enabled", HEARTBEAT_TIMER],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout.strip() == "enabled"
    except Exception:
        return False


def run_heartbeat(enable: bool):
    try:
        cmd = "enable" if enable else "disable"
        # --now so the switch takes effect immediately, not at next boot.
        subprocess.run(
            ["systemctl", cmd, "--now", HEARTBEAT_TIMER],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def owner_session_key():
    """The picoclaw session the owner's own WhatsApp thread uses.

    Must match the key the bridge hands picoclaw on each message
    (`sudo-whatsapp:<owner jid>`), or the heartbeat's context gate looks in
    the wrong file and concludes nobody has talked to the device -- and so
    never nudges anyone.
    """
    try:
        with open(OWNER_FILE, encoding="utf-8") as f:
            jid = (json.load(f) or {}).get("jid")
        if jid:
            return "sudo-whatsapp:" + str(jid).replace(":", "_")
    except Exception:
        pass
    return "sudo-whatsapp:main"


def run_configure_picoclaw():
    if os.path.isfile(CONFIGURE_SCRIPT):
        subprocess.Popen(
            [CONFIGURE_SCRIPT],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def local_system_prompt(cfg):
    """The on-device model's brief: welcome someone who just unboxed this.

    This model is the first thing a new owner talks to, so its job is the
    first conversation -- meet them, take an interest, get them going.

    Nothing here says what it cannot do. Two earlier versions listed limits
    and a 270M model cannot paraphrase: it recited the list on every turn,
    including when asked what 1+1 is, and eventually contradicted itself
    inside one sentence. A model told what it is not spends its whole
    vocabulary saying so. What it cannot do belongs in the interface, where
    it can be stated once and accurately, not in the mouth of the smallest
    model in the building.
    """
    agent = (cfg.get("agent_name") or "sudo").strip()[:32]
    user = (cfg.get("user_name") or "").strip()[:64]
    tone = PERSONALITY_TONE.get(cfg.get("personality"), PERSONALITY_TONE["friendly"])
    whose = f"{user}'s home" if user else "someone's home"

    # BOOTSTRAP.md is the source when it is there. Editing the agent's own
    # files should change how it behaves, including the small model.
    try:
        with open(BOOTSTRAP_FILE, encoding="utf-8") as handle:
            match = BOOTSTRAP_SHORT.search(handle.read())
        if match:
            block = " ".join(match.group(1).split())
            if block:
                block = block.replace("{{AGENT_NAME}}", agent)
                block = block.replace("{{USER_NAME}}", user or "someone")
                return block + "\nBe " + tone + "."
    except OSError:
        pass

    # Fallback for a device whose workspace has not been rendered yet.
    return (
        f"You are {agent}, just set up on a small computer in {whose}. "
        f"You are meeting for the first time. Be {tone}.\n"
        "Keep every reply to one or two short sentences, like a text message.\n"
        "Take an interest in them: what they do, what they are working on, "
        "what they would like a hand with. Ask one thing at a time.\n"
        "Answer whatever they ask simply and directly, then keep the "
        "conversation going."
    )


def local_model_state():
    """Whether the on-device model is installed and answering."""
    data = read_json_file(LOCALMODEL_STATUS, {}) or {}
    return {
        "state": data.get("state", "absent"),
        "message": data.get("message", ""),
        "model": LOCAL_MODEL,
    }


def whatsapp_state():
    """Where things stand with the WhatsApp bridge, straight from its own
    status file. It writes this itself -- see whatsapp-bridge/bridge.js --
    so this is a read, not a computation. 'absent' covers both "never set
    up" and "unlinked": the dashboard doesn't need to tell those apart, the
    action is the same either way.
    """
    data = read_json_file(WHATSAPP_STATUS, {}) or {}
    return {
        "state": data.get("state", "absent"),
        "message": data.get("message", ""),
        "qr": data.get("qr"),
        "number": data.get("number"),
    }


def local_model_chat(message, history=None, cfg=None):
    """Ask the on-device model. Returns None if it cannot answer.

    Never raises: this is the fallback path, so a failure here has to read as
    'no local model' rather than taking the chat request down with it.

    Uses the chat endpoint rather than a bare prompt so the last few turns
    come along. Without them the model answered "but why" as though it were
    the opening line of a new conversation.
    """
    cfg = cfg if cfg is not None else read_json_file(CONFIG)
    messages = [{"role": "system", "content": local_system_prompt(cfg)}]
    for turn in (history or [])[-LOCAL_HISTORY_TURNS:]:
        role = "assistant" if turn.get("role") == "agent" else "user"
        text = str(turn.get("text") or "").strip()[:MAX_CHAT_LEN]
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": message})

    payload = json.dumps({
        "model": LOCAL_MODEL,
        "messages": messages,
        "stream": False,
        # Keep it resident between messages, or every reply pays the load cost
        # again -- on a Pi that is the difference between 1s and 10s.
        "keep_alive": "30m",
        "options": {"num_predict": 120, "temperature": 0.6},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
        reply = ((data.get("message") or {}).get("content") or "").strip()
        return reply or None
    except Exception:
        return None


# Openers picoclaw uses when it starts narrating instead of answering.
# Matched only at the very start of a reply, and only alongside a run-on join,
# so an answer that happens to begin "Let me check that for you" is safe.
NARRATION_OPENERS = re.compile(
    r"^(the user (is |wants|asks|said|needs)|let me |let's |i'll (answer|be|check|explain)|"
    r"i need to |i should |okay,? let me |first,? let me |i don't have |i'm not sure where)",
    re.IGNORECASE,
)


def split_run_on(text: str) -> str:
    """Drop leading narration that has been run into the answer.

    picoclaw does not always mark it. The reply that prompted this was
    "The user asks why I can't see my model. Let me answer honestly and
    simplyHonest answer: I just don't..." -- no lobster anywhere, and the join
    is a missing space, not punctuation.

    Two signals have to agree before anything is cut: the text opens like
    narration, and there is a lowercase-then-uppercase run-on. Requiring both
    is what keeps "I'm picoclaw, running on Go 1.25.11" intact -- that opens
    innocently and has no join -- and stops CamelCase words like "DeepSeek"
    from splitting a legitimate sentence.
    """
    if not NARRATION_OPENERS.match(text.strip()):
        return text
    match = re.search(r"[a-z,;:]([A-Z])", text)
    if not match:
        return text
    tail = text[match.start() + 1:].strip()
    # Only trust it if what follows looks like a real reply rather than a
    # fragment of a split word.
    return tail if len(tail) >= 12 else text


def strip_narration(line: str) -> str:
    """Pull the answer out of a lobster-prefixed line, or return "" if it is
    all narration.

    picoclaw sometimes runs its working-out straight into the reply with no
    separator: "Let me answer honestly.I'm picoclaw...". The join is a
    sentence end immediately followed by a capital, with no space.

    Only that punctuation boundary is trusted. A plain lowercase-to-uppercase
    rule looks tempting -- it would also catch "...matching theirCool, chill
    day" -- but it fires inside every CamelCase word, and the narration is
    full of them ("DeepSeek", "Settings"), so it would cut answers in half.
    When no boundary is found the marker alone is removed and the text kept:
    showing a little narration is a much smaller failure than eating the
    reply.
    """
    body = line[1:].strip()
    match = re.search(r"[.!?;]([A-Z])", body)
    if match:
        tail = body[match.start() + 1:].strip()
        # A boundary at the very end is sentence punctuation, not a join.
        if len(tail) >= 2:
            return tail
        return ""
    return body


def clean_agent_text(text: str) -> str:
    """Strip ANSI colors / CLI banners so chat UI stays readable."""
    if not text:
        return ""
    # CSI / OSC escape sequences
    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\][^\x07]*\x07", "", text)
    text = re.sub(r"\x1b[@-Z\\-_]", "", text)
    # Literal CSI leftovers like [1;38;2;213;70;70m when ESC was lost
    text = re.sub(r"\[[\d;]*m", "", text)
    # Box-drawing banner lines
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(("text", ""))
            continue
        box = sum(1 for ch in stripped if ch in "╔╗╚╝═║─│┌┐└┘├┤┬┴┼█▓▒░▀▄")
        if box >= max(3, len(stripped) // 3):
            continue
        if stripped.lower().startswith(("picoclaw", "gateway", "log level", "loading config")):
            continue
        # picoclaw narrates its own reasoning on lines led by the lobster —
        # "Let me answer honestly.", "The user wants me to check...". That is
        # working-out, not an answer, and showing it makes the agent look like
        # it is talking to itself in front of the user.
        # Marked, not resolved: whether a narration line can be dropped
        # depends on whether the message says anything else.
        if stripped.startswith("\U0001F99E"):
            lines.append(("narration", stripped))
            continue
        lines.append(("text", line))

    # Dropping narration is only safe when there is a real answer beside it.
    # If narration is all the agent produced, an odd reply still beats an
    # empty bubble.
    # Unmarked narration first: it can appear on an ordinary line with no
    # lobster to key off.
    lines = [(kind, split_run_on(value) if kind == "text" else value)
             for kind, value in lines]
    has_answer = any(kind == "text" and value.strip() for kind, value in lines)
    resolved = []
    for kind, value in lines:
        if kind != "narration":
            resolved.append(value)
            continue
        rest = strip_narration(value)
        if rest and rest != value[1:].strip():
            resolved.append(rest)            # an answer was joined onto it
        elif not has_answer:
            resolved.append(rest or value)   # nothing else to show
    cleaned = "\n".join(resolved).strip()
    # Drop leading lobster/banner fluff if a real greeting follows later
    return cleaned or text.strip()


def picoclaw_chat(message: str, session_key: str = DASHBOARD_SESSION) -> str:
    if not os.path.isfile(PICOCLAW_BIN):
        raise RuntimeError("PicoClaw not installed on this device")
    if not os.path.isfile(PICOCLAW_CONFIG):
        # The agent config is written by configure-picoclaw.sh once cloud
        # registration has produced an OpenRouter key. If the user reaches chat
        # before that lands, build it now rather than making them go and press
        # Save in Settings to trigger it as a side effect.
        if read_json_file(CONFIG).get("openrouter_key") and os.path.isfile(CONFIGURE_SCRIPT):
            subprocess.run([CONFIGURE_SCRIPT], capture_output=True, timeout=60)
    if not os.path.isfile(PICOCLAW_CONFIG):
        if not read_json_file(CONFIG).get("openrouter_key"):
            raise RuntimeError(
                "Still setting up — this device is registering with Sudo. "
                "Give it a moment and try again."
            )
        raise RuntimeError("Agent not configured yet — see /var/log/sudo-picoclaw-config.log")

    env = os.environ.copy()
    env["HOME"] = "/root"
    env["PICOCLAW_CONFIG"] = PICOCLAW_CONFIG
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["PICOCLAW_LOG_LEVEL"] = "error"

    proc = subprocess.run(
        [PICOCLAW_BIN, "agent", "-m", message, "--session", session_key],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd="/opt/sudo/agent-workspace",
    )
    if proc.returncode != 0:
        err = clean_agent_text(proc.stderr or proc.stdout or "PicoClaw error")
        if "insufficient" in err.lower() or "credit" in err.lower():
            raise RuntimeError("Insufficient credits — top up at /billing.html")
        raise RuntimeError(err[:500])
    return clean_agent_text(proc.stdout or proc.stderr or "") or "(empty response)"


def remote_password_required() -> bool:
    """Whether the public link asks for a password. On unless switched off.

    Also off when no password has been set at all, since demanding one nobody
    knows would lock the owner out of their own device.
    """
    cfg = read_json_file(CONFIG)
    if not cfg.get("remote_password_enabled", True):
        return False
    return bool(read_text(PASS_HASH_FILE) or read_text(PASS_FILE))


def verify_password(password: str) -> bool:
    raw = read_text(PASS_HASH_FILE)
    if not raw:
        # Fallback: compare plaintext file (first boot edge case)
        stored = read_text(PASS_FILE)
        if not stored:
            return False
        return hmac.compare_digest(stored, password)

    try:
        algo, iters_s, salt_hex, hash_hex = raw.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
        return hmac.compare_digest(dk, expected)
    except (ValueError, TypeError):
        return False


def set_password(password: str):
    if len(password) < 10 or len(password) > 128:
        raise ValueError("Password must be 10–128 characters")
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    line = f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}\n"
    os.makedirs("/etc/sudo", exist_ok=True)
    tmp = PASS_HASH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(line)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PASS_HASH_FILE)
    # Remove plaintext password after change
    try:
        if os.path.isfile(PASS_FILE):
            os.remove(PASS_FILE)
    except OSError:
        pass


def session_secret_key() -> bytes:
    sec = device_secret()
    if not sec:
        # Should not happen after factory install; fail closed for signing
        sec = "missing-device-secret"
    return sec.encode("utf-8")


def make_session_token() -> str:
    exp = int(time.time()) + SESSION_TTL
    nonce = secrets.token_hex(16)
    payload = f"{exp}.{nonce}"
    sig = hmac.new(session_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> bool:
    if not token or token.count(".") != 2:
        return False
    exp_s, nonce, sig = token.split(".", 2)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    if len(nonce) < 16 or len(sig) != 64:
        return False
    payload = f"{exp_s}.{nonce}"
    expected = hmac.new(session_secret_key(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def rate_allow(key: str, limit: int, window: float) -> bool:
    now = time.time()
    with _lock:
        bucket = _buckets.setdefault(key, [])
        cutoff = now - window
        bucket[:] = [t for t in bucket if t >= cutoff]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def is_safe_relpath(rel: str) -> bool:
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = rel.split("/")
    return ".." not in parts and all(p for p in parts)


def cloud_headers():
    did = read_device_id()
    sec = device_secret()
    headers = {"Content-Type": "application/json", "User-Agent": "sudo-dashboard/1.0"}
    if did:
        headers["X-Device-Id"] = did
    if sec:
        headers["X-Device-Secret"] = sec
    return headers


def cloud_request(method: str, path: str, body: dict | None = None, timeout: int = 30):
    url = read_api_base() + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=cloud_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else {"error": str(exc)}
        except json.JSONDecodeError:
            payload = {"error": raw[:300] or str(exc)}
        return exc.code, payload
    except Exception as exc:
        return 502, {"error": str(exc)}


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SudoDashboard/2"

    def log_message(self, format, *args):
        # Keep minimal access log without leaking secrets
        sys_stderr = getattr(self, "_stderr_log", None)
        msg = "%s - %s" % (self.client_address[0], format % args)
        try:
            with open("/var/log/sudo-dashboard.log", "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
        except OSError:
            pass
        if sys_stderr:
            pass

    def via_tunnel(self) -> bool:
        """True when this request arrived over the public Cloudflare link.

        Local requests hit the Pi directly and carry no forwarding headers;
        cloudflared adds them and the Host becomes *.trycloudflare.com.
        """
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host.endswith(".trycloudflare.com"):
            return True
        pub = (public_url() or "").lower()
        if pub and host and host in pub:
            return True
        return bool(self.headers.get("X-Forwarded-For") or self.headers.get("Cf-Connecting-Ip"))

    def client_ip(self) -> str:
        # Prefer proxy header only when behind local Caddy
        xff = self.headers.get("X-Forwarded-For", "")
        if remote_enabled() and xff:
            return xff.split(",")[0].strip()[:64]
        return self.client_address[0]

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cache-Control", "no-store")
        # Strict CORS: same-origin only (browser same-origin requests omit CORS)
        origin = self.headers.get("Origin")
        if origin:
            # Only allow our own public domain or local hostnames
            allowed = False
            host = (self.headers.get("Host") or "").split(":")[0].lower()
            pub = (public_url() or "").lower()
            try:
                o = urllib.parse.urlparse(origin)
                ohost = (o.hostname or "").lower()
                if ohost in ("localhost", "127.0.0.1", host) or ohost.endswith(".local"):
                    allowed = True
                if pub and ohost in pub:
                    allowed = True
                if ohost.endswith(".trycloudflare.com"):
                    allowed = True
            except Exception:
                allowed = False
            if allowed:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, Authorization",
                )

    def send_bytes(self, data, code=200, content_type="text/html; charset=utf-8", extra_headers=None):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.security_headers()
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, code=200, extra_headers=None):
        # Never cache API replies. Device state is shared between every phone
        # and laptop pointed at this box, so a cached /api/setup/status leaves
        # one of them insisting setup has not happened while another has
        # already finished it.
        headers = [("Cache-Control", "no-store, must-revalidate"), ("Pragma", "no-cache")]
        if extra_headers:
            headers.extend(extra_headers)
        self.send_bytes(
            json.dumps(data).encode("utf-8"),
            code=code,
            content_type="application/json",
            extra_headers=headers,
        )

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("Body too large")
        if length == 0:
            return b""
        return self.rfile.read(length)

    def read_body_json(self):
        raw = self.read_body().decode("utf-8")
        return json.loads(raw) if raw else {}

    def redirect(self, url, code=302):
        self.send_response(code)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.security_headers()
        self.end_headers()

    def get_cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        if name in c:
            return c[name].value
        return None

    def session_ok(self) -> bool:
        # Bearer token (for API clients) or cookie
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
            return verify_session_token(token)
        token = self.get_cookie(COOKIE_NAME)
        return verify_session_token(token or "")

    def require_auth(self) -> bool:
        """Passwordless at home; a password over the public link.

        On the home network the door is already the front door -- someone has
        to be on the Wi-Fi. The Cloudflare link has no such door: it is a URL
        that works from anywhere, so it is the one place a password earns its
        keep. The owner can switch it off, and is told plainly what that means.
        """
        if not self.via_tunnel():
            return True
        if not remote_password_required():
            return True
        if self.session_ok():
            return True
        self.send_json({"error": "Password required", "auth_required": True}, code=401)
        return False

    def session_cookie_header(self, token: str) -> str:
        secure = remote_enabled() or (self.headers.get("X-Forwarded-Proto") == "https")
        parts = [
            f"{COOKIE_NAME}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Strict",
            f"Max-Age={SESSION_TTL}",
        ]
        if secure:
            parts.append("Secure")
        return "; ".join(parts)

    def clear_cookie_header(self) -> str:
        return f"{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"

    def handle_api(self, path: str) -> bool:
        # Public health (no secrets)
        if path == "/api/health":
            self.send_json({"ok": True, "remote": remote_enabled()})
            return True

        # Login / logout — rate limited
        if path == "/api/auth/login" and self.command == "POST":
            ip = self.client_ip()
            if not rate_allow(f"login:{ip}", LOGIN_MAX, LOGIN_WINDOW):
                self.send_json({"error": "Too many login attempts"}, code=429)
                return True
            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True
            password = str(body.get("password") or "")
            # Constant-time-ish: always verify
            ok = verify_password(password)
            if not ok:
                time.sleep(0.4)
                self.send_json({"error": "Invalid password"}, code=401)
                return True
            token = make_session_token()
            self.send_json(
                {"ok": True},
                extra_headers=[("Set-Cookie", self.session_cookie_header(token))],
            )
            return True

        if path == "/api/auth/logout" and self.command == "POST":
            self.send_json(
                {"ok": True},
                extra_headers=[("Set-Cookie", self.clear_cookie_header())],
            )
            return True

        if path == "/api/auth/status":
            needed = self.via_tunnel() and remote_password_required()
            self.send_json({
                "authenticated": (not needed) or self.session_ok(),
                "auth_required": needed,
                "via_tunnel": self.via_tunnel(),
            })
            return True

        # Everything below requires session
        if not self.require_auth():
            return True

        if path == "/api/setup/status":
            self.send_json(setup_status(include_sensitive=False))
            return True

        if path == "/api/device-id":
            # Authenticated only — never public
            self.send_json({"device_id": read_device_id()})
            return True

        if path == "/api/config":
            self.send_json(setup_status(include_sensitive=True))
            return True

        if path == "/api/access":
            ips = []
            try:
                out = subprocess.run(
                    ["hostname", "-I"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                ips = [x for x in out.stdout.split() if x]
            except Exception:
                pass
            url = public_url()
            self.send_json(
                {
                    "lan_urls": [f"http://{ip}/" for ip in ips[:4]],
                    "mdns": "http://raspberrypi.local/",
                    "remote_enabled": remote_enabled(),
                    "public_url": url,
                    "remote_starting": remote_enabled() and not url,
                }
            )
            return True

        if path == "/api/remote-access":
            if self.command == "GET":
                self.send_json(
                    {
                        "enabled": remote_enabled(),
                        "wanted": bool(read_json_file(CONFIG).get("remote_access", False)),
                        "public_url": public_url(),
                        "starting": remote_enabled() and not public_url(),
                    }
                )
                return True
            if self.command == "POST":
                try:
                    body = self.read_body_json()
                except (ValueError, json.JSONDecodeError):
                    self.send_json({"error": "Invalid JSON"}, code=400)
                    return True
                want = bool(body.get("enabled", True))
                cfg = read_json_file(CONFIG)
                cfg["remote_access"] = want
                write_json_file(CONFIG, cfg)
                run_remote_access(want)
                self.send_json({"ok": True, "enabled": want})
                return True

        if path == "/api/auth/password" and self.command == "POST":
            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True
            current = str(body.get("current_password") or "")
            new = str(body.get("new_password") or "")
            if not verify_password(current):
                self.send_json({"error": "Current password incorrect"}, code=401)
                return True
            try:
                set_password(new)
            except ValueError as exc:
                self.send_json({"error": str(exc)}, code=400)
                return True
            token = make_session_token()
            self.send_json(
                {"ok": True},
                extra_headers=[("Set-Cookie", self.session_cookie_header(token))],
            )
            return True

        if path == "/api/agent/profile":
            if self.command == "GET":
                cfg = read_json_file(CONFIG)
                self.send_json(
                    {
                        "user_name": cfg.get("user_name"),
                        "agent_name": cfg.get("agent_name", "sudo"),
                        "personality": cfg.get("personality", "friendly"),
                        "model": resolve_model(cfg.get("model")),
                        "profile_done": bool(cfg.get("profile_done")),
                        "remote_access": cfg.get("remote_access", False),
                        "country": read_wifi_country(),
                    }
                )
                return True
            if self.command == "POST":
                try:
                    body = self.read_body_json()
                except (ValueError, json.JSONDecodeError):
                    self.send_json({"error": "Invalid JSON"}, code=400)
                    return True
                allowed_personality = {"friendly", "professional", "concise", "creative"}
                personality = body.get("personality", "friendly")
                model = body.get("model", DEFAULT_MODEL)
                if personality not in allowed_personality:
                    personality = "friendly"
                model = resolve_model(model)
                cfg = read_json_file(CONFIG)
                cfg["user_name"] = (body.get("user_name") or "friend").strip()[:64]
                cfg["agent_name"] = (body.get("agent_name") or "sudo").strip()[:32]
                cfg["personality"] = personality
                cfg["model"] = model
                cfg["profile_done"] = True
                theme = body.get("theme")
                if theme in ALLOWED_THEMES:
                    cfg["theme"] = theme

                routing = body.get("routing_mode")
                if routing in ROUTING_MODES:
                    cfg["routing_mode"] = routing

                if "prefer_local" in body:
                    cfg["prefer_local"] = bool(body.get("prefer_local"))
                if "remote_access" in body:
                    cfg["remote_access"] = bool(body.get("remote_access"))
                elif "remote_access" not in cfg:
                    cfg["remote_access"] = False
                write_json_file(CONFIG, cfg)

                country = str(body.get("country") or "").strip().upper()
                if re.fullmatch(r"[A-Z]{2}", country) and country != read_wifi_country():
                    try:
                        os.makedirs(os.path.dirname(WIFI_COUNTRY_FILE), exist_ok=True)
                        tmp = WIFI_COUNTRY_FILE + ".tmp"
                        with open(tmp, "w", encoding="utf-8") as f:
                            f.write(country + "\n")
                        os.replace(tmp, WIFI_COUNTRY_FILE)
                        apply_wifi_country(country)
                    except OSError:
                        pass

                run_configure_picoclaw()
                if cfg.get("remote_access"):
                    run_remote_access(True)
                else:
                    run_remote_access(False)
                self.send_json({"ok": True, **setup_status(include_sensitive=False)})
                return True

        if path == "/api/agent/ssh":
            cfg = read_json_file(CONFIG)
            user, _ = login_user()

            if self.command == "GET":
                # The password is deliberately withheld over the public tunnel:
                # the dashboard has no login, so anyone holding that URL would
                # otherwise be handed shell access.
                pw = None
                if not self.via_tunnel():
                    pw = read_text(SSH_PASSWORD_FILE)
                self.send_json(
                    {
                        "enabled": ssh_enabled(),
                        "auth": cfg.get("ssh_auth", "password"),
                        "user": user,
                        "host": (self.headers.get("Host") or "raspberrypi.local").split(":")[0],
                        "password": pw,
                        "password_hidden_remotely": bool(self.via_tunnel()),
                    }
                )
                return True

            if self.command == "POST":
                try:
                    body = self.read_body_json()
                except (ValueError, json.JSONDecodeError):
                    self.send_json({"error": "Invalid JSON"}, code=400)
                    return True

                want = bool(body.get("enabled"))
                if not want:
                    disable_ssh()
                    cfg["ssh_enabled"] = False
                    write_json_file(CONFIG, cfg)
                    try:
                        os.remove(SSH_PASSWORD_FILE)
                    except OSError:
                        pass
                    self.send_json({"ok": True, "enabled": False})
                    return True

                key = str(body.get("public_key") or "").strip()
                try:
                    if key:
                        if not key.startswith(SSH_KEY_PREFIXES) or len(key) > 2048:
                            self.send_json(
                                {"error": "That doesn't look like a public key — it should "
                                          "start with ssh-ed25519 or ssh-rsa"},
                                code=400,
                            )
                            return True
                        user = install_ssh_key(key)
                        cfg["ssh_auth"] = "key"
                        password = None
                    else:
                        user, password = set_ssh_password()
                        cfg["ssh_auth"] = "password"

                except Exception as exc:
                    self.send_json({"error": str(exc)}, code=500)
                    return True

                cfg["ssh_enabled"] = True
                write_json_file(CONFIG, cfg)
                self.send_json(
                    {
                        "ok": True,
                        "enabled": True,
                        "auth": cfg["ssh_auth"],
                        "user": user,
                        "host": (self.headers.get("Host") or "raspberrypi.local").split(":")[0],
                        "password": password,
                    }
                )
                return True

        if path == "/api/agent/devtools":
            user, _ = login_user()
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            tool = (qs.get("tool", ["claude"])[0] or "claude").lower()
            if tool not in DEVTOOLS:
                self.send_json({"error": "Unknown tool"}, code=400)
                return True
            if self.command == "GET":
                self.send_json({**devtools_state(tool), "user": user})
                return True

            if self.command == "POST":
                if not os.path.isfile(DEVTOOLS_SCRIPT):
                    self.send_json(
                        {"error": "Installer missing — re-flash this device to get it"},
                        code=500,
                    )
                    return True
                current = devtools_state(tool)
                if current["state"] == "installing":
                    self.send_json({"ok": True, **current, "user": user})
                    return True
                # Detached: this outlives the request by several minutes.
                try:
                    status_file = f"{DEVTOOLS_STATUS}-{tool}"
                    write_json_file(
                        status_file,
                        {"state": "installing", "message": "Starting…", "updated": int(time.time())},
                    )
                    os.chmod(status_file, 0o644)
                    # apt-get writes /usr, which ProtectSystem=full blocks,
                    # so this runs as a transient unit outside our sandbox.
                    run_privileged(
                        f"sudo-devtools-{tool}", DEVTOOLS_SCRIPT, tool, wait=False, timeout=20
                    )
                except Exception as exc:
                    self.send_json({"error": str(exc)}, code=500)
                    return True
                self.send_json(
                    {"ok": True, "tool": tool, "label": DEVTOOLS[tool][0],
                     "state": "installing", "message": "Starting…",
                     "installed": False, "user": user}
                )
                return True

        if path == "/api/agent/channels/whatsapp":
            if self.command == "GET":
                self.send_json(whatsapp_state())
                return True

            if self.command == "POST":
                if not os.path.isfile(WHATSAPP_INSTALL_SCRIPT):
                    self.send_json(
                        {"error": "Installer missing — re-flash this device to get it"},
                        code=500,
                    )
                    return True
                current = whatsapp_state()
                if current["state"] in ("installing", "qr", "connected", "reconnecting"):
                    # Already underway or already linked -- nothing to kick off.
                    # A second click here most often means "show me the QR
                    # again", and the status the poll already reads covers
                    # that without running the installer a second time.
                    self.send_json({"ok": True, **current})
                    return True
                try:
                    write_json_file(WHATSAPP_STATUS,
                                     {"state": "installing", "message": "Starting…"})
                    # Same reasoning as devtools: apt/npm need real root and a
                    # writable /usr, which this service's own ProtectSystem=full
                    # blocks -- systemd-run steps outside that sandbox.
                    run_privileged("sudo-whatsapp-install", WHATSAPP_INSTALL_SCRIPT,
                                    wait=False, timeout=20)
                except Exception as exc:
                    self.send_json({"error": str(exc)}, code=500)
                    return True
                self.send_json({"ok": True, "state": "installing", "message": "Starting…"})
                return True

        if path == "/api/agent/channels/whatsapp/unlink" and self.command == "POST":
            if not os.path.isfile(WHATSAPP_UNLINK_SCRIPT):
                self.send_json({"error": "Unlink script missing — re-flash this device to get it"},
                               code=500)
                return True
            try:
                # Fast and local -- stop, wipe the session, restart -- so
                # unlike the install path this can simply be waited on.
                run_privileged("sudo-whatsapp-unlink", WHATSAPP_UNLINK_SCRIPT, timeout=30)
            except Exception as exc:
                self.send_json({"error": str(exc)}, code=500)
                return True
            self.send_json({"ok": True, **whatsapp_state()})
            return True

        if path == "/api/agent/files" and self.command == "GET":
            self.send_json({"workspace": WORKSPACE, "files": read_agent_files()})
            return True

        if path == "/api/agent/abilities" and self.command == "POST":
            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True
            cfg = read_json_file(CONFIG)
            for flag in ("exec_enabled", "subagent_enabled", "skills_enabled", "full_access_enabled"):
                if flag in body:
                    cfg[flag] = bool(body.get(flag))
            write_json_file(CONFIG, cfg)
            if "heartbeat_enabled" in body:
                # Not a config value: the timer itself is the state. Enabling
                # or disabling the unit is the whole change, so there is
                # nothing that can drift out of sync.
                run_heartbeat(bool(body.get("heartbeat_enabled")))
            run_configure_picoclaw()
            self.send_json({"ok": True, **setup_status(include_sensitive=False)})
            return True

        if path == "/api/auth/remote-password":
            cfg = read_json_file(CONFIG)

            if self.command == "GET":
                # Shown on the home network only. Handing the password to
                # whoever already has the public URL would defeat the point
                # of asking for it -- same rule the SSH password follows.
                current = None
                if not self.via_tunnel():
                    current = read_text(PASS_FILE)
                self.send_json({
                    "enabled": bool(cfg.get("remote_password_enabled", True)),
                    "has_password": bool(read_text(PASS_HASH_FILE) or read_text(PASS_FILE)),
                    "password": current,
                    "hidden_remotely": bool(self.via_tunnel()),
                })
                return True

            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True

            if "enabled" in body:
                wanted = bool(body.get("enabled"))
                if wanted and not (read_text(PASS_HASH_FILE) or read_text(PASS_FILE)):
                    self.send_json({
                        "error": "Set a password first, then switch this on."
                    }, code=400)
                    return True
                cfg["remote_password_enabled"] = wanted
                write_json_file(CONFIG, cfg)

            new = str(body.get("new_password") or "").strip()
            if new:
                # Deliberately not asking for the current one. Anyone able to
                # reach this is already on the home network or already past
                # the password, and an owner who has forgotten it would
                # otherwise have no way back in short of reflashing.
                try:
                    set_password(new)
                except ValueError as exc:
                    self.send_json({"error": str(exc)}, code=400)
                    return True
                # Keep a readable copy so the dashboard can show it at home.
                try:
                    os.makedirs("/etc/sudo", exist_ok=True)
                    tmp = PASS_FILE + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        f.write(new + "\n")
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, PASS_FILE)
                except OSError:
                    pass

            self.send_json({"ok": True, **setup_status(include_sensitive=False)})
            return True

        if path == "/api/device/hostname" and self.command == "POST":
            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True

            wanted = str(body.get("hostname") or "").strip().lower()
            if not HOSTNAME_RE.match(wanted):
                self.send_json({
                    "error": "Use letters, numbers and hyphens only — no spaces "
                             "or dots, and it cannot start or end with a hyphen."
                }, code=400)
                return True

            current = socket.gethostname().split(".")[0]
            if wanted == current:
                self.send_json({"ok": True, "hostname": wanted,
                                "url": f"http://{wanted}.local", "changed": False})
                return True

            result = run_privileged("sudo-set-hostname", HOSTNAME_SCRIPT, wanted, timeout=45)
            if result.returncode != 0:
                self.send_json({
                    "error": "Could not rename the device — see "
                             "/var/log/sudo-set-hostname.log"
                }, code=500)
                return True

            self.send_json({"ok": True, "hostname": wanted,
                            "url": f"http://{wanted}.local",
                            "previous_url": f"http://{current}.local",
                            "changed": True})
            return True

        if path == "/api/agent/provider-keys" and self.command == "POST":
            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True
            cfg = read_json_file(CONFIG)
            stored = dict(cfg.get("provider_keys") or {})

            # Only keys present in the body are touched. The UI sends a field
            # only when the user typed in it, so an untouched box cannot wipe a
            # stored key; an empty string is an explicit delete.
            # A single pasted key, provider worked out from its prefix.
            if "auto_key" in body:
                pasted = str(body.get("auto_key") or "").strip()
                provider = detect_provider(pasted)
                if pasted and not provider:
                    self.send_json({
                        "error": "That does not look like a key we recognise. "
                                 "Pick the provider below and paste it there."
                    }, code=400)
                    return True
                if provider:
                    stored[provider] = pasted
                    cfg["provider_keys"] = stored
                    write_json_file(CONFIG, cfg)
                    run_configure_picoclaw()
                    self.send_json({
                        "ok": True, "detected": provider,
                        **setup_status(include_sensitive=False),
                    })
                    return True

            incoming = body.get("keys")
            if isinstance(incoming, dict):
                for name, value in incoming.items():
                    if name not in PROVIDERS:
                        continue
                    value = str(value or "").strip()
                    if value:
                        stored[name] = value
                    else:
                        stored.pop(name, None)
                cfg["provider_keys"] = stored

            if "composio_enabled" in body:
                cfg["composio_enabled"] = bool(body.get("composio_enabled"))

            if "composio_api_key" in body:
                composio = str(body.get("composio_api_key") or "").strip()
                if composio:
                    cfg["composio_api_key"] = composio
                else:
                    cfg.pop("composio_api_key", None)

            write_json_file(CONFIG, cfg)  # chmod 600
            run_configure_picoclaw()
            self.send_json({"ok": True, **setup_status(include_sensitive=False)})
            return True

        if path == "/api/agent/keys" and self.command == "POST":
            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True
            cfg = read_json_file(CONFIG)

            key = str(body.get("user_openrouter_key") or "").strip()
            if key:
                # Loose shape check only — OpenRouter is the real authority.
                if not key.startswith("sk-or-") or len(key) > 256:
                    self.send_json(
                        {"error": "That doesn't look like an OpenRouter key (starts with sk-or-)"},
                        code=400,
                    )
                    return True
                cfg["user_openrouter_key"] = key

            source = "own" if body.get("openrouter_key_source") == "own" else "sudo"
            if source == "own" and not cfg.get("user_openrouter_key"):
                self.send_json({"error": "No key saved yet — paste one first"}, code=400)
                return True
            cfg["openrouter_key_source"] = source

            write_json_file(CONFIG, cfg)  # chmod 600
            run_configure_picoclaw()
            self.send_json({"ok": True, **setup_status(include_sensitive=False)})
            return True

        if path == "/api/agent/chat" and self.command == "POST":
            ip = self.client_ip()
            if not rate_allow(f"chat:{ip}", CHAT_MAX, CHAT_WINDOW):
                self.send_json({"error": "Rate limit — slow down"}, code=429)
                return True
            try:
                body = self.read_body_json()
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid JSON"}, code=400)
                return True
            message = (body.get("message") or "").strip()
            if not message:
                self.send_json({"error": "message required"}, code=400)
                return True
            if len(message) > MAX_CHAT_LEN:
                self.send_json({"error": f"message too long (max {MAX_CHAT_LEN})"}, code=400)
                return True
            # No cloud key means picoclaw has nothing to talk to, so go
            # straight to the on-device model rather than waiting out a
            # timeout that was never going to produce an answer.
            cfg = read_json_file(CONFIG)
            # Stay on-device when asked to, or when there is no key to use.
            # A preference for local is not a promise the local model exists,
            # so a missing one still falls through to the cloud rather than
            # leaving the agent mute.
            history = body.get("history")
            if not isinstance(history, list):
                history = []
            # The caller's conversation identity. picoclaw keeps the actual
            # transcript under this key; the local model still gets the
            # explicit history above. Defaults to the dashboard session so a
            # plain chat POST from the web box is still one conversation.
            session_key = (body.get("session") or "").strip() or DASHBOARD_SESSION
            if cfg.get("prefer_local") or not effective_openrouter_key(cfg):
                reply = local_model_chat(message, history, cfg)
                if reply:
                    self.send_json({"reply": reply, "source": "local"})
                    return True
                if effective_openrouter_key(cfg):
                    reply = picoclaw_chat(message, session_key)
                    self.send_json({"reply": reply, "source": "cloud",
                                    "note": "on-device model unavailable"})
                    return True
                self.send_json({
                    "reply": "I can't reply yet — add an API key in "
                             "Settings and I'll wake up properly.",
                    "source": "none",
                })
                return True
            try:
                reply = picoclaw_chat(message, session_key)
                self.send_json({"reply": reply, "source": "cloud"})
            except subprocess.TimeoutExpired:
                self.send_json({"error": "Agent timed out"}, code=504)
            except Exception as exc:
                self.send_json({"error": str(exc)}, code=500)
            return True

        # Billing proxies — keep device_secret on device
        if path == "/api/billing/balance" and self.command == "GET":
            code, data = cloud_request("GET", "/v1/wallet/balance")
            if code == 200 and isinstance(data, dict):
                cfg = read_json_file(CONFIG)
                if "balance_usd" in data:
                    cfg["balance_usd"] = data.get("balance_usd")
                if data.get("subscription_status"):
                    cfg["subscription_status"] = data.get("subscription_status")
                write_json_file(CONFIG, cfg)
            self.send_json(data, code=code)
            return True

        if path == "/api/billing/confirm" and self.command == "POST":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            code, data = cloud_request("POST", "/v1/payments/confirm", body)
            if code == 200 and isinstance(data, dict):
                cfg = read_json_file(CONFIG)
                if "balance_usd" in data:
                    cfg["balance_usd"] = data.get("balance_usd")
                if data.get("subscription_status"):
                    cfg["subscription_status"] = data.get("subscription_status")
                write_json_file(CONFIG, cfg)
            self.send_json(data, code=code)
            return True

        if path == "/api/billing/checkout" and self.command == "POST":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            # Prefer the URL the user is actually on (LAN IP, .local, or tunnel)
            if not body.get("return_url"):
                host = self.headers.get("Host") or "raspberrypi.local"
                proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
                body["return_url"] = f"{proto}://{host}/billing.html?paid=1"
            if not body.get("cancel_url"):
                host = self.headers.get("Host") or "raspberrypi.local"
                proto = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
                body["cancel_url"] = f"{proto}://{host}/billing.html"
            code, data = cloud_request("POST", "/v1/payments/checkout", body)
            self.send_json(data, code=code)
            return True

        self.send_json({"error": "Not found"}, code=404)
        return True

    def serve_static(self, path: str):
        if path in ("/", ""):
            status = setup_status()
            if not status["profile_done"]:
                self.redirect("/onboarding.html")
                return
            path = "/index.html"

        # One responsive app shell owns each dashboard feature. Keeping these
        # URLs working also preserves old bookmarks and payment return URLs.
        if path in {
            "/dashboard.html",
            "/chat.html",
            "/billing.html",
            "/onboarding.html",
            "/login.html",
        }:
            path = "/index.html"

        rel = path.lstrip("/")
        if not is_safe_relpath(rel):
            self.send_error(400)
            return

        file_path = os.path.realpath(os.path.join(ROOT, rel))
        root_real = os.path.realpath(ROOT)
        if not file_path.startswith(root_real + os.sep) and file_path != root_real:
            self.send_error(403)
            return
        if not os.path.isfile(file_path):
            self.send_error(404)
            return

        with open(file_path, "rb") as f:
            data = f.read()

        # Never inject secrets into HTML beyond api base for legacy; billing uses proxy now
        if file_path.endswith("billing.html"):
            # Keep placeholder empty — billing uses same-origin /api/billing/*
            inject = b'<script>window.SUDO_USE_PROXY=true;</script>'
            data = data.replace(b"</head>", inject + b"</head>", 1)

        content_type = "text/html; charset=utf-8"
        if file_path.endswith(".js"):
            content_type = "application/javascript"
        elif file_path.endswith(".css"):
            content_type = "text/css"
        elif file_path.endswith(".json"):
            content_type = "application/json"
        elif file_path.endswith(".svg"):
            content_type = "image/svg+xml"

        self.send_bytes(data, content_type=content_type)

    def do_OPTIONS(self):
        self.send_response(204)
        self.security_headers()
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            self.handle_api(path)
            return
        self.serve_static(path)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/"):
            self.handle_api(path)
            return
        self.send_error(405)


if __name__ == "__main__":
    # Ensure secrets exist (offline-safe)
    if os.path.isfile("/usr/local/bin/sudo-device-secrets.sh"):
        subprocess.run(["/usr/local/bin/sudo-device-secrets.sh"], check=False)

    os.chdir(ROOT)

    class ReuseHTTPServer(http.server.ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    try:
        server = ReuseHTTPServer((BIND, PORT), DashboardHandler)
    except AttributeError:
        import socketserver

        class Fallback(socketserver.ThreadingMixIn, http.server.HTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        server = Fallback((BIND, PORT), DashboardHandler)

    print(f"Sudo Dashboard listening on {BIND}:{PORT} remote={remote_enabled()}")
    server.serve_forever()
