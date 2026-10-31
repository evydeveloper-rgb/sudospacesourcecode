#!/usr/bin/env python3
"""problem-report.py — the daily problem report beta owners can opt in to.

Schema 2 adds how the agent is used, still never what is said: messages per
chat app, tool use and failures, why website tasks fail (with the site's name,
only for failures), check-ins, tokens and cost, reply times, settings, and
signs of a bad experience counted on the device (the person repeating
themselves, "that's wrong", the agent saying it couldn't). The conversations
are read here; only the counts leave.

What went wrong on this sudo in the last day, sent to the Sudo team so a
broken reminder, a crashing agent or a failed update gets fixed without the
owner having to notice and describe it. Opt-in (Settings -> Problem reports),
off until the owner says yes.

What a report holds: versions, the board and its memory, how long it has been
up, and counts of things that went wrong, with error *kinds* only. Never the
owner's messages, names, numbers, contacts, keys, files or reminder text:
reminder names are left out (they are the owner's words) and every error
string goes through scrub() first. The owner can read the exact report in
Settings before and after it is sent.

  problem-report.py collect   print the report this device would send now
  problem-report.py send      collect and send it (only when opted in)
  problem-report.py forget    ask the service to delete this device's reports
  problem-report.py feedback  "Report this": send one exchange the owner chose,
                              read as JSON from stdin ({source, note, exchange}).
                              Sent whatever the daily-report setting: the owner
                              asked for this one.

Run as root (sudo-problem-report.timer, daily): the gateway journal and the
OpenClaw state database need it.
"""

import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

CONFIG = "/opt/sudo/config.json"
KEY_FILE = "/etc/sudo/reports.json"
LAST_FILE = "/var/lib/sudo-report-last.json"
SERVICE = "https://gluklijoptuvampxmtbl.supabase.co/functions/v1/reports"
STATE_DB = "/opt/sudo/openclaw/state/openclaw.sqlite"
OC_PKG = "/opt/openclaw/node_modules/openclaw/package.json"
GATEWAY = "sudo-openclaw-gateway.service"
DAY = 24 * 3600


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def scrub(text, limit=160):
    """An error string with anything personal taken out: quoted text (job
    names, messages), email addresses, links, and long numbers (phone
    numbers, ids)."""
    text = str(text or "")
    text = re.sub(r"[\"“”'‘’«»][^\"“”'‘’«»]{0,400}[\"“”'‘’«»]", "<…>", text)
    # OpenClaw names a failed reminder without quotes: "Cron: <its name> failed".
    # The name is the owner's own words (people's names, errands).
    text = re.sub(r"(Cron(?: job)?:?)\s+.*?\s+failed", r"\1 <reminder> failed", text)
    text = re.sub(r"\S+@\S+", "<email>", text)
    text = re.sub(r"https?://\S+", "<link>", text)
    text = re.sub(r"\+?\d[\d\s().-]{5,}\d", "<number>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def device():
    model = ""
    try:
        model = open("/proc/device-tree/model", encoding="utf-8", errors="ignore").read().strip("\x00 \n")
        model = re.sub(r"\s+Rev\s+\S+$", "", model)
    except OSError:
        pass
    mem = {}
    try:
        for line in open("/proc/meminfo", encoding="utf-8"):
            k, v = line.split(":", 1)
            mem[k] = int(v.split()[0]) // 1024
    except (OSError, ValueError):
        pass
    try:
        uptime_h = round(float(open("/proc/uptime").read().split()[0]) / 3600, 1)
    except (OSError, ValueError):
        uptime_h = None
    disk = shutil.disk_usage("/")
    temp = run(["vcgencmd", "measure_temp"]).strip().replace("temp=", "")
    throttled = run(["vcgencmd", "get_throttled"]).strip().replace("throttled=", "")
    return {
        "board": model,
        "ram_mb": mem.get("MemTotal"),
        "ram_free_mb": mem.get("MemAvailable"),
        "disk_used_pct": round(disk.used * 100 / disk.total) if disk.total else None,
        "uptime_h": uptime_h,
        "temp": temp or None,
        # Under-voltage and throttling flags: the usual cause of "it just
        # stopped" on a Pi with the wrong power supply.
        "throttled": throttled or None,
    }


def agent(cfg):
    pkg = read_json(OC_PKG, {}) or {}
    show = run(["systemctl", "show", GATEWAY, "-p", "ActiveState", "-p", "NRestarts", "-p", "ExecMainStatus"])
    props = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    pk = cfg.get("provider_keys") or {}
    tg = cfg.get("telegram") or {}
    return {
        "openclaw": pkg.get("version"),
        "gateway": props.get("ActiveState"),
        "gateway_restarts": int(props.get("NRestarts") or 0),
        "gateway_exit": props.get("ExecMainStatus"),
        "brain": next((p for p in ("openrouter", "anthropic", "openai", "gemini", "deepseek") if pk.get(p)), None)
                 or ("openrouter" if cfg.get("openrouter_key") or cfg.get("user_openrouter_key") else None),
        "model": cfg.get("model"),
        "prefer_local": bool(cfg.get("prefer_local")),
        "channels": {
            "whatsapp": bool(cfg.get("whatsapp_numbers")),
            "whatsapp_old_bridge": os.path.isfile("/opt/sudo/whatsapp-auth/creds.json"),
            "telegram": bool(tg.get("owner_id")),
        },
        "connectors": bool((cfg.get("composio_api_key") or "").strip()),
        "checkins": bool(cfg.get("checkins_enabled", True)),
        "spend_paused": os.path.isfile("/opt/sudo/spend-paused.json"),
    }


def reminders(since_ms, window_h):
    """Scheduled-job runs in the window: outcomes, error kinds and OpenClaw's
    own diagnosis of each failure (scrubbed). Jobs are told apart by a hash
    of their id, never by their name."""
    out = {"window_h": window_h, "runs": 0, "ok": 0, "error": 0, "skipped": 0, "not_delivered": 0,
           "errors": []}
    seen = {}
    if not os.path.isfile(STATE_DB):
        return out
    try:
        db = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True, timeout=5)
        rows = db.execute(
            "SELECT job_id, status, error, delivery_status, delivery_error, delivered, "
            "diagnostics_summary, duration_ms, ts FROM cron_run_logs WHERE ts >= ?", (since_ms,)).fetchall()
        db.close()
    except sqlite3.Error as exc:
        out["read_error"] = scrub(exc)
        return out
    for job_id, status, error, dstatus, derror, delivered, diag, duration_ms, ts in rows:
        out["runs"] += 1
        status = (status or "").lower()
        if status in ("ok", "error", "skipped"):
            out[status] += 1
        if dstatus and dstatus not in ("delivered", "ok", "not-requested", "skipped") and not delivered:
            out["not_delivered"] += 1
        for kind, text in (("run", error), ("delivery", derror)):
            # A switched-off job is logged as skipped with error "disabled":
            # not a failure, just a job the agent turned off.
            if text and not (status == "skipped" and str(text).strip().lower() == "disabled"):
                key = f"{kind}: {scrub(text, 100)}"
                entry = seen.setdefault(key, {"count": 0, "jobs": set(), "why": {}, "secs": [], "last": 0})
                entry["count"] += 1
                entry["jobs"].add(hashlib.sha256(str(job_id).encode()).hexdigest()[:8])
                if diag:
                    why = scrub(diag, 120)
                    entry["why"][why] = entry["why"].get(why, 0) + 1
                if duration_ms:
                    entry["secs"].append(duration_ms / 1000)
                entry["last"] = max(entry["last"], int((ts or 0) / 1000))
    out["errors"] = [{"error": k, "count": v["count"], "jobs": len(v["jobs"]),
                      # OpenClaw's own note on why, most common first.
                      "why": [w for w, _ in sorted(v["why"].items(), key=lambda kv: -kv[1])[:3]],
                      "avg_secs": round(sum(v["secs"]) / len(v["secs"])) if v["secs"] else None,
                      "last_at": v["last"] or None}
                     for k, v in sorted(seen.items(), key=lambda kv: -kv[1]["count"])[:15]]
    return out


def gateway_log():
    """Error lines from the agent's log in the last day, counted by the
    subsystem tag OpenClaw puts on each line ([telegram], [whatsapp]...)."""
    text = run(["journalctl", "-u", GATEWAY, "--since", "-24h", "--no-pager", "-o", "cat"], timeout=40)
    tags = {}
    for line in text.splitlines():
        low = line.lower()
        if " error" in low or "failed" in low or "exception" in low:
            m = re.search(r"\[([a-z][\w.-]{1,30})\]", line)
            tag = m.group(1) if m else "other"
            tags[tag] = tags.get(tag, 0) + 1
    oom = run(["journalctl", "-k", "--since", "-24h", "--no-pager", "-o", "cat"], timeout=30).count("Out of memory")
    return {"error_lines": tags, "out_of_memory_kills": oom}


def system_events():
    update = read_json("/var/lib/sudo-update-status.json", {}) or {}
    install_log = ""
    try:
        install_log = open("/var/log/sudo-openclaw-install.log", encoding="utf-8", errors="ignore").read()[-4000:]
    except OSError:
        pass
    local = read_json("/var/lib/sudo-localmodel-status", {}) or {}
    config_status = read_json("/var/lib/sudo-openclaw-config-status.json", {}) or {}
    return {
        # False means the agent is running older settings than Sudo wrote.
        "config_accepted": config_status.get("ok") if config_status else None,
        "config_error": scrub(config_status.get("error")) if config_status.get("ok") is False else None,
        "update": {"state": update.get("state"), "message": scrub(update.get("message"))} if update else None,
        "openclaw_installed": os.path.isfile("/opt/openclaw/node_modules/.bin/openclaw"),
        "openclaw_install_failed": (not os.path.isfile("/opt/openclaw/node_modules/.bin/openclaw")
                                    and "=== install-openclaw" in install_log),
        "local_model": {"state": local.get("state"), "model": local.get("model")} if local else None,
    }


def sudo_version():
    update = read_json("/var/lib/sudo-update-status.json", {}) or {}
    m = re.search(r"Installing (\d+\.\d+\.\d+)", str(update.get("message") or ""))
    if update.get("state") == "applying" and m:
        return m.group(1)
    try:
        return open("/opt/sudo/version").read().strip()
    except OSError:
        return None


# ── How it is used (schema 2) ──────────────────────────────────────────────────
OC_STATE = "/opt/sudo/openclaw"
FRUSTRATED = re.compile(
    r"\b(wrong|not what i|that'?s not|no,? that|stop|useless|doesn'?t work|didn'?t work|not working|"
    r"you didn'?t|why did you|i said|i told you|again\?|ugh|wtf|frustrat|"
    r"mal|no es eso|no funciona|no sirve|te dije|otra vez|para ya|equivocad|in[uú]til)\b"
    r"|不对|错了|没用|不行|我说了|خطأ|غلط|لا يعمل|قلت لك|لا فائدة", re.I)
COULDNT = re.compile(
    r"\b(i can'?t|i cannot|i'?m unable|i wasn'?t able|i couldn'?t|sorry|unfortunately|"
    r"no puedo|no pude|lo siento|lamentablemente|perd[oó]n)\b|抱歉|无法|不能|عذرا|آسف|لا أستطيع", re.I)
WEB_TOOLS = ("browser", "web_fetch", "fetch", "web")
FAIL_CLASSES = (
    ("blocked", r"\b(403|401|access denied|forbidden|blocked|not allowed|bot)\b"),
    ("captcha", r"captcha|are you a robot|verify you are human|challenge"),
    ("rate_limited", r"\b429\b|rate.?limit|too many requests"),
    ("timeout", r"time(d)?.?out|timeout"),
    ("not_found", r"\b404\b|not found"),
    ("server_error", r"\b50[0-4]\b|server error|maintenance|unavailable"),
    ("network", r"net::err|enotfound|econnre|dns|connection (refused|reset)"),
)


def channel_of(key):
    key = (key or "").lower()
    for name in ("telegram", "whatsapp", "discord", "imessage", "slack"):
        if f":{name}:" in key or key.startswith(name):
            return name
    if "heartbeat" in key:
        return "checkin"
    if key.startswith("cron") or ":cron:" in key:
        return "reminder"
    if "dashboard" in key or "openai" in key or key.endswith(":main"):
        return "dashboard"
    return "other"


def key_in_transcript(lines):
    """Newer transcripts name their session in a session.started line."""
    for line in lines[:8]:
        if '"sessionKey"' in line:
            try:
                return str(json.loads(line).get("sessionKey") or "")
            except ValueError:
                pass
    return ""


def channel_of_prompt(text):
    """A transcript with no session name: check-ins and reminders start with
    OpenClaw's own prompts, which no person types."""
    # Near the start, not at it: OpenClaw puts a timestamp in front of the
    # prompt, which kept 13 check-ins a day labelled "other" in 1.0.45.
    head = (text or "")[:240]
    if "Read HEARTBEAT.md" in head:
        return "checkin"
    if "[cron:" in head:
        return "reminder"
    return "other"


# What kind of error a tool returned, by pattern only: the report carries the
# kind and a count, never the error text (it can name people or places).
TOOL_ERROR_CLASSES = (
    ("not_configured", r"not configured|no .{0,30}provider|currently available|needs? an? .{0,20}(api )?key"),
    ("blocked", r"not on (the|their) list|blocked|not allowed|forbidden|denied|not permitted|cancel"),
    ("not_visible", r"visibilit|not visible|outside (the|this|your) (scope|tree)"),
    ("not_connected", r"not (connected|linked|running|logged in)|disconnected|no active|offline"),
    ("bad_target", r"invalid (target|number|recipient|chat|jid)|unknown (target|recipient|channel|account)"),
    ("timeout", r"time(d)?.?out|timeout"),
    ("not_found", r"\b404\b|not found|no such|does not exist|enoent"),
    ("rate_limited", r"\b429\b|rate.?limit|too many requests"),
    ("network", r"net::err|enotfound|econnre|dns|connection (refused|reset)|fetch failed"),
)
AUTOMATED = ("checkin", "reminder")


def site_of(args):
    """The site a web tool was pointed at: example.com, nothing else of the URL."""
    url = ""
    if isinstance(args, dict):
        url = str(args.get("url") or args.get("targetUrl") or args.get("href") or "")
    m = re.match(r"https?://([^/:?#]+)", url)
    if not m:
        return None
    host = m.group(1).lower()
    parts = host.split(".")
    two = {"co", "com", "org", "net", "gov", "ac", "edu"}
    return ".".join(parts[-3:] if len(parts) > 2 and parts[-2] in two else parts[-2:])


def text_of(message):
    out = []
    for item in message.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            out.append(str(item.get("text") or ""))
        elif isinstance(item, str):
            out.append(item)
    return " ".join(out)


def words(text):
    return set(re.findall(r"\w+", text.lower()))


def conversations(since_ms):
    """Counts from the agent's own transcripts. Text is looked at here, to
    count, and goes nowhere."""
    out = {"messages": {}, "replies": 0, "tools": {}, "web_failures": {}, "models": {},
           "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "reply_secs": [], "slow_replies": 0,
           "stop_reasons": {}, "empty_replies": 0, "checkins": {"runs": 0, "sent": 0},
           "bad_experience": {"repeated": 0, "frustrated": 0, "agent_couldnt": 0, "error_shown": 0,
                              "ended_after_error": 0, "loop_stopped": 0}}
    for sessions_dir in glob.glob(os.path.join(OC_STATE, "agents", "*", "sessions")):
        store = read_json(os.path.join(sessions_dir, "sessions.json"), {}) or {}
        key_of = {}
        for k, v in (store.items() if isinstance(store, dict) else []):
            if isinstance(v, dict) and v.get("sessionId"):
                key_of[v["sessionId"]] = k
        for path in glob.glob(os.path.join(sessions_dir, "*.jsonl")):
            try:
                if os.path.getmtime(path) * 1000 < since_ms:
                    continue
            except OSError:
                continue
            sid = os.path.basename(path)[:-6]
            calls = {}
            last_user = None   # (ts, words)
            pending_user_ts = None
            last_kind = None
            heartbeat_text = []
            try:
                lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
            except OSError:
                continue
            # sessions.json only maps each session to its latest transcript, so
            # every earlier check-in or reminder run (and any chat from before
            # a session reset) came out as "other" -- 13 a day on beta devices,
            # which read as people chatting. The transcript's own header names
            # its session when it has one; otherwise the first prompt says.
            chan = channel_of(key_of.get(sid) or key_in_transcript(lines))
            for line in lines:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "message":
                    continue
                m = d.get("message") or {}
                ts = m.get("timestamp") or 0
                if not isinstance(ts, (int, float)) or ts < since_ms:
                    continue
                role = m.get("role")
                if role == "user":
                    text = text_of(m)
                    if chan == "other":
                        chan = channel_of_prompt(text)
                    out["messages"][chan] = out["messages"].get(chan, 0) + 1
                    if chan in AUTOMATED:
                        # The device prompting itself: not a person, so no
                        # repeats, frustration or reply time to count.
                        last_kind = "user"
                        continue
                    w = words(text)
                    if last_user and ts - last_user[0] < 10 * 60 * 1000 and w and last_user[1]:
                        if len(w & last_user[1]) / max(1, len(w | last_user[1])) >= 0.6:
                            out["bad_experience"]["repeated"] += 1
                    if FRUSTRATED.search(text):
                        out["bad_experience"]["frustrated"] += 1
                    last_user, pending_user_ts, last_kind = (ts, w), ts, "user"
                elif role == "assistant":
                    usage = m.get("usage") or {}
                    out["tokens_in"] += int(usage.get("input") or 0) + int(usage.get("cacheRead") or 0)
                    out["tokens_out"] += int(usage.get("output") or 0)
                    out["cost_usd"] += float(((usage.get("cost") or {}).get("total")) or 0)
                    model = m.get("model") or "?"
                    out["models"][model] = out["models"].get(model, 0) + 1
                    stop = m.get("stopReason") or "?"
                    out["stop_reasons"][stop] = out["stop_reasons"].get(stop, 0) + 1
                    text = text_of(m)
                    tool_calls = [c for c in (m.get("content") or [])
                                  if isinstance(c, dict) and c.get("type") in ("toolCall", "tool_use")]
                    for c in tool_calls:
                        calls[c.get("id")] = (c.get("name") or "?", c.get("arguments") or c.get("input") or {})
                        name = c.get("name") or "?"
                        t = out["tools"].setdefault(name, {"calls": 0, "errors": 0})
                        t["calls"] += 1
                    if text.strip():
                        out["replies"] += 1
                        if chan == "checkin":
                            heartbeat_text.append(text)
                        if COULDNT.search(text) and chan not in AUTOMATED:
                            out["bad_experience"]["agent_couldnt"] += 1
                        if pending_user_ts:
                            secs = (ts - pending_user_ts) / 1000
                            if secs >= 0:
                                out["reply_secs"].append(secs)
                                if secs > 60:
                                    out["slow_replies"] += 1
                            pending_user_ts = None
                    elif not tool_calls:
                        out["empty_replies"] += 1
                    if stop in ("error", "aborted"):
                        out["bad_experience"]["error_shown"] += 1
                    last_kind = "error" if stop in ("error", "aborted") else "assistant"
                elif role == "toolResult":
                    name = m.get("toolName") or calls.get(m.get("toolCallId"), ("?", {}))[0]
                    result = text_of(m)
                    if "loop detect" in result.lower() or "loopdetection" in result.lower():
                        out["bad_experience"]["loop_stopped"] += 1
                    if m.get("isError"):
                        t = out["tools"].setdefault(name, {"calls": 0, "errors": 0})
                        t["errors"] += 1
                        kind = next((c for c, rx in TOOL_ERROR_CLASSES if re.search(rx, result, re.I)), "other")
                        t.setdefault("why", {})[kind] = t.get("why", {}).get(kind, 0) + 1
                        if any(w_ in name.lower() for w_ in WEB_TOOLS):
                            site = site_of(calls.get(m.get("toolCallId"), ("", {}))[1]) or "unknown"
                            why = next((c for c, rx in FAIL_CLASSES if re.search(rx, result, re.I)), "other")
                            key = f"{site} ({why})"
                            out["web_failures"][key] = out["web_failures"].get(key, 0) + 1
            if chan == "checkin" and (heartbeat_text or last_kind):
                out["checkins"]["runs"] += 1
                if heartbeat_text and "HEARTBEAT_OK" not in heartbeat_text[-1]:
                    out["checkins"]["sent"] += 1
            if last_kind == "error":
                out["bad_experience"]["ended_after_error"] += 1
    secs = sorted(out.pop("reply_secs"))
    out["reply_secs_median"] = round(secs[len(secs) // 2], 1) if secs else None
    out["reply_secs_p90"] = round(secs[int(len(secs) * 0.9)], 1) if secs else None
    out["cost_usd"] = round(out["cost_usd"], 4)
    out["tools"] = dict(sorted(out["tools"].items(), key=lambda kv: -kv[1]["calls"])[:20])
    return out


def setup_and_settings(cfg):
    oc = read_json(os.path.join(OC_STATE, "openclaw.json"), {}) or {}
    tg = (oc.get("channels") or {}).get("telegram") or {}
    agents = ((oc.get("agents") or {}).get("list") or [])
    spend = read_json("/opt/sudo/spend-usd.json", {}) or {}
    return {
        "agents": max(1, len(agents)),
        "telegram_bots": len(tg.get("accounts") or {}) or (1 if tg.get("botToken") else 0),
        "whatsapp_accounts": len(cfg.get("whatsapp_numbers") or {}),
        "settings": {
            "shell": bool(cfg.get("exec_enabled", True)), "helpers": bool(cfg.get("subagent_enabled")),
            "skills": bool(cfg.get("skills_enabled")), "full_access": bool(cfg.get("full_access_enabled")),
            "spend_guard": bool(cfg.get("spend_enabled", True)),
            "dollar_limits": bool(cfg.get("spend_usd_day") or cfg.get("spend_usd_month")),
            "checkins": bool(cfg.get("checkins_enabled", True)), "prefer_local": bool(cfg.get("prefer_local")),
            "personality": cfg.get("personality"), "ui_language": cfg.get("ui_language"),
            "agent_language": cfg.get("agent_language"), "theme": cfg.get("theme"),
            "timezone_pinned": bool(cfg.get("timezone_pinned")),
            "remote_link": bool(cfg.get("remote_access")),
        },
        "spend_usd_today": spend.get("spent_day") if spend.get("day") == time.strftime("%Y-%m-%d") else None,
        "setup_done": {"profile": bool(cfg.get("profile_done")),
                       "cloud_key": bool((cfg.get("provider_keys") or {}) or cfg.get("user_openrouter_key")),
                       "channel": bool(cfg.get("whatsapp_numbers") or (cfg.get("telegram") or {}).get("owner_id")),
                       "apps": bool((cfg.get("composio_api_key") or "").strip())},
    }


def dashboard_use(window_h):
    """The dashboard's own counters (server.py count_usage): tabs opened and
    thumbs-down on replies, per day; summed over the report's window."""
    days = (read_json("/var/lib/sudo-usage.json", {}) or {}).get("days") or {}
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - window_h * 3600))
    out = {"tabs": {}, "thumbs_down": 0, "reports_sent": 0}
    for day, counts in days.items():
        if day < since or not isinstance(counts, dict):
            continue
        for tab, n in (counts.get("tabs") or {}).items():
            out["tabs"][tab] = out["tabs"].get(tab, 0) + int(n or 0)
        out["thumbs_down"] += int(counts.get("thumbs_down") or 0)
        out["reports_sent"] += int(counts.get("reports_sent") or 0)
    return out


def channel_health():
    """Is each linked chat app actually connected right now?"""
    env = dict(os.environ, HOME="/root", OPENCLAW_STATE_DIR=OC_STATE,
               OPENCLAW_CONFIG_PATH=os.path.join(OC_STATE, "openclaw.json"))
    try:
        out = subprocess.run(["/opt/openclaw/node_modules/.bin/openclaw", "channels", "status", "--json"],
                             capture_output=True, text=True, timeout=60, env=env).stdout
        data = json.loads(out[out.find("{"):]) if "{" in out else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    health = {}
    for chan, accounts in (data.get("channelAccounts") or {}).items():
        for a in accounts or []:
            health[f"{chan}:{a.get('accountId') or 'default'}"] = {
                "linked": a.get("linked"), "connected": a.get("connected"), "running": a.get("running")}
    return health


def collect():
    cfg = read_json(CONFIG, {}) or {}
    now = time.time()
    # Two weeks the first time this version of the report goes out (the
    # schema-1 reports before it had no usage), then one day at a time.
    last = read_json(LAST_FILE, {}) or {}
    window = 24 if last.get("sent") and (last.get("report") or {}).get("lookback_v2") else 14 * 24
    return {
        "schema": 2,
        "lookback_v2": True,
        "sent_at": int(now),
        "sudo": sudo_version(),
        "device": device(),
        "agent": agent(cfg),
        # The first report after opting in looks back two weeks, so problems
        # from before the owner said yes are in it too; then one day at a time.
        "reminders": reminders(int((now - window * 3600) * 1000), window),
        "log_24h": gateway_log(),
        "system": system_events(),
        "usage": conversations(int((now - window * 3600) * 1000)),
        "setup": setup_and_settings(cfg),
        "channel_health": channel_health(),
        "dashboard": dashboard_use(window),
    }


def report_key():
    data = read_json(KEY_FILE, {}) or {}
    if not data.get("key"):
        data = {"key": secrets.token_urlsafe(32), "created_at": int(time.time())}
        os.makedirs(os.path.dirname(KEY_FILE), exist_ok=True)
        with open(KEY_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(KEY_FILE + ".tmp", 0o600)
        os.replace(KEY_FILE + ".tmp", KEY_FILE)
    return data["key"]


def post(route, body):
    req = urllib.request.Request(SERVICE + route, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Sudo-Report-Key": report_key()})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, json.loads(resp.read() or b"{}")


def save_last(entry):
    try:
        with open(LAST_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(entry, f, indent=2)
        os.chmod(LAST_FILE + ".tmp", 0o644)
        os.replace(LAST_FILE + ".tmp", LAST_FILE)
    except OSError:
        pass


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "collect"
    cfg = read_json(CONFIG, {}) or {}
    if cmd == "collect":
        print(json.dumps(collect(), indent=2))
        return 0
    if cmd == "send":
        if cfg.get("problem_reports") is not True:
            print(json.dumps({"sent": False, "reason": "off"}))
            return 0
        report = collect()
        try:
            status, reply = post("/report", report)
            ok = status == 200
        except Exception as exc:  # noqa: BLE001 - offline: try again tomorrow
            ok, reply = False, {"error": scrub(exc)}
        save_last({"sent": ok, "at": int(time.time()), "reply": reply, "report": report})
        print(json.dumps({"sent": ok, "reply": reply}))
        return 0 if ok else 1
    if cmd == "feedback":
        try:
            body = json.load(sys.stdin)
        except ValueError:
            print(json.dumps({"sent": False, "error": "not JSON"}))
            return 2
        body = {"sudo": sudo_version(), "source": str(body.get("source") or "dashboard")[:20],
                "note": str(body.get("note") or "")[:2000], "exchange": body.get("exchange") or {}}
        try:
            status, reply = post("/feedback", body)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"sent": False, "error": scrub(exc)}))
            return 1
        print(json.dumps({"sent": status == 200, "reply": reply}))
        return 0 if status == 200 else 1
    if cmd == "forget":
        try:
            status, reply = post("/forget", {})
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"forgotten": False, "error": scrub(exc)}))
            return 1
        try:
            os.remove(LAST_FILE)
        except OSError:
            pass
        print(json.dumps({"forgotten": status == 200, "reply": reply}))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
