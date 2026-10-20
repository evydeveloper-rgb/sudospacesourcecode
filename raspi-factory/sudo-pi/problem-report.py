#!/usr/bin/env python3
"""problem-report.py — the daily problem report beta owners can opt in to.

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

Run as root (sudo-problem-report.timer, daily): the gateway journal and the
OpenClaw state database need it.
"""

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
            if text:
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
    local = (open("/var/lib/sudo-localmodel-status").read().strip()
             if os.path.isfile("/var/lib/sudo-localmodel-status") else "")
    return {
        "update": {"state": update.get("state"), "message": scrub(update.get("message"))} if update else None,
        "openclaw_installed": os.path.isfile("/opt/openclaw/node_modules/.bin/openclaw"),
        "openclaw_install_failed": (not os.path.isfile("/opt/openclaw/node_modules/.bin/openclaw")
                                    and "=== install-openclaw" in install_log),
        "local_model": scrub(local, 60) or None,
    }


def collect():
    cfg = read_json(CONFIG, {}) or {}
    now = time.time()
    window = 24 if (read_json(LAST_FILE, {}) or {}).get("sent") else 14 * 24
    return {
        "schema": 1,
        "sent_at": int(now),
        "sudo": (open("/opt/sudo/version").read().strip() if os.path.isfile("/opt/sudo/version") else None),
        "device": device(),
        "agent": agent(cfg),
        # The first report after opting in looks back two weeks, so problems
        # from before the owner said yes are in it too; then one day at a time.
        "reminders": reminders(int((now - window * 3600) * 1000), window),
        "log_24h": gateway_log(),
        "system": system_events(),
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
