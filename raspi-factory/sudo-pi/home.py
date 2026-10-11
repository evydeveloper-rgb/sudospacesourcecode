#!/usr/bin/env python3
"""sudo-home.py -- the Home app from the command line, for the agent.

The agent runs this with its exec tool when the owner asks for something in
chat ("pause the kids' iPad for 30 minutes", "who's home?"). It talks to the
dashboard on this Pi, so it does exactly what the Home app's buttons do.

  sudo-home.py devices                       what's on the Wi-Fi
  sudo-home.py who                           who is home (if tracking is on)
  sudo-home.py kids                          kids devices and their settings
  sudo-home.py pause <device> [minutes]      no minutes = until resumed
  sudo-home.py resume <device>
  sudo-home.py block <device> <app>          app: youtube, tiktok, roblox...
  sudo-home.py unblock <device> <app>
  sudo-home.py bedtime <device> <HH:MM> <HH:MM> | off
  sudo-home.py time <device> [days]          estimated time per app

<device> matches a device's name, its owner's name or its address.
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1"


def api(path, body=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = str(e)
        sys.exit(f"error: {msg}")


def fmt(m):
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def find(query, devices, prefer=None):
    q = query.strip().lower()
    hits = [d for d in devices if q in (d.get("label") or "").lower() or q in (d.get("name") or "").lower()
            or q == (d.get("person") or "").lower() or q in (d.get("person") or "").lower() or q == d.get("ip")]
    if prefer:
        preferred = [d for d in hits if d["mac"] in prefer]
        hits = preferred or hits
    if len(hits) == 1:
        return hits[0]
    if not hits:
        sys.exit(f"No device matches '{query}'. Run 'sudo-home.py devices' to see the names.")
    names = ", ".join(f"{d.get('label') or 'unnamed'} ({d.get('ip')})" for d in hits[:8])
    sys.exit(f"'{query}' matches more than one device: {names}. Ask which one.")


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd, args = argv[0], argv[1:]
    home = api("/api/home")
    devices = home.get("devices") or []
    if cmd == "devices":
        for d in devices:
            state = "online" if d["online"] else "offline"
            who = f", {d['person']}'s" if d.get("person") else ""
            print(f"{d.get('label') or 'unnamed ' + d['kind']} [{d['kind']}{who}] {d['ip']} {state}")
        return
    if cmd == "who":
        if not home["settings"].get("presence"):
            print("Tracking who's home is off. The owner can turn it on in Apps -> Home.")
            return
        if not home["people"]:
            print("Nobody's phone is marked yet (Apps -> Home -> Edit a phone -> Whose is it?).")
        for p in home["people"]:
            print(f"{p['name']}: {'home' if p['home'] else 'away'}")
        return
    filt = api("/api/home/filter")
    if filt.get("status", {}).get("state") != "ready":
        sys.exit("Parental controls aren't set up yet. The owner can set them up in Apps -> Home.")
    kids = {k["mac"]: k for k in filt.get("devices") or []}
    if cmd == "kids":
        if not kids:
            print("No device has the Kids profile yet.")
        for mac, k in kids.items():
            d = next((x for x in devices if x["mac"] == mac), {})
            state = "PAUSED" if k["paused"] else "bedtime now" if k["in_bedtime"] else "active"
            bed = k["bedtime"]
            print(f"{d.get('label') or mac} ({k['profile']}): {state}; blocked apps: {', '.join(k['apps']) or 'none'}; "
                  f"bedtime: {bed['start']}-{bed['end'] if bed.get('on') else 'off'}; "
                  f"{'connected' if k['using_filter'] else 'NOT pointed at Sudo yet'}")
        return
    if not args:
        sys.exit("Say which device.")
    d = find(args[0], devices, prefer=set(kids))
    label = d.get("label") or d["ip"]

    def set_(changes, msg):
        api("/api/home/filter/device", {"mac": d["mac"], **changes})
        print(msg)

    needs_kids = cmd in ("pause", "resume", "block", "unblock", "bedtime")
    if needs_kids and (kids.get(d["mac"]) or {}).get("profile") != "kids":
        sys.exit(f"{label} doesn't have the Kids profile. The owner can turn it on in Apps -> Home -> Edit.")
    if cmd == "pause":
        mins = int(args[1]) if len(args) > 1 and args[1].isdigit() else -1
        set_({"pause_minutes": mins}, f"Paused {label} " + (f"for {mins} minutes." if mins > 0 else "until resumed."))
    elif cmd == "resume":
        set_({"pause_minutes": 0}, f"{label} is back online.")
    elif cmd in ("block", "unblock"):
        if len(args) < 2:
            sys.exit("Say which app.")
        names = {a["id"]: a["name"] for a in filt["apps"]}
        want = args[1].lower().replace(" ", "")
        app = next((i for i, n in names.items() if want in (i, n.lower().replace(" ", ""))), None)
        if not app:
            sys.exit(f"Unknown app '{args[1]}'. Known: {', '.join(names.values())}.")
        apps = set(kids[d["mac"]]["apps"])
        apps = apps | {app} if cmd == "block" else apps - {app}
        set_({"apps": sorted(apps)}, f"{'Blocked' if cmd == 'block' else 'Unblocked'} {names[app]} on {label}.")
    elif cmd == "bedtime":
        if len(args) > 1 and args[1].lower() == "off":
            set_({"bedtime": {"on": False}}, f"Bedtime off for {label}.")
        elif len(args) >= 3:
            set_({"bedtime": {"on": True, "start": args[1], "end": args[2]}}, f"Bedtime for {label}: {args[1]} to {args[2]}.")
        else:
            sys.exit("Use: bedtime <device> <HH:MM> <HH:MM>  or  bedtime <device> off")
    elif cmd == "time":
        if d["mac"] not in kids:
            sys.exit(f"{label} isn't being tracked. The owner can choose 'Track my time' for it in Apps -> Home -> Edit.")
        days = int(args[1]) if len(args) > 1 and args[1].isdigit() else 7
        t = api(f"/api/home/screentime?mac={urllib.parse.quote(d['mac'])}&days={days}")
        if not t["services"]:
            print(f"Nothing counted yet for {label}.")
        for s in t["services"]:
            print(f"{s['name']}: {fmt(s['week'])} in {days} days, {fmt(s['today'])} today")
        print("(Estimates from when the device talked to each app.)")
    else:
        sys.exit(f"Unknown command '{cmd}'. Run 'sudo-home.py help'.")


if __name__ == "__main__":
    main(sys.argv[1:])
