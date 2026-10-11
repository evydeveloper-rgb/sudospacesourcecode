"""Home: kids profiles and screen time, on top of AdGuard Home.

install-adguard.sh puts AdGuard Home on the Pi; this module drives it. A
device only goes through the filter when its DNS points at this Pi (or the
router hands this Pi out), so nothing changes for anyone else.

Per device, the owner picks a profile:
  kids  -- safe search forced on (Google, YouTube, Bing...), adult sites,
           gambling and dating blocked, plus whichever apps they choose;
           a bedtime when the internet is off; a pause button.
  track -- nothing blocked; the device is named in the filter's log so its
           screen time can be estimated (the owner's own phone, say).

Bedtime and pause are DNS rules this module switches on and off itself
("/.*/$client='<ip>'" answers nothing for that one device), checked every
minute from the Home scanner. Profiles live in /opt/sudo/home/kids.json.
"""
import base64
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timedelta

CRED = "/etc/sudo/adguard.json"
STATUS = "/var/lib/sudo-adguard-status.json"
PROFILES = "/opt/sudo/home/kids.json"
INSTALL_SCRIPT = "/usr/local/bin/sudo-install-adguard.sh"
RULES_BEGIN, RULES_END = "! sudo:begin", "! sudo:end"
CLIENT_PREFIX = "sudo-"

# The apps parents ask about, by AdGuard's service id.
APPS = [
    ("youtube", "YouTube"), ("tiktok", "TikTok"), ("roblox", "Roblox"), ("minecraft", "Minecraft"),
    ("epic_games", "Fortnite"), ("instagram", "Instagram"), ("snapchat", "Snapchat"), ("discord", "Discord"),
    ("netflix", "Netflix"), ("twitch", "Twitch"), ("whatsapp", "WhatsApp"), ("steam", "Steam"),
]
ALWAYS_BLOCKED_GROUPS = ("gambling", "dating")      # every kids device, not a choice
# Screen-time categories, from AdGuard's service groups.
CATEGORY = {"streaming": "video", "social_network": "social", "gaming": "games", "messenger": "messaging",
            "shopping": "shopping", "ai": "ai"}

_lock = threading.RLock()
_services = {"at": 0, "list": []}
_last_rules = None


# ── storage ──────────────────────────────────────────────────────────────────

def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def profiles():
    return _read(PROFILES, {})


# ── AdGuard Home's API ───────────────────────────────────────────────────────

def installed():
    return os.path.isfile(CRED)


def install_status():
    s = _read(STATUS, {})
    if installed() and s.get("state") in (None, "ready"):
        return {"state": "ready", "message": ""}
    return {"state": s.get("state", "absent"), "message": s.get("message", "")}


def call(path, body=None, method=None, timeout=15):
    cred = _read(CRED, {})
    auth = "Basic " + base64.b64encode(f'{cred["user"]}:{cred["password"]}'.encode()).decode()
    req = urllib.request.Request(
        cred.get("web", "http://127.0.0.1:3010") + "/control" + path,
        method=method or ("POST" if body is not None else "GET"),
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read()
    return json.loads(text) if text.strip() else {}


def services():
    """AdGuard's built-in service list (id, name, group, rules), cached a day."""
    with _lock:
        if time.time() - _services["at"] < 86400 and _services["list"]:
            return _services["list"]
        try:
            _services["list"] = call("/blocked_services/all").get("blocked_services", [])
            _services["at"] = time.time()
        except Exception:      # noqa: BLE001 -- the filter may still be starting
            pass
        return _services["list"]


def _group_ids(groups):
    return sorted(s["id"] for s in services() if s.get("group_id") in groups)


# ── profiles → filter ────────────────────────────────────────────────────────

def _hm(text, default):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(text or ""))
    return (int(m.group(1)) % 24, int(m.group(2)) % 60) if m else default


def in_bedtime(p, now=None):
    bed = p.get("bedtime") or {}
    if not bed.get("on"):
        return False
    now = now or datetime.now()
    start, end = _hm(bed.get("start"), (20, 0)), _hm(bed.get("end"), (7, 0))
    cur = (now.hour, now.minute)
    return (start <= cur < end) if start < end else (cur >= start or cur < end)


def is_paused(p, now_ms=None):
    until = p.get("paused_until") or 0
    return until == -1 or (until > 0 and until > (now_ms or time.time() * 1000))


def _client(mac, p, ip, label):
    name = CLIENT_PREFIX + mac.replace(":", "")
    if p.get("profile") == "kids":
        blocked = sorted(set(p.get("apps") or []) | set(_group_ids(ALWAYS_BLOCKED_GROUPS)))
        return {
            "name": name, "ids": [ip], "tags": [], "upstreams": [],
            "use_global_settings": False, "filtering_enabled": True,
            "parental_enabled": p.get("adult", True), "safebrowsing_enabled": True,
            "safe_search": {"enabled": True, "bing": True, "duckduckgo": True, "ecosia": True,
                            "google": True, "pixabay": True, "yandex": True, "youtube": True},
            "use_global_blocked_services": False, "blocked_services": blocked,
            "blocked_services_schedule": {"time_zone": "Local"},
        }
    return {  # track: named in the log, everything else as normal
        "name": name, "ids": [ip], "tags": [], "upstreams": [], "use_global_settings": True,
        "use_global_blocked_services": True, "blocked_services": [],
        "blocked_services_schedule": {"time_zone": "Local"},
        "filtering_enabled": True, "parental_enabled": False, "safebrowsing_enabled": False,
    }


def sync(devices):
    """Make the filter match kids.json: clients, then bedtime/pause rules."""
    global _last_rules
    if not installed():
        return
    profs = profiles()
    try:
        current = {c["name"]: c for c in (call("/clients").get("clients") or [])
                   if c.get("name", "").startswith(CLIENT_PREFIX)}
    except Exception:      # noqa: BLE001
        return
    wanted = {}
    for mac, p in profs.items():
        d = devices.get(mac) or {}
        ip = d.get("ip")
        if ip and p.get("profile") in ("kids", "track"):
            c = _client(mac, p, ip, d.get("name") or "")
            wanted[c["name"]] = c
    for name, c in wanted.items():
        if name not in current:
            call("/clients/add", c)
        else:
            have = current[name]
            if (have.get("ids") != c["ids"] or have.get("use_global_settings") != c["use_global_settings"]
                    or sorted(have.get("blocked_services") or []) != c["blocked_services"]
                    or have.get("parental_enabled") != c.get("parental_enabled")):
                call("/clients/update", {"name": name, "data": c})
    for name in current:
        if name not in wanted:
            call("/clients/delete", {"name": name})
    # Bedtime and pause: one rule per device that should be offline right now.
    now = datetime.now()
    rules = [f"/.*/$client='{devices[mac]['ip']}'" for mac, p in sorted(profs.items())
             if p.get("profile") == "kids" and (devices.get(mac) or {}).get("ip")
             and (is_paused(p) or in_bedtime(p, now))]
    if rules == _last_rules:
        return
    try:
        existing = call("/filtering/status").get("user_rules") or []
    except Exception:      # noqa: BLE001
        return
    keep, inside = [], False
    for line in existing:
        if line.strip() == RULES_BEGIN:
            inside = True
        elif line.strip() == RULES_END:
            inside = False
        elif not inside:
            keep.append(line)
    block = [RULES_BEGIN, *rules, RULES_END] if rules else []
    call("/filtering/set_rules", {"rules": keep + block})
    _last_rules = rules


def set_profile(mac, changes, devices):
    with _lock:
        profs = profiles()
        p = dict(profs.get(mac) or {})
        if "profile" in changes:
            if changes["profile"] in ("kids", "track"):
                p["profile"] = changes["profile"]
                p.setdefault("adult", True)
                p.setdefault("apps", [])
                p.setdefault("bedtime", {"on": False, "start": "20:00", "end": "07:00"})
            else:
                profs.pop(mac, None)
                _write(PROFILES, profs)
                sync(devices)
                return None
        valid_apps = {a for a, _ in APPS}
        if "apps" in changes:
            p["apps"] = sorted(a for a in changes["apps"] if a in valid_apps)
        if "adult" in changes:
            p["adult"] = bool(changes["adult"])
        if "bedtime" in changes and isinstance(changes["bedtime"], dict):
            b = dict(p.get("bedtime") or {})
            for k in ("on",):
                if k in changes["bedtime"]:
                    b[k] = bool(changes["bedtime"][k])
            for k in ("start", "end"):
                if k in changes["bedtime"] and _hm(changes["bedtime"][k], None):
                    hh, mm = _hm(changes["bedtime"][k], None)
                    b[k] = f"{hh:02d}:{mm:02d}"
            p["bedtime"] = b
        if "pause_minutes" in changes:
            mins = int(changes["pause_minutes"] or 0)
            p["paused_until"] = -1 if mins < 0 else (int(time.time() * 1000) + mins * 60000 if mins else 0)
        if not p.get("profile"):
            return None
        profs[mac] = p
        _write(PROFILES, profs)
    sync(devices)
    return p


# ── what devices are actually using the filter ───────────────────────────────

def recent_clients(window_s=7200):
    """{ip: last query ms} for devices that asked the filter recently."""
    seen = {}
    try:
        data = call("/querylog?limit=500", timeout=20).get("data") or []
    except Exception:      # noqa: BLE001
        return seen
    cutoff = time.time() - window_s
    for e in data:
        ts = _ts(e.get("time"))
        if ts and ts >= cutoff:
            ip = e.get("client")
            seen[ip] = max(seen.get(ip, 0), int(ts * 1000))
    return seen


def _ts(text):
    if not text:
        return 0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


# ── screen time ──────────────────────────────────────────────────────────────

def _service_matchers():
    out = []
    for s in services():
        for rule in s.get("rules") or []:
            m = re.match(r"^\|\|([a-z0-9.-]+)\^", rule)
            if m:
                out.append((m.group(1), s["id"], s.get("name", s["id"]), s.get("group_id", "")))
    out.sort(key=lambda x: -len(x[0]))       # longest suffix first
    return out


def screen_time(ip, days=7):
    """Estimated minutes per service for one device over the last `days`.

    A minute counts for a service when the device asked for that service at
    least twice in it -- one lone lookup is usually an app refreshing in the
    background. An estimate: lookups are cached, some apps hide them, and on
    mobile data the phone is not on the home Wi-Fi at all.
    """
    if not installed() or not ip:
        return {"services": [], "categories": [], "days": days}
    matchers = _service_matchers()
    since = time.time() - days * 86400
    today = datetime.now().date()
    hits = {}            # (service, minute) -> count
    meta = {}
    older = ""
    for _ in range(40):                       # at most 40 pages of 500
        q = f"/querylog?limit=500&search={ip}" + (f"&older_than={urllib.request.quote(older)}" if older else "")
        try:
            page = call(q, timeout=25)
        except Exception:      # noqa: BLE001
            break
        data = page.get("data") or []
        if not data:
            break
        stop = False
        for e in data:
            if e.get("client") != ip:
                continue
            ts = _ts(e.get("time"))
            if ts < since:
                stop = True
                continue
            host = ((e.get("question") or {}).get("name") or "").rstrip(".").lower()
            for suffix, sid, name, group in matchers:
                if host == suffix or host.endswith("." + suffix):
                    minute = int(ts // 60)
                    hits[(sid, minute)] = hits.get((sid, minute), 0) + 1
                    meta[sid] = (name, group)
                    break
        older = page.get("oldest") or (data[-1].get("time") if data else "")
        if stop or not older:
            break
    per = {}
    for (sid, minute), n in hits.items():
        if n < 2:
            continue
        day = datetime.fromtimestamp(minute * 60).date()
        rec = per.setdefault(sid, {"id": sid, "name": meta[sid][0], "group": meta[sid][1],
                                   "category": CATEGORY.get(meta[sid][1], "other"), "today": 0, "week": 0})
        rec["week"] += 1
        if day == today:
            rec["today"] += 1
    items = sorted(per.values(), key=lambda r: -r["week"])
    cats = {}
    for r in items:
        c = cats.setdefault(r["category"], {"category": r["category"], "today": 0, "week": 0})
        c["today"] += r["today"]
        c["week"] += r["week"]
    return {"services": items[:12], "categories": sorted(cats.values(), key=lambda c: -c["week"]), "days": days}


# ── what the dashboard shows ─────────────────────────────────────────────────

def snapshot(devices, own_ip=""):
    st = install_status()
    profs = profiles()
    recent = recent_clients() if st["state"] == "ready" else {}
    out = []
    for mac, p in profs.items():
        d = devices.get(mac) or {}
        ip = d.get("ip", "")
        out.append({
            "mac": mac, "profile": p.get("profile"), "ip": ip,
            "apps": p.get("apps") or [], "adult": p.get("adult", True),
            "bedtime": p.get("bedtime") or {"on": False, "start": "20:00", "end": "07:00"},
            "paused": is_paused(p), "paused_until": p.get("paused_until") or 0,
            "in_bedtime": in_bedtime(p), "using_filter": ip in recent,
        })
    return {"status": st, "dns_ip": own_ip, "devices": out,
            "apps": [{"id": a, "name": n} for a, n in APPS]}


def install(run_privileged):
    """Start install-adguard.sh outside the dashboard's sandbox."""
    _write(STATUS, {"state": "installing", "message": "Starting…", "updated": int(time.time() * 1000)})
    return run_privileged(f"sudo-adguard-{int(time.time())}", INSTALL_SCRIPT, wait=False, timeout=20)
