#!/usr/bin/env python3
"""sudo-spend-guard.py — a local circuit breaker for the agent's API budget.

Why this exists. The agent talks to a paid model every time anyone sends it a
message. Left alone, a runaway loop, a stuck tool, or a curious kid can spend
real money on the owner's own API key before anyone notices. This script is the
one place that counts those calls and, past a ceiling, stops them.

It is deliberately LOCAL and KEY-FREE. It never sees the owner's key, never
talks to a network, and keeps no cloud state — it just counts how many agent
turns happened and refuses to start more when the owner's ceiling is reached.
That is the whole point: the guard on a fully-owned box has to be on the box.

Used by these callers, which all shell out to the same commands so there is a
single source of truth:
  * sudo-dashboard/server.py   (dashboard chat)
  * whatsapp-bridge/bridge.js  (the old WhatsApp connection)
  * sudo-spend-watch.timer     (dollar limits, `watch` below)

Contract:
  spend-guard.py check   -> exit 0 allow, exit 3 blocked. JSON on stdout either way.
  spend-guard.py record  -> count one agent turn. exit 0.
  spend-guard.py status  -> current counters + ceilings as JSON.
  spend-guard.py reset   -> clear the breaker and the window (keeps the day count).

Dollar limits (OpenRouter keys). The message count above is a proxy; what
the owner actually cares about is money, and it only counts turns that pass
through the dashboard. OpenRouter reports what a key has spent, across every
channel, reminder and check-in, so `watch` (sudo-spend-watch.timer, every 5
minutes, as root) reads that and pauses the whole agent past the owner's
daily or monthly limit, then lets it back on at their local midnight or the
first of the month:
  spend-guard.py watch   -> read spend, pause or resume. JSON on stdout.
The pause is /opt/sudo/spend-paused.json. While it exists the gateway does
not start (an ExecCondition drop-in) and `check` refuses dashboard turns.
Other providers' keys have no spend endpoint we can read without an admin
key, so for those the dashboard points at the provider's own limit.

Config lives in the owner's /opt/sudo/config.json:
  spend_enabled        (bool,   default true)
  spend_max_per_day    (int,    default 300)   # agent turns per local day
  spend_max_per_minute (int,    default 12)    # burst guard
  spend_cooldown_min   (int,    default 30)    # pause length after a burst
  spend_usd_day        (number, unset = none) # dollars per local day
  spend_usd_month      (number, unset = none) # dollars per local month

Everything here is best-effort and fail-open: if state cannot be read or
written we ALLOW the call rather than lock the owner out of their own agent.
A guard that blocks legitimate use because of its own bug is worse than none.
"""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

CONFIG = os.environ.get("SUDO_CONFIG", "/opt/sudo/config.json")
STATE = os.environ.get("SUDO_SPEND_STATE", "/opt/sudo/spend.json")
LOCK = STATE + ".lock"
USD_STATE = os.environ.get("SUDO_SPEND_USD_STATE", "/opt/sudo/spend-usd.json")
PAUSE = os.environ.get("SUDO_SPEND_PAUSE", "/opt/sudo/spend-paused.json")
GATEWAY = "sudo-openclaw-gateway.service"
NOTIFY = "/usr/local/bin/sudo-notify-owner.sh"

DEFAULTS = {
    "spend_enabled": True,
    "spend_max_per_day": 300,
    "spend_max_per_minute": 12,
    "spend_cooldown_min": 30,
}


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def _caps():
    cfg = _read_json(CONFIG, {}) or {}
    caps = {}
    for key, default in DEFAULTS.items():
        value = cfg.get(key, default)
        caps[key] = value if isinstance(value, type(default)) else default
    # Guard against nonsense values that would make the box unusable.
    caps["spend_max_per_day"] = max(1, int(caps["spend_max_per_day"]))
    caps["spend_max_per_minute"] = max(1, int(caps["spend_max_per_minute"]))
    caps["spend_cooldown_min"] = max(1, int(caps["spend_cooldown_min"]))
    return caps


def _today():
    return time.strftime("%Y-%m-%d", time.localtime())


def _load_state():
    state = _read_json(STATE, {}) or {}
    if state.get("day") != _today():
        # A new local day: reset the ceiling, keep nothing from yesterday.
        state = {"day": _today(), "day_calls": 0, "window": [], "tripped_until": 0, "reason": ""}
    state.setdefault("day_calls", 0)
    state.setdefault("window", [])
    state.setdefault("tripped_until", 0)
    state.setdefault("reason", "")
    return state


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE)
    except OSError:
        pass


def _trim_window(window, now):
    return [t for t in window if isinstance(t, (int, float)) and now - t < 60]


def _blocked_response(state, caps, now):
    """Shape the reply a caller sends the user when we refuse a turn."""
    if state["tripped_until"] > now and state.get("reason") != "daily limit reached":
        retry = int(state["tripped_until"] - now)
        reason = state.get("reason") or "heavy use"
        return {
            "allowed": False,
            "reason": "cooldown",
            "detail": reason,
            "retry_after": retry,
            "day_calls": state["day_calls"],
            "day_cap": caps["spend_max_per_day"],
        }
    return {
        "allowed": False,
        "reason": "daily_cap",
        "detail": "daily limit reached",
        "retry_after": max(0, int(state["tripped_until"] - now)),
        "day_calls": state["day_calls"],
        "day_cap": caps["spend_max_per_day"],
    }


def cmd_check():
    caps = _caps()
    if not caps["spend_enabled"]:
        print(json.dumps({"allowed": True, "reason": "disabled"}))
        return 0
    paused = _read_json(PAUSE, None)
    if isinstance(paused, dict):
        print(json.dumps({"allowed": False, "reason": "budget", "detail": paused.get("period") or "day",
                          "limit": paused.get("limit"), "spent": paused.get("spent")}))
        return 3
    now = time.time()
    state = _load_state()
    if state["tripped_until"] > now:
        print(json.dumps(_blocked_response(state, caps, now)))
        return 3
    if state["day_calls"] >= caps["spend_max_per_day"]:
        # Trip until the next local midnight, so the block survives restarts
        # and clears itself at the start of the owner's next day.
        midnight = time.mktime(time.strptime(_today() + " 23:59:59", "%Y-%m-%d %H:%M:%S")) + 1
        state["tripped_until"] = midnight
        state["reason"] = "daily limit reached"
        _save_state(state)
        print(json.dumps(_blocked_response(state, caps, now)))
        return 3
    print(json.dumps({"allowed": True, "day_calls": state["day_calls"], "day_cap": caps["spend_max_per_day"]}))
    return 0


def cmd_record():
    caps = _caps()
    if not caps["spend_enabled"]:
        return 0
    now = time.time()
    with open(LOCK, "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError:
            pass
        state = _load_state()
        state["day_calls"] += 1
        state["window"] = _trim_window(state["window"], now)
        state["window"].append(now)
        # Burst guard: too many turns inside a minute means something is
        # looping. Pause for a cooldown instead of burning the whole day's cap.
        if len(state["window"]) > caps["spend_max_per_minute"] and state["tripped_until"] <= now:
            state["tripped_until"] = now + caps["spend_cooldown_min"] * 60
            state["reason"] = "a burst of messages"
        if state["day_calls"] >= caps["spend_max_per_day"] and state["tripped_until"] <= now:
            midnight = time.mktime(time.strptime(_today() + " 23:59:59", "%Y-%m-%d %H:%M:%S")) + 1
            state["tripped_until"] = midnight
            state["reason"] = "daily limit reached"
        _save_state(state)
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError:
            pass
    return 0


def cmd_status():
    caps = _caps()
    now = time.time()
    state = _load_state()
    usd = _read_json(USD_STATE, {}) or {}
    cfg = _read_json(CONFIG, {}) or {}
    paused = _read_json(PAUSE, None)
    fresh = usd.get("day") == _today()
    print(json.dumps({
        "usd": {
            "source": usd.get("source") or "",
            "spent_day": round(usd.get("spent_day", 0), 4) if fresh else 0,
            "spent_month": round(usd.get("spent_month", 0), 4) if usd.get("month") == time.strftime("%Y-%m") else 0,
            "limit_day": _usd_limit(cfg, "spend_usd_day"),
            "limit_month": _usd_limit(cfg, "spend_usd_month"),
            "checked_at": usd.get("checked_at") or 0,
            "error": usd.get("error") or "",
            "paused": paused if isinstance(paused, dict) else None,
        },
        "enabled": caps["spend_enabled"],
        "day_calls": state["day_calls"],
        "day_cap": caps["spend_max_per_day"],
        "max_per_minute": caps["spend_max_per_minute"],
        "tripped": state["tripped_until"] > now,
        "retry_after": max(0, int(state["tripped_until"] - now)),
        "reason": state.get("reason") or "",
    }))
    return 0


def cmd_reset():
    state = _load_state()
    state["tripped_until"] = 0
    state["reason"] = ""
    state["window"] = []
    _save_state(state)
    print(json.dumps({"ok": True}))
    return 0


def _usd_limit(cfg, key):
    try:
        value = float(cfg.get(key))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _openrouter_key(cfg):
    """The key the agent runs on -- same precedence as configure-openclaw.sh."""
    pk = cfg.get("provider_keys") or {}
    own = cfg.get("user_openrouter_key") if cfg.get("openrouter_key_source", "sudo") == "own" else ""
    return (pk.get("openrouter") or own or cfg.get("openrouter_key") or "").strip()


def _spend_now(cfg):
    """Dollars spent this local day and month, from the key's running total.

    OpenRouter's own daily and monthly figures roll over at UTC midnight,
    which is the middle of the evening for much of the world. So the running
    total is sampled instead, and the sample taken just before the owner's
    midnight (or the first of their month) becomes the base the new period
    counts from.
    """
    key = _openrouter_key(cfg)
    usd = _read_json(USD_STATE, {}) or {}
    if not key:
        return None, usd
    req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = (json.load(resp) or {}).get("data") or {}
        total = float(data.get("usage") or 0)
    except Exception as exc:  # noqa: BLE001 - offline or a bad key: keep the last figures
        usd["error"] = str(exc)[:200]
        return None, usd
    now = time.time()
    today, month = _today(), time.strftime("%Y-%m")
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:12]
    recent = usd.get("last_total") is not None and now - float(usd.get("checked_at") or 0) < 3600
    if usd.get("key") != fingerprint:
        usd = {"key": fingerprint, "day": today, "day_base": total, "month": month, "month_base": total}
    if usd.get("day") != today:
        usd["day"], usd["day_base"] = today, (usd["last_total"] if recent else total)
    if usd.get("month") != month:
        usd["month"], usd["month_base"] = month, (usd["last_total"] if recent else total)
    for base in ("day_base", "month_base"):
        if total < float(usd.get(base) or 0):
            usd[base] = total   # credits refunded or the counter reset
    usd.update({"last_total": total, "checked_at": now, "source": "openrouter", "error": "",
                "spent_day": total - float(usd["day_base"]), "spent_month": total - float(usd["month_base"])})
    return usd, usd


PAUSE_TEXT = {
    "en": ("I've reached the {period} spending limit you set (${limit:.2f}), so I'm pausing until {when}. "
           "You can raise it in Settings → Spending guard."),
    "es": ("Llegué al límite de gasto {period} que pusiste (${limit:.2f}), así que hago una pausa hasta {when}. "
           "Puedes subirlo en Ajustes → Límite de gasto."),
    "zh": "我已达到你设定的{period}花费上限（${limit:.2f}），暂停到{when}。可以在 设置 → 花费保护 里调高。",
    "ar": "وصلت إلى حد الإنفاق {period} الذي وضعته (${limit:.2f})، لذا سأتوقف حتى {when}. يمكنك رفعه من الإعدادات ← حارس الإنفاق.",
}
PERIOD_WORDS = {
    "en": {"day": "daily", "month": "monthly", "when_day": "midnight", "when_month": "the 1st"},
    "es": {"day": "diario", "month": "mensual", "when_day": "la medianoche", "when_month": "el día 1"},
    "zh": {"day": "每日", "month": "每月", "when_day": "午夜", "when_month": "下月 1 号"},
    "ar": {"day": "اليومي", "month": "الشهري", "when_day": "منتصف الليل", "when_month": "أول الشهر"},
}
RESUME_TEXT = {
    "en": "I'm back on: I'm within your spending limit again.",
    "es": "Ya estoy de vuelta: vuelvo a estar dentro de tu límite de gasto.",
    "zh": "我回来了：花费又在你的上限之内了。",
    "ar": "عدت للعمل: أصبحت ضمن حد الإنفاق مرة أخرى.",
}


def _notify(cfg, text):
    if os.path.isfile(NOTIFY):
        try:
            subprocess.run(["/bin/bash", NOTIFY, text], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def cmd_watch():
    cfg = _read_json(CONFIG, {}) or {}
    caps = _caps()
    paused = _read_json(PAUSE, None)
    limits = {"day": _usd_limit(cfg, "spend_usd_day"), "month": _usd_limit(cfg, "spend_usd_month")}
    usd, state = _spend_now(cfg) if caps["spend_enabled"] and any(limits.values()) else (None, _read_json(USD_STATE, {}) or {})
    _save_json(USD_STATE, state)
    over = None
    if usd:
        for period in ("month", "day"):
            if limits[period] and usd[f"spent_{period}"] >= limits[period]:
                over = period
                break
    lang = (cfg.get("agent_language") if cfg.get("agent_language") not in (None, "", "auto") else None) \
        or cfg.get("ui_language") or "en"
    lang = lang if lang in PAUSE_TEXT else "en"
    words = PERIOD_WORDS[lang]
    action = "none"
    if over and not isinstance(paused, dict):
        _save_json(PAUSE, {"period": over, "limit": limits[over], "spent": round(usd[f"spent_{over}"], 2),
                           "at": int(time.time())})
        # Tell them while the agent can still send, then stop it.
        _notify(cfg, PAUSE_TEXT[lang].format(period=words[over], limit=limits[over],
                                             when=words["when_" + over]))
        subprocess.run(["systemctl", "stop", GATEWAY], capture_output=True, timeout=60)
        action = "paused"
    elif isinstance(paused, dict) and not over and (usd is not None or not any(limits.values())
                                                    or not caps["spend_enabled"] or not _openrouter_key(cfg)):
        # A new day or month, a raised limit, or the cap switched off.
        try:
            os.remove(PAUSE)
        except OSError:
            pass
        subprocess.run(["systemctl", "start", GATEWAY], capture_output=True, timeout=60)
        time.sleep(45)   # the gateway has to be up before it can send
        _notify(cfg, RESUME_TEXT[lang])
        action = "resumed"
    out = {"action": action, "over": over, "limits": limits,
           "spent_day": usd and round(usd["spent_day"], 4), "spent_month": usd and round(usd["spent_month"], 4)}
    print(json.dumps(out))
    return 0


def _save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    handlers = {"check": cmd_check, "record": cmd_record, "status": cmd_status, "reset": cmd_reset,
                "watch": cmd_watch}
    return handlers.get(cmd, cmd_status)()


if __name__ == "__main__":
    sys.exit(main())