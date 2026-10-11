"""Screen time: minutes per app per day for the devices being watched.

The filter (AdGuard Home, see home_filter.py) keeps a week of DNS lookups.
That is too short for trends, so this module reads it incrementally and keeps
a small summary per device per day, for 90 days:

  /opt/sudo/home/screentime/<mac>.json
    {"cursor": <last lookup time processed, ms>,
     "days": {"2026-10-11": {"apps": {"youtube": 42, ...},   # minutes
                             "minutes": 61,                   # distinct active minutes
                             "hours": [0, 0, ..., 12, ...]}}} # active minutes per hour

A minute counts for an app when the device asked for that app at least twice
in it (one lone lookup is usually something refreshing in the background).
The day's total is the number of distinct minutes with any app, so two apps
open at once are not counted twice. Only apps the filter recognises count:
the system's own background traffic is not screen time.
"""
import json
import os
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

import home_filter

DIR = "/opt/sudo/home/screentime"
KEEP_DAYS = 90
_lock = threading.Lock()
_last_refresh = {}


def _path(mac):
    return os.path.join(DIR, mac.replace(":", "") + ".json")


def _load(mac):
    try:
        with open(_path(mac), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"cursor": 0, "days": {}, "minutes_seen": {}}


def _save(mac, data):
    os.makedirs(DIR, exist_ok=True)
    tmp = _path(mac) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _path(mac))


def refresh(mac, ip, max_pages=40):
    """Fold new lookups for one device into its daily summary."""
    if not ip or not home_filter.installed():
        return
    with _lock:
        data = _load(mac)
        cursor = data.get("cursor", 0)
        matchers = home_filter._service_matchers()
        if not matchers:
            return
        hits = {}               # (app, minute) -> lookups
        newest = cursor
        older = ""
        for _ in range(max_pages):
            q = f"/querylog?limit=500&search={ip}" + (f"&older_than={urllib.parse.quote(older)}" if older else "")
            try:
                page = home_filter.call(q, timeout=25)
            except Exception:      # noqa: BLE001
                break
            entries = page.get("data") or []
            if not entries:
                break
            reached = False
            for e in entries:
                if e.get("client") != ip:
                    continue
                ts_ms = int(home_filter._ts(e.get("time")) * 1000)
                if ts_ms <= cursor:
                    reached = True
                    continue
                newest = max(newest, ts_ms)
                host = ((e.get("question") or {}).get("name") or "").rstrip(".").lower()
                for suffix, sid, _name, _group in matchers:
                    if host == suffix or host.endswith("." + suffix):
                        key = (sid, ts_ms // 60000)
                        hits[key] = hits.get(key, 0) + 1
                        break
            older = page.get("oldest") or ""
            if reached or not older:
                break
        # Lookups for the same minute can straddle two refreshes; carry the
        # counts of the newest minutes so a minute is judged on all of them.
        carry = {tuple(k.split("|", 1)): v for k, v in (data.get("carry") or {}).items()}
        for (sid, minute), n in carry.items():
            key = (sid, int(minute))
            hits[key] = hits.get(key, 0) + n
        newest_minute = newest // 60000
        days = data.setdefault("days", {})
        seen = data.setdefault("minutes_seen", {})        # day -> [minutes already counted]
        new_carry = {}
        for (sid, minute), n in sorted(hits.items(), key=lambda kv: kv[0][1]):
            if minute >= newest_minute - 1 and n < 2:
                new_carry[f"{sid}|{minute}"] = n            # might still get its second lookup
                continue
            if n < 2:
                continue
            when = datetime.fromtimestamp(minute * 60)
            day = when.strftime("%Y-%m-%d")
            rec = days.setdefault(day, {"apps": {}, "minutes": 0, "hours": [0] * 24})
            app_minutes = seen.setdefault(day + "|" + sid, [])
            if minute not in app_minutes:
                app_minutes.append(minute)
                rec["apps"][sid] = rec["apps"].get(sid, 0) + 1
            any_minutes = seen.setdefault(day, [])
            if minute not in any_minutes:
                any_minutes.append(minute)
                rec["minutes"] += 1
                rec["hours"][when.hour] += 1
        data["carry"] = new_carry
        data["cursor"] = max(cursor, newest)
        cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
        data["days"] = {d: v for d, v in days.items() if d >= cutoff}
        today = datetime.now().strftime("%Y-%m-%d")
        # Per-minute bookkeeping is only needed while a day can still change.
        data["minutes_seen"] = {k: v for k, v in seen.items() if k[:10] >= (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")}
        _save(mac, data)
        _last_refresh[mac] = time.time()


def refresh_all(devices, every=300):
    """From the Home scanner: keep every watched device's summary current."""
    for mac, p in home_filter.profiles().items():
        ip = (devices.get(mac) or {}).get("ip")
        if ip and time.time() - _last_refresh.get(mac, 0) >= every:
            try:
                refresh(mac, ip)
            except Exception as exc:      # noqa: BLE001
                print(f"screen time refresh failed for {mac}: {exc}")


def _names():
    return {s["id"]: (s.get("name", s["id"]), home_filter.CATEGORY.get(s.get("group_id", ""), "other"))
            for s in home_filter.services()}


def summary(macs, days=7):
    """Trends for one device or several together ("everyone").

    series: one entry per day, oldest first, with minutes per category.
    top:    apps this period vs the period before, most used first.
    hours:  active minutes per hour of day over the period.
    """
    names = _names()
    today = datetime.now().date()
    span = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    prev = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(2 * days - 1, days - 1, -1)]
    stores = [_load(m) for m in macs]
    series = []
    for day in span:
        cats, total = {}, 0
        for st in stores:
            rec = (st.get("days") or {}).get(day) or {}
            total += rec.get("minutes", 0)
            for sid, mins in (rec.get("apps") or {}).items():
                cat = names.get(sid, (sid, "other"))[1]
                cats[cat] = cats.get(cat, 0) + mins
        series.append({"day": day, "total": total, "categories": cats})
    apps_now, apps_before, hours = {}, {}, [0] * 24
    for st in stores:
        for day in span:
            rec = (st.get("days") or {}).get(day) or {}
            for sid, mins in (rec.get("apps") or {}).items():
                apps_now[sid] = apps_now.get(sid, 0) + mins
            for h, mins in enumerate(rec.get("hours") or [0] * 24):
                hours[h] += mins
        for day in prev:
            rec = (st.get("days") or {}).get(day) or {}
            for sid, mins in (rec.get("apps") or {}).items():
                apps_before[sid] = apps_before.get(sid, 0) + mins
    top = sorted(({"id": sid, "name": names.get(sid, (sid,))[0], "category": names.get(sid, (sid, "other"))[1],
                   "minutes": m, "before": apps_before.get(sid, 0)} for sid, m in apps_now.items()),
                 key=lambda a: -a["minutes"])
    today_total = series[-1]["total"] if series else 0
    has_history = any((st.get("days") or {}) for st in stores)
    return {"days": days, "series": series, "top": top[:15], "hours": hours,
            "today": today_total, "period_total": sum(s["total"] for s in series),
            "has_history": has_history}
