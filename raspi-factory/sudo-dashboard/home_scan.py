"""Home: the devices on the home network, and who is home.

Runs as a background thread inside the dashboard. Everything stays on the Pi.

How devices are found -- listening, not probing:
  - every few minutes one tiny UDP packet goes to each address on the local
    network (port 9, "discard"). Nothing listens there; the point is that the
    kernel has to look the address up first, so every device that is on the
    network answers that lookup and lands in the neighbour table with its MAC.
  - names come from what devices announce themselves: mDNS/Bonjour (phones,
    Macs, printers, Chromecasts, speakers), SSDP/UPnP (TVs, consoles), and
    the router's own DNS names.
  - the maker comes from the first half of the MAC address, looked up in the
    Wireshark manufacturer list (downloaded once and kept on the Pi).

Who is home: the owner marks a device as someone's phone. That device is then
checked every minute; "home" means it answered within the last few minutes.
Phones sleep and drop off Wi-Fi for short spells, so leaving needs a quiet
spell (AWAY_AFTER), not a single missed check.
"""
import ipaddress
import json
import os
import re
import socket
import struct
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

HOME_DIR = "/opt/sudo/home"
DEVICES = os.path.join(HOME_DIR, "devices.json")
SETTINGS = os.path.join(HOME_DIR, "settings.json")
EVENTS = os.path.join(HOME_DIR, "events.json")
NOW = os.path.join(HOME_DIR, "now.json")          # short summary the agent reads
MANUF = os.path.join(HOME_DIR, "manuf.txt")
MANUF_URL = "https://www.wireshark.org/download/automated/data/manuf"

QUICK_EVERY = 60          # presence checks
FULL_EVERY = 300          # sweep + names
AWAY_AFTER = 600          # quiet this long = left
MAX_HOSTS = 1024          # never sweep more than this many addresses
DEFAULT_SETTINGS = {"presence": False, "alert_new": False, "alert_presence": False}

_lock = threading.RLock()
_state = {"scanning": False, "last_full": 0, "last_quick": 0, "network": None, "error": ""}
_wake = threading.Event()
_notify = None            # set by the dashboard: callable(text)
_after_scan = None        # set by the dashboard: callable(devices), e.g. the kids filter


# ── storage ──────────────────────────────────────────────────────────────────

def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(HOME_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def settings():
    s = dict(DEFAULT_SETTINGS)
    s.update(_read(SETTINGS, {}))
    return s


def save_settings(changes):
    with _lock:
        s = settings()
        for k in DEFAULT_SETTINGS:
            if k in changes:
                s[k] = bool(changes[k])
        _write(SETTINGS, s)
        return s


def _event(kind, **fields):
    events = _read(EVENTS, [])
    events.append({"kind": kind, "at": int(time.time() * 1000), **fields})
    _write(EVENTS, events[-500:])


# ── the local network ────────────────────────────────────────────────────────

def _run(cmd, timeout=5):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def network():
    """(interface, own ip, ipaddress network, gateway) or None."""
    route = _run(["ip", "-4", "-o", "route", "show", "default"])
    m = re.search(r"via (\S+) dev (\S+)", route)
    if not m:
        return None
    gateway, dev = m.group(1), m.group(2)
    addr = _run(["ip", "-4", "-o", "addr", "show", "dev", dev])
    a = re.search(r"inet (\d+\.\d+\.\d+\.\d+/\d+)", addr)
    if not a:
        return None
    iface = ipaddress.ip_interface(a.group(1))
    net = iface.network
    if net.num_addresses > MAX_HOSTS + 2:
        net = ipaddress.ip_network(f"{iface.ip}/22", strict=False)
        if net.num_addresses > MAX_HOSTS + 2:
            net = ipaddress.ip_network(f"{iface.ip}/24", strict=False)
    return dev, str(iface.ip), net, gateway


def _nudge(ips):
    """One small UDP packet per address, so the kernel resolves each one."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    try:
        for i, ip in enumerate(ips):
            try:
                s.sendto(b"\0", (ip, 9))
            except OSError:
                pass
            if i % 64 == 63:
                time.sleep(0.02)
    finally:
        s.close()


def neighbours(dev):
    """{mac: (ip, state)} from the kernel's neighbour table."""
    out = _run(["ip", "-4", "neigh", "show", "dev", dev])
    found = {}
    for line in out.splitlines():
        m = re.match(r"(\d+\.\d+\.\d+\.\d+) lladdr ([0-9a-f:]{17}) (?:router )?(\w+)", line.strip())
        if m:
            found[m.group(2)] = (m.group(1), m.group(3))
    return found


LIVE_STATES = {"REACHABLE", "DELAY", "PROBE"}


# ── names ────────────────────────────────────────────────────────────────────

_manuf = None
# "Amazon Technologies Inc." -> "Amazon Technologies", "Xiaomi Communications Co Ltd" -> "Xiaomi Communications"
_CORP = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporate|corporation|co|company|ltd|limited|llc|gmbh|"
                   r"s\.?a|ag|b\.?v|plc|pte|pty|systems|trading|technology co|electronics co)\b\.?.*$", re.I)
_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


def _load_manuf():
    global _manuf
    if _manuf is not None:
        return _manuf
    try:
        fresh = os.path.getmtime(MANUF) > time.time() - 60 * 86400
    except OSError:
        fresh = False
    if not fresh:
        try:
            with urllib.request.urlopen(MANUF_URL, timeout=30) as r:
                data = r.read()
            if len(data) > 100_000:
                os.makedirs(HOME_DIR, exist_ok=True)
                with open(MANUF + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(MANUF + ".tmp", MANUF)
        except OSError:
            pass
    table = {}
    try:
        with open(MANUF, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip() or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.rstrip("\n").split("\t")]
                if len(parts) >= 2 and len(parts[0]) == 8:      # 24-bit prefixes only
                    full = parts[2] if len(parts) > 2 and parts[2] else parts[1]
                    table[parts[0].lower()] = _PAREN.sub("", _CORP.sub("", full)).strip(" ,.") or parts[1]
    except OSError:
        pass
    _manuf = table
    return table


def vendor(mac):
    first = int(mac[:2], 16)
    if first & 0x02:
        return "", True          # locally administered: a phone's private Wi-Fi address
    return _load_manuf().get(mac[:8].lower(), ""), False


def reverse_dns(ips, wait=3.0):
    """{ip: name} from the router's DNS, all at once, giving up after `wait`.
    Threads with a deadline rather than socket.setdefaulttimeout, which is
    process-wide and would reach the dashboard's own connections."""
    found = {}

    def one(ip):
        try:
            name = socket.gethostbyaddr(ip)[0]
        except OSError:
            return
        if name != ip and not re.fullmatch(r"[\d.-]+(\.in-addr\.arpa)?", name):
            found[ip] = name

    threads = [threading.Thread(target=one, args=(ip,), daemon=True) for ip in ips[:256]]
    for t in threads:
        t.start()
    end = time.time() + wait
    for t in threads:
        t.join(max(0, end - time.time()))
    return dict(found)


# mDNS: a minimal DNS reader -- enough for PTR, SRV, TXT and A records.
MDNS_SERVICES = [
    "_services._dns-sd._udp.local", "_device-info._tcp.local", "_googlecast._tcp.local",
    "_airplay._tcp.local", "_raop._tcp.local", "_companion-link._tcp.local", "_hap._tcp.local",
    "_homekit._tcp.local", "_spotify-connect._tcp.local", "_sonos._tcp.local", "_ipp._tcp.local",
    "_printer._tcp.local", "_smb._tcp.local", "_workstation._tcp.local", "_amzn-wplay._tcp.local",
    "_hue._tcp.local", "_matter._tcp.local", "_androidtvremote2._tcp.local", "_ssh._tcp.local",
]


def _qname(name):
    out = b""
    for label in name.strip(".").split("."):
        b = label.encode()
        out += bytes([len(b)]) + b
    return out + b"\0"


def _read_name(data, pos, depth=0):
    labels = []
    while depth < 20:
        if pos >= len(data):
            break
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = struct.unpack("!H", data[pos:pos + 2])[0] & 0x3FFF
            sub, _ = _read_name(data, ptr, depth + 1)
            labels.append(sub)
            pos += 2
            break
        labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(x for x in labels if x), pos


def _parse_mdns(data):
    """[(type, name, value)] for every answer/additional record."""
    try:
        _, flags, qd, an, ns, ar = struct.unpack("!6H", data[:12])
    except struct.error:
        return []
    pos = 12
    for _ in range(qd):
        _, pos = _read_name(data, pos)
        pos += 4
    records = []
    for _ in range(an + ns + ar):
        name, pos = _read_name(data, pos)
        if pos + 10 > len(data):
            break
        rtype, _, _, rdlen = struct.unpack("!HHIH", data[pos:pos + 10])
        pos += 10
        rdata = data[pos:pos + rdlen]
        if rtype == 1 and rdlen == 4:
            records.append(("A", name, socket.inet_ntoa(rdata)))
        elif rtype == 12:
            records.append(("PTR", name, _read_name(data, pos)[0]))
        elif rtype == 33:
            records.append(("SRV", name, _read_name(data, pos + 6)[0]))
        elif rtype == 16:
            txt, i = [], 0
            while i < len(rdata):
                ln = rdata[i]
                txt.append(rdata[i + 1:i + 1 + ln].decode("utf-8", "replace"))
                i += 1 + ln
            records.append(("TXT", name, txt))
        pos += rdlen
    return records


def mdns_scan(wait=3.0):
    """{ip: {"host": ..., "names": set, "services": set, "model": ...}}"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    s.settimeout(0.3)
    try:
        s.bind(("", 0))
        for svc in MDNS_SERVICES:
            # class IN with the "unicast response please" bit, so answers come
            # straight back to this socket instead of the multicast group.
            q = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + _qname(svc) + struct.pack("!HH", 12, 0x8001)
            try:
                s.sendto(q, ("224.0.0.251", 5353))
            except OSError:
                pass
        found, end = {}, time.time() + wait
        while time.time() < end:
            try:
                data, (src, _) = s.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            info = found.setdefault(src, {"host": "", "names": set(), "services": set(), "model": ""})
            for rtype, name, value in _parse_mdns(data):
                if rtype == "A" and value == src and name.endswith(".local"):
                    info["host"] = name[:-6]
                elif rtype == "PTR" and name.startswith("_") and "._" in name:
                    svc = ".".join(name.split(".")[-3:-1]) if name.count(".") >= 2 else name
                    info["services"].add(svc)
                    inst = value.split("._")[0]
                    if inst and not inst.startswith("_"):
                        info["names"].add(inst)
                elif rtype == "TXT":
                    for kv in value:
                        k, _, v = kv.partition("=")
                        if k in ("fn",) and v:
                            info["names"].add(v)
                        if k in ("md", "model", "mdl", "ty") and v and not info["model"]:
                            info["model"] = v
        return found
    finally:
        s.close()


def ssdp_scan(wait=3.0):
    """{ip: {"name":..., "maker":..., "model":...}} from UPnP devices."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.settimeout(0.3)
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
           "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()
    locations = {}
    try:
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + wait
        while time.time() < end:
            try:
                data, (src, _) = s.recvfrom(4096)
            except socket.timeout:
                continue
            m = re.search(rb"(?im)^location:\s*(\S+)", data)
            if m and src not in locations:
                locations[src] = m.group(1).decode("ascii", "ignore")
    except OSError:
        pass
    finally:
        s.close()
    found = {}
    for ip, url in locations.items():
        # Only ever follow a description URL on the device's own address.
        if f"//{ip}:" not in url and f"//{ip}/" not in url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                root = ET.fromstring(r.read(200_000))
        except Exception:
            continue

        def first(tag):
            el = root.find(f".//{{*}}{tag}")
            return (el.text or "").strip() if el is not None and el.text else ""
        found[ip] = {"name": first("friendlyName"), "maker": first("manufacturer"), "model": first("modelName")}
    return found


# ── what a device probably is ────────────────────────────────────────────────

KIND_RULES = [
    ("pi", r"raspberry"),
    ("phone", r"iphone|android|galaxy|pixel|oneplus|redmi|xiaomi.*phone|huawei.*phone|motorola"),
    ("tablet", r"ipad|tablet|kindle|fire-?hd"),
    ("tv", r"\btv\b|tv$|appletv|apple-tv|roku|fire-?tv|bravia|chromecast|googlecast|_googlecast|androidtv|shield|tizen|webos|vizio"),
    ("speaker", r"sonos|echo|homepod|_raop|_spotify|nest-?audio|nest-?mini|google-?home|bose"),
    ("printer", r"printer|_ipp|_printer|epson|canon|brother|laserjet|officejet|hp-?[a-z]*print"),
    ("computer", r"macbook|imac|mac-?mini|laptop|desktop|windows|_workstation|_smb|thinkpad|surface"),
    ("plug", r"plug|kasa|tapo|shelly|sonoff|wemo|tuya|smart-?life|meross|espressif|_hap|_homekit|_matter|_hue|hue|lifx|wiz|govee"),
    ("console", r"playstation|xbox|nintendo|switch"),
    ("camera", r"camera|cam\b|ring|arlo|wyze|blink|eufy|nest-?cam|reolink|hikvision"),
]
# Makers whose network chips nearly always mean a computer.
VENDOR_KINDS = [("computer", r"^intel\b|^dell\b|^lenovo|^hewlett|^asustek|^micro-star|^liteon")]


def guess_kind(d):
    says = " ".join(str(x or "") for x in (
        d.get("vendor"), d.get("hostname"), d.get("mdns_host"), d.get("model"),
        " ".join(d.get("names") or []), d.get("ssdp_name"), d.get("ssdp_model"), d.get("ssdp_maker"))).lower()
    offers = " ".join(d.get("services") or []).lower()
    if "amazon" in says:
        return "speaker" if re.search(r"echo|alexa", says) or "_amzn" in offers else "plug"
    for text in (says, offers):
        for kind, rx in KIND_RULES:
            if re.search(rx, text):
                return kind
    for kind, rx in VENDOR_KINDS:
        if re.search(rx, (d.get("vendor") or "").lower()):
            return kind
    if d.get("private_mac"):
        return "phone"
    return "other"


def _clean_name(n):
    n = str(n or "").strip()
    n = re.sub(r"^[0-9A-Fa-f]{12}@", "", n)                     # AirPlay: "FA2D7E8B52A1@Sam's MacBook Pro"
    n = re.sub(r"\s*\[[0-9a-fA-F:]{17}\]$", "", n)             # Linux: "kitchenpi [aa:bb:...]"
    n = re.sub(r"\.(lan|local|home|localdomain|domain|router)$", "", n, flags=re.I)
    n = re.sub(r"^\[([^\]]+)\]\s*", r"\1 ", n).strip()            # "[LG] 2" -> "LG 2"
    if re.fullmatch(r"[A-Za-z0-9]+(-[A-Za-z0-9]+)+", n):
        n = n.replace("-", " ")                                   # "Rands-MacBook-Pro-3"
    return n


def _good_name(n):
    if len(re.sub(r"[^A-Za-z\u00C0-\uFFFF]", "", n)) < 4:         # "LG 2", "2"
        return False
    if re.search(r"[0-9a-fA-F]{10,}", n.replace(" ", "")) or re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", n):
        return False                                              # serials and uuids
    return True


def display_name(d):
    if d.get("name"):
        return d["name"]
    for candidate in [d.get("ssdp_name")] + list(d.get("names") or []) + [d.get("mdns_host"), d.get("hostname")]:
        c = _clean_name(candidate)
        if c and _good_name(c):
            return c
    model = d.get("ssdp_model") or ""
    if model and _good_name(model):
        return model
    return d.get("vendor") or ""


# ── scanning ─────────────────────────────────────────────────────────────────

def _merge_names(devices, by_ip, mdns, ssdp):
    for ip, info in mdns.items():
        d = by_ip.get(ip)
        if not d:
            continue
        if info["host"]:
            d["mdns_host"] = info["host"]
        if info["names"]:
            d["names"] = sorted(set(d.get("names") or []) | info["names"])[:6]
        if info["services"]:
            d["services"] = sorted(set(d.get("services") or []) | info["services"])[:12]
        if info["model"]:
            d["model"] = info["model"]
    for ip, info in ssdp.items():
        d = by_ip.get(ip)
        if d:
            d["ssdp_name"] = info["name"] or d.get("ssdp_name", "")
            d["ssdp_maker"] = info["maker"] or d.get("ssdp_maker", "")
            d["ssdp_model"] = info["model"] or d.get("ssdp_model", "")


def scan(full=True):
    net = network()
    if not net:
        with _lock:
            _state["error"] = "Not on a network right now"
        return
    dev, own_ip, cidr, gateway = net
    with _lock:
        _state.update(scanning=True, error="", network={"cidr": str(cidr), "ip": own_ip, "gateway": gateway})
    try:
        devices = _read(DEVICES, {})
        s = settings()
        people_macs = [m for m, d in devices.items() if d.get("person")]
        now = time.time()
        if full:
            _nudge([str(h) for h in cidr.hosts() if str(h) != own_ip])
        elif s["presence"] and people_macs:
            _nudge([devices[m]["ip"] for m in people_macs if devices[m].get("ip")])
        time.sleep(3)
        table = neighbours(dev)
        baseline = bool(devices) or _read(SETTINGS, {}).get("baseline_done")
        new_macs = []
        for mac, (ip, st) in table.items():
            d = devices.get(mac)
            if d is None:
                if not full:
                    continue
                d = devices[mac] = {"mac": mac, "first_seen": int(now * 1000)}
                if baseline:
                    new_macs.append(mac)
            d["ip"] = ip
            if st in LIVE_STATES:
                d["last_seen"] = int(now * 1000)
            d["vendor"], d["private_mac"] = vendor(mac)
            if ip == gateway:
                d["role"] = "router"
            if ip == own_ip:
                d["role"] = "self"
        if full:
            by_ip = {d["ip"]: d for d in devices.values() if d.get("ip")}
            _merge_names(devices, by_ip, mdns_scan(), ssdp_scan())
            unnamed = [d["ip"] for d in devices.values()
                       if d.get("ip") and not d.get("hostname") and now * 1000 - d.get("last_seen", 0) < 600_000]
            for ip, name in reverse_dns(unnamed).items():
                if ip in by_ip:
                    by_ip[ip]["hostname"] = name
        # This Pi never shows up in its own neighbour table.
        selfmac = (_run(["cat", f"/sys/class/net/{dev}/address"]).strip() or "").lower()
        if selfmac:
            d = devices.setdefault(selfmac, {"mac": selfmac, "first_seen": int(now * 1000)})
            d.update(ip=own_ip, last_seen=int(now * 1000), role="self")
            d.setdefault("vendor", "Raspberry Pi")
            d.setdefault("private_mac", False)
        for d in devices.values():
            d["kind"] = "router" if d.get("role") == "router" else "pi" if d.get("role") == "self" else guess_kind(d)
        for mac in new_macs:
            d = devices[mac]
            _event("new_device", mac=mac, label=display_name(d) or d.get("vendor") or "Unknown device")
            if s["alert_new"] and _notify:
                label = display_name(d) or d.get("vendor") or "an unknown device"
                _notify(f"🏠 New device on your Wi-Fi: {label} ({d.get('ip')}). "
                        "If you don't recognise it, check it in Sudo → Apps → Home.")
        with _lock:
            # The owner may have renamed a device or marked someone's phone
            # while this scan ran; their choices on disk win over our copy.
            disk = _read(DEVICES, {})
            for mac, d in devices.items():
                for key in ("name", "person", "hidden"):
                    if key in disk.get(mac, {}):
                        d[key] = disk[mac][key]
                    else:
                        d.pop(key, None)
            if s["presence"]:
                _presence(devices, now)
            _write(DEVICES, devices)
        if not _read(SETTINGS, {}).get("baseline_done") and full:
            raw = _read(SETTINGS, {})
            raw["baseline_done"] = True
            _write(SETTINGS, raw)
        _write_now(devices)
        if _after_scan:
            try:
                _after_scan(devices)     # kids: follow IP changes, bedtime, pause expiry
            except Exception as exc:     # noqa: BLE001
                print(f"after-scan hook failed: {exc}")
        with _lock:
            _state["last_full" if full else "last_quick"] = int(now * 1000)
    finally:
        with _lock:
            _state["scanning"] = False


def _people(devices, now_ms):
    people = {}
    for d in devices.values():
        person = d.get("person")
        if not person:
            continue
        p = people.setdefault(person, {"name": person, "last_seen": 0, "devices": []})
        p["last_seen"] = max(p["last_seen"], d.get("last_seen", 0))
        p["devices"].append(d["mac"])
    for p in people.values():
        p["home"] = now_ms - p["last_seen"] < AWAY_AFTER * 1000
    return people


def _presence(devices, now):
    now_ms = int(now * 1000)
    status = _read(os.path.join(HOME_DIR, "presence.json"), {})
    s = settings()
    for name, p in _people(devices, now_ms).items():
        was = status.get(name, {}).get("home")
        if was is None:
            status[name] = {"home": p["home"], "since": now_ms}
            continue
        if p["home"] != was:
            status[name] = {"home": p["home"], "since": now_ms if p["home"] else p["last_seen"]}
            _event("arrived" if p["home"] else "left", person=name)
            if s["alert_presence"] and _notify:
                _notify(f"🏠 {name} {'just got home' if p['home'] else 'left home'}.")
    for name in list(status):
        if not any(d.get("person") == name for d in devices.values()):
            status.pop(name)
    _write(os.path.join(HOME_DIR, "presence.json"), status)


def _write_now(devices):
    now_ms = int(time.time() * 1000)
    status = _read(os.path.join(HOME_DIR, "presence.json"), {})
    online = [d for d in devices.values() if now_ms - d.get("last_seen", 0) < AWAY_AFTER * 1000]
    summary = {
        "updated": now_ms,
        "people": [{"name": n, "home": v.get("home"), "since": v.get("since")} for n, v in status.items()]
        if settings()["presence"] else [],
        "devices_online": len(online),
        "devices": [{"name": display_name(d) or d.get("vendor") or "Unknown", "kind": d.get("kind"),
                     "ip": d.get("ip"), "online": d in online} for d in devices.values()][:200],
    }
    _write(NOW, summary)


# ── what the dashboard calls ─────────────────────────────────────────────────

def snapshot(viewer=""):
    """viewer: the address of the device asking, when it is on the home
    network -- so the page can say "you're using this"."""
    devices = _read(DEVICES, {})
    now_ms = int(time.time() * 1000)
    s = settings()
    status = _read(os.path.join(HOME_DIR, "presence.json"), {})
    out = []
    for d in devices.values():
        if d.get("hidden"):
            continue
        out.append({
            "mac": d["mac"], "ip": d.get("ip", ""), "name": d.get("name", ""),
            "label": display_name(d) or "", "vendor": d.get("vendor", ""),
            "private_mac": bool(d.get("private_mac")), "kind": d.get("kind", "other"),
            "role": d.get("role", ""), "person": d.get("person", ""),
            "online": now_ms - d.get("last_seen", 0) < AWAY_AFTER * 1000,
            "last_seen": d.get("last_seen", 0), "first_seen": d.get("first_seen", 0),
            "model": d.get("model") or d.get("ssdp_model") or "",
            "viewer": bool(viewer) and d.get("ip") == viewer,
        })
    out.sort(key=lambda d: (not d["online"], d["role"] != "self", d["kind"], (d["label"] or "~").lower()))
    people = []
    if s["presence"]:
        for name, p in _people(devices, now_ms).items():
            people.append({"name": name, "home": p["home"], "last_seen": p["last_seen"],
                           "since": status.get(name, {}).get("since", 0)})
        people.sort(key=lambda p: (not p["home"], p["name"].lower()))
    with _lock:
        st = dict(_state)
    return {"settings": {k: s[k] for k in DEFAULT_SETTINGS}, "devices": out, "people": people,
            "events": list(reversed(_read(EVENTS, [])[-60:])), "scanning": st["scanning"],
            "last_scan": st["last_full"], "network": st["network"], "error": st["error"]}


def update_device(mac, changes):
    with _lock:
        devices = _read(DEVICES, {})
        d = devices.get(mac)
        if not d:
            return False
        for key in ("name", "person"):
            if key in changes:
                v = str(changes[key] or "").strip()[:48]
                if v:
                    d[key] = v
                else:
                    d.pop(key, None)
        if "hidden" in changes:
            d["hidden"] = bool(changes["hidden"])
        _write(DEVICES, devices)
        _write_now(devices)
        return True


def scan_now():
    _wake.set()


def _loop():
    next_full = 0
    while True:
        try:
            now = time.time()
            full = now >= next_full
            scan(full=full)
            if full:
                next_full = now + FULL_EVERY
        except Exception as exc:          # a scan must never take the dashboard down
            with _lock:
                _state["error"] = f"scan failed: {exc}"
                _state["scanning"] = False
        if _wake.wait(QUICK_EVERY):
            _wake.clear()
            next_full = 0


def devices_raw():
    return _read(DEVICES, {})


def own_ip():
    with _lock:
        return (_state.get("network") or {}).get("ip", "")


def start(notify=None, after_scan=None):
    global _notify, _after_scan
    _notify = notify
    _after_scan = after_scan
    t = threading.Thread(target=_loop, name="home-scan", daemon=True)
    t.start()
