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

Used by three callers, which all shell out to the same commands so there is a
single source of truth:
  * sudo-dashboard/server.py   (dashboard chat)
  * whatsapp-bridge/bridge.js  (WhatsApp messages)
  * sudo-pi/sudo-heartbeat.sh  (proactive nudges)

Contract:
  spend-guard.py check   -> exit 0 allow, exit 3 blocked. JSON on stdout either way.
  spend-guard.py record  -> count one agent turn. exit 0.
  spend-guard.py status  -> current counters + ceilings as JSON.
  spend-guard.py reset   -> clear the breaker and the window (keeps the day count).

Config lives in the owner's /opt/sudo/config.json:
  spend_enabled        (bool,   default true)
  spend_max_per_day    (int,    default 300)   # agent turns per local day
  spend_max_per_minute (int,    default 12)    # burst guard
  spend_cooldown_min   (int,    default 30)    # pause length after a burst

Everything here is best-effort and fail-open: if state cannot be read or
written we ALLOW the call rather than lock the owner out of their own agent.
A guard that blocks legitimate use because of its own bug is worse than none.
"""

import fcntl
import json
import os
import sys
import time

CONFIG = os.environ.get("SUDO_CONFIG", "/opt/sudo/config.json")
STATE = os.environ.get("SUDO_SPEND_STATE", "/opt/sudo/spend.json")
LOCK = STATE + ".lock"

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
    print(json.dumps({
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


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    handlers = {"check": cmd_check, "record": cmd_record, "status": cmd_status, "reset": cmd_reset}
    return handlers.get(cmd, cmd_status)()


if __name__ == "__main__":
    sys.exit(main())