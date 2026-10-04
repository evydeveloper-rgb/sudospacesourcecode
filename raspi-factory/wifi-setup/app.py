#!/usr/bin/env python3
"""
WiFi Setup captive portal — port 80, Sudo branded.
Must respond in <1s or iPhone/Android dismiss the captive sheet.

Flow:
  1. User picks home Wi‑Fi and taps Connect
  2. Drop hotspot, join home Wi‑Fi, and reboot
  3. Dashboard opens directly at http://raspberrypi.local
"""

import http.server
import socket
import socketserver
import urllib.parse
import shutil
import subprocess
import json
import os
import threading
import time
import traceback
import logging

PORT = 80
TEMPLATE_DIR = "/opt/wifi-setup/templates"
ASSET_DIR = "/opt/wifi-setup/assets"
AP_IP = "192.168.4.1"
AP_NAME = "RaspiSetup"
MARKER = "/var/lib/wifi-setup-configured"
PENDING_FILE = "/var/lib/wifi-setup-pending"
CONFIG = "/opt/sudo/config.json"
LOG_FILE = "/var/log/wifi-setup.log"
def _dashboard_url():
    """Where the dashboard will live once Wi-Fi is up.

    Derived from the device's real hostname rather than hardcoded: the owner
    sets this in Raspberry Pi Imager, so assuming "raspberrypi" sends people
    to an address that does not resolve.
    """
    try:
        host = socket.gethostname().split(".")[0].strip()
    except Exception:
        host = ""
    return f"http://{host or 'raspberrypi'}.local"


def dashboard_url():
    """Resolved per request -- cloud-init can set the hostname after
    this service starts, and a cached value would be wrong."""
    return _dashboard_url()


DASHBOARD_URL = _dashboard_url()
WIFI_COUNTRY_FILE = "/etc/sudo/wifi_country"

CAPTIVE_PATHS = {
    "/generate_204",
    "/hotspot-detect.html",
    "/connecttest.txt",
    "/ncsi.txt",
    "/success.txt",
    "/redirect",
    "/canonical.html",
    "/library/test/success.html",
    "/kindle-wifi/wifiredirect.html",
}

FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>sudo — WiFi Setup</title>
<style>
body{font-family:"Plus Jakarta Sans",ui-sans-serif,system-ui,sans-serif;background:#f9f9f9;margin:0;padding:24px}
.card{max-width:420px;margin:40px auto;background:#ffffff;border:2px solid #1a1c1c;border-radius:20px;padding:24px}
h1{font-size:20px;color:#1a1c1c} p{color:#4a4452;font-size:14px}
a{color:#340075}
input,button{width:100%;padding:12px;margin:8px 0;border-radius:10px;border:1px solid #ccc;font-size:15px;box-sizing:border-box}
button{background:#340075;color:#fff;border:2px solid #1a1c1c;font-weight:600;cursor:pointer}
</style></head><body>
<div class="card">
<h1>sudo — Connect to Wi‑Fi</h1>
<p>Setup portal is loading. If this page stays, use manual entry:</p>
<form method="POST" action="/connect">
<input name="ssid" placeholder="Wi‑Fi name" required>
<input name="password" type="password" placeholder="Password">
<input type="hidden" name="country" value="US">
<button type="submit">Connect</button>
</form>
<p><a href="/">Retry full setup page</a></p>
</div></body></html>"""

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("wifi-setup")


BOOT_LOG = "/boot/firmware/wifi-setup.log"


def mirror_log():
    """Copy the tail of our log onto the FAT boot partition.

    This is the log that explains why a network would not join, and it was the
    one log not readable from another machine -- /var/log is on ext4, and a
    device that failed to get online cannot be reached over the network to ask.
    Called at the few moments worth capturing rather than on every line, since
    each call writes to the SD card.
    """
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as src:
            data = src.read()[-20000:]
        with open(BOOT_LOG, "w", encoding="utf-8") as dst:
            dst.write(data)
    except OSError:
        pass


def get_ap_ip():
    try:
        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "wlan0"],
            capture_output=True, text=True, timeout=2,
        )
        for token in result.stdout.split():
            if "/" in token and token[0].isdigit():
                return token.split("/")[0]
    except Exception:
        pass
    return AP_IP


def is_returning():
    """True when this device has been set up before, even though the portal is
    showing again.

    The marker is cleared by the recovery watchdog (device carried to a new
    place, or the router changed) and by a reset -- but a reset clears the
    onboarding answers too, whereas a move does not. So the answers the owner
    gave are the durable signal: present answers mean a returning device that
    merely lost its network, absent ones mean a genuine fresh box. The portal
    uses this only to pick which intro to show; it never gates the Wi-Fi form.
    """
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    return bool(cfg.get("profile_done") or cfg.get("user_name") or cfg.get("agent_name"))


def brand_css():
    path = os.path.join(TEMPLATE_DIR, "brand.css")
    try:
        with open(path, "r") as f:
            return f.read()
    except OSError:
        return ""


def render_template(name, **replacements):
    html = open(os.path.join(TEMPLATE_DIR, name), encoding="utf-8").read()
    html = html.replace("{{BRAND_CSS}}", brand_css())
    for key, value in replacements.items():
        html = html.replace("{{" + key + "}}", str(value))
    return html


def scan_wifi():
    """Scan WiFi — can be slow in AP mode; never call from page render."""
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, timeout=4,
        )
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
            capture_output=True, text=True, timeout=8,
        )
        networks = []
        seen = set()
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(":")
            if len(parts) >= 2:
                ssid = parts[0].strip()
                signal = parts[1].strip() if len(parts) > 1 else "?"
                security = parts[2].strip() if len(parts) > 2 else ""
                if ssid and ssid not in seen and ssid != AP_NAME:
                    seen.add(ssid)
                    networks.append({
                        "ssid": ssid,
                        "signal": signal,
                        "security": security,
                        # nmcli reports "WPA2 802.1X" for the kind of network
                        # a workplace or school runs, where a password alone
                        # is not enough. The portal asks for a username too
                        # when it sees this.
                        "enterprise": "802.1X" in security.upper(),
                    })
        networks.sort(
            key=lambda x: int(x["signal"]) if x["signal"].isdigit() else 0,
            reverse=True,
        )
        # The security string is what decides whether a username is asked for,
        # so it is worth having in the log when a join goes wrong.
        log.info("scan found %d: %s", len(networks), "; ".join(
            f"{n['ssid']}[{n['security'] or 'open'}]" for n in networks[:12]))
        mirror_log()
        return networks
    except Exception as exc:
        log.warning("scan_wifi failed: %s", exc)
        return []


def lan_ip():
    """The address the Pi just got on the home network.

    The .local name needs mDNS, which plenty of Android builds and some
    routers do not resolve, so the success page offers the raw IP too.
    Read after connecting; DHCP can take a moment, hence the retries.
    """
    for _ in range(6):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                if ":" not in line:
                    continue
                addr = line.split(":", 1)[1].strip().split("/")[0]
                # Skip the AP's own address — that goes away on reboot.
                if addr and addr != AP_IP:
                    return addr
        except Exception:
            pass
        time.sleep(1)
    return ""


def connect_enterprise(ssid, username, password):
    """Join a WPA2-Enterprise network (the kind that wants a username).

    PEAP with MSCHAPv2, which is what the overwhelming majority of school and
    office networks run and what a phone picks by default.

    The server certificate is not verified. That is what a phone does when you
    tap "don't validate", and without it NetworkManager refuses to connect at
    all on networks that publish no CA the device already trusts. It is a real
    tradeoff: an impostor access point using the same name could collect the
    credentials. Said plainly in the portal rather than hidden here.
    """
    subprocess.run(["nmcli", "connection", "delete", ssid],
                   capture_output=True, timeout=10)
    add = subprocess.run(
        ["nmcli", "connection", "add", "type", "wifi", "ifname", "wlan0",
         "con-name", ssid, "ssid", ssid],
        capture_output=True, text=True, timeout=15,
    )
    if add.returncode != 0:
        log.warning("enterprise profile add failed: %s", add.stderr or add.stdout)
        return False

    settings = subprocess.run(
        ["nmcli", "connection", "modify", ssid,
         "wifi-sec.key-mgmt", "wpa-eap",
         "802-1x.eap", "peap",
         "802-1x.phase2-auth", "mschapv2",
         "802-1x.identity", username,
         "802-1x.password", password,
         # Without this the password is stored but never used. NetworkManager
         # treats an 802.1X secret as agent-owned by default and asks a running
         # agent for it at activation; on a headless box there is none, so the
         # join dies with "Secrets were required, but not provided" even though
         # the password is right there in the profile. 0 = keep it in the
         # connection and use it directly. Cost two failed joins at a school.
         "802-1x.password-flags", "0",
         "connection.autoconnect", "yes",
         "connection.autoconnect-priority", "100"],
        capture_output=True, text=True, timeout=15,
    )
    if settings.returncode != 0:
        log.warning("enterprise settings failed: %s", settings.stderr or settings.stdout)
        return False

    # Enterprise association involves a full EAP exchange with a RADIUS server
    # somewhere on the far side, so it is routinely slower than a home network.
    up = subprocess.run(["nmcli", "--wait", "60", "connection", "up", ssid],
                        capture_output=True, text=True, timeout=75)
    if up.returncode != 0:
        log.warning("enterprise connect failed ssid=%s: %s", ssid, up.stderr or up.stdout)
        subprocess.run(["nmcli", "connection", "delete", ssid],
                       capture_output=True, timeout=10)
        return False
    return True


def connect_wifi(ssid, password, country="US", username=""):
    apply_wifi_country(country)

    subprocess.run(["nmcli", "connection", "delete", ssid], capture_output=True, timeout=10)
    # Dropping the AP is what disconnects the phone, so it happens here and not
    # a moment earlier: the success page has already been delivered by now.
    subprocess.run(["nmcli", "connection", "down", AP_NAME], capture_output=True, timeout=10)

    if username:
        return connect_enterprise(ssid, username, password)

    result = subprocess.run(
        # nmcli waits up to 90s by default; our own timeout was 30, so a slow
        # association was killed from this side and reported as a failure even
        # when it was about to succeed. Give nmcli an explicit budget and sit
        # just outside it.
        ["nmcli", "--wait", "50", "device", "wifi", "connect", ssid,
         "password", password],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        log.warning("wifi connect failed ssid=%s: %s", ssid, result.stderr or result.stdout)
        # Put the hotspot back, or a wrong password leaves the device with no
        # AP and no home network -- unreachable by any route until a reboot.
        restore_hotspot()
        return False

    subprocess.run(
        [
            "nmcli", "connection", "modify", ssid,
            "connection.autoconnect", "yes",
            "connection.autoconnect-priority", "100",
        ],
        capture_output=True, timeout=10,
    )
    return True


def apply_wifi_country(country):
    """Set the wireless regulatory domain on whatever board this is.

    raspi-config only exists on Raspberry Pi OS. Orange Pi OS and Armbian
    have no equivalent, so fall back to the generic mechanisms: `iw reg set`
    applies immediately, and the config files make it survive a reboot.
    Every step is best-effort — a wrong regdomain limits channels, it does
    not stop the device working, so nothing here should abort setup.
    """
    code = (country or "US").strip().upper()[:2]
    if not code.isalpha():
        return

    if shutil.which("raspi-config"):
        try:
            subprocess.run(["raspi-config", "nonint", "do_wifi_country", code],
                           capture_output=True, timeout=10)
            return
        except Exception as exc:
            log.warning("raspi-config regdomain failed: %s", exc)

    try:
        subprocess.run(["iw", "reg", "set", code], capture_output=True, timeout=10)
    except Exception as exc:
        log.warning("iw reg set failed: %s", exc)

    # Persist. Which of these the board reads depends on its wireless stack,
    # so write both rather than guessing.
    try:
        with open("/etc/default/crda", "w", encoding="utf-8") as f:
            f.write(f"REGDOMAIN={code}\n")
    except OSError:
        pass
    try:
        os.makedirs("/etc/modprobe.d", exist_ok=True)
        with open("/etc/modprobe.d/cfg80211.conf", "w", encoding="utf-8") as f:
            f.write(f"options cfg80211 ieee80211_regdom={code}\n")
    except OSError:
        pass


def save_wifi_country(country):
    try:
        os.makedirs(os.path.dirname(WIFI_COUNTRY_FILE), exist_ok=True)
        tmp = WIFI_COUNTRY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(country.strip().upper() + "\n")
        os.replace(tmp, WIFI_COUNTRY_FILE)
    except OSError as exc:
        log.warning("could not save wifi country: %s", exc)


def finalize_setup(ssid):
    with open(MARKER, "w") as f:
        f.write(ssid + "\n")
    try:
        os.remove(PENDING_FILE)
    except OSError:
        pass

    subprocess.run(["systemctl", "enable", "sudo-dashboard.service"], capture_output=True)
    subprocess.run(
        ["systemctl", "disable", "wifi-setup.service", "raspi-hotspot.service"],
        capture_output=True,
    )



def ensure_mdns():
    """Make <hostname>.local actually resolve, now that we have a network.

    The dashboard address is an mDNS name, which needs avahi-daemon. Nothing
    reliably installs it before this point:
      - cloud-init is told to, but apt needs a network and at first boot there
        is none -- raising the hotspot is what the whole flow exists to do
      - install-factory only enables the unit *if* it already exists, so a
        missing package is a silent no-op
    This runs after the join succeeds, which is the first moment the device can
    reach a package mirror. Bounded and best-effort: failing to install mDNS
    must never stop setup, it just means the owner uses the IP instead.
    """
    have = subprocess.run(
        ["systemctl", "list-unit-files", "avahi-daemon.service"],
        capture_output=True, text=True, timeout=15,
    )
    if "avahi-daemon.service" not in have.stdout:
        log.info("avahi-daemon missing — installing so .local resolves")
        try:
            subprocess.run(["apt-get", "update"], capture_output=True, timeout=180)
            subprocess.run(
                ["apt-get", "install", "-y", "--no-install-recommends", "avahi-daemon"],
                capture_output=True, timeout=300,
                env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
            )
        except Exception as exc:
            log.warning("avahi install failed: %s", exc)

    for cmd in (["systemctl", "enable", "avahi-daemon.service"],
                ["systemctl", "restart", "avahi-daemon.service"]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception:
            pass

    ok = subprocess.run(["systemctl", "is-active", "avahi-daemon"],
                        capture_output=True, text=True, timeout=10)
    log.info("avahi-daemon: %s | hostname: %s",
             ok.stdout.strip() or "unknown", socket.gethostname())


# The intro plays once. Tapping through to the network list is what ends it,
# so a reload, a captive re-probe, or coming back after a failed join all land
# on the list rather than replaying the scene. A reboot starts it over, which
# is right: a reflashed device really is meeting someone for the first time.
INTRO_DONE = {"seen": False}


# The success page cannot be updated from here once the AP drops -- the network
# it arrived over is gone -- so it polls /joined and fills itself in when the
# hotspot comes back.
JOIN = {"state": "joining", "ssid": "", "ip": "", "url": "", "error": ""}


def set_join(**fields):
    JOIN.update(fields)


def restore_hotspot():
    """Bring RaspiSetup back so the phone has a way in.

    Two callers, same reason: without an AP and without a home network the
    device is unreachable by any route until someone pulls the power. After a
    failed join it is the retry path; after a successful one it is the only
    channel left for telling the owner which address to use.
    """
    up = subprocess.run(["nmcli", "--wait", "30", "connection", "up", AP_NAME],
                        capture_output=True, text=True, timeout=45)
    if up.returncode != 0:
        log.warning("could not restore hotspot: %s", up.stderr or up.stdout)
        return False
    # `ipv4.method shared` on the profile is what makes NetworkManager run
    # dnsmasq, so bringing the profile up is enough for DHCP -- but a phone
    # that associates and gets no address reports it exactly like a failed
    # join, so confirm the address landed rather than trusting the exit code.
    addr = subprocess.run(["ip", "-4", "addr", "show", "wlan0"],
                          capture_output=True, text=True, timeout=10)
    if AP_IP not in addr.stdout:
        log.warning("hotspot profile up but %s not on wlan0", AP_IP)
        return False
    log.info("hotspot restored at %s", AP_IP)
    return True


def switch_to_wifi(ssid, password, country, username=""):
    """Join the home network after the browser has its page.

    Runs on a thread so the response is already on the wire. A short delay
    gives the phone time to finish loading the page and its stylesheet before
    the AP disappears underneath it.
    """
    def run():
        time.sleep(3)
        if not connect_wifi(ssid, password, country, username):
            # connect_wifi has already restored the AP. No marker is written,
            # so the portal stays up and the owner can try again.
            log.warning("could not join %s — hotspot restored for retry", ssid)
            set_join(state="failed", ssid=ssid,
                     error="sudo could not join that network. A mistyped "
                           "password is the usual reason.")
            mirror_log()
            return

        # Read the address before anything else: this is the number the owner
        # needs, and everything below can fail without making it wrong.
        ip = lan_ip()
        finalize_setup(ssid)
        save_wifi_country(country)
        # First moment there is a network, so this is where mDNS gets fixed.
        try:
            ensure_mdns()
        except Exception as exc:
            log.warning("ensure_mdns failed: %s", exc)
        # Cloud registration is opt-in: only fire it when this build has Sudo
        # credits enabled. Default builds are zero-cloud — the owner supplies
        # their own API key, so there is nothing to register.
        try:
            import os as _os
            billing_on = (
                _os.path.exists("/etc/sudo/billing-enabled")
                and open("/etc/sudo/billing-enabled").read().strip() == "billing=1"
            )
            if billing_on:
                subprocess.Popen(
                    ["/usr/bin/python3", "/opt/sudo-pi/trigger-cloud-init.py"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        except Exception as exc:
            log.warning("cloud-init trigger failed: %s", exc)

        log.info("joined %s at %s", ssid, ip or "(no address read)")
        set_join(state="ok", ssid=ssid, ip=ip,
                 url=("http://%s" % ip) if ip else "")
        mirror_log()

        # Straight to the reboot, and onto the home network. Raising the AP
        # again to hand over the numeric address cost far more than it bought:
        # it took the device *off* the network it had just joined, for ten
        # minutes -- which is exactly when the owner types the address in, so
        # the lookup failed because nothing was there. The success page was
        # already delivered before the AP dropped and carries the address with
        # a copy button; that was always the way this was meant to work.
        schedule_reboot(3)

    threading.Thread(target=run, daemon=True).start()


def schedule_reboot(delay=6):
    def do_reboot():
        time.sleep(delay)
        subprocess.run(["reboot"])
    threading.Thread(target=do_reboot, daemon=True).start()


class SetupHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        log.info("%s - %s", self.address_string(), format % args)

    def send_bytes(self, data, code=200, content_type="text/html; charset=utf-8"):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html, code=200):
        self.send_bytes(html, code=code)

    def redirect(self, url, code=302):
        self.send_response(code)
        self.send_header("Location", url)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def render_intro(self):
        """The first screen: it introduces itself, then hands over to setup.

        Fixed text end to end. The on-device model is nowhere near this -- the
        very first thing the product ever says cannot be allowed to be slow,
        generic, or missing.
        """
        self.send_html(render_template("hello.html"))

    def render_first_screen(self):
        """Which of the three openings to show.

        A device that has been set up before but lost its network (moved, or
        the router changed -- the exact case the recovery watchdog reopens the
        portal for) must NOT be greeted as if it were new: no "first time I've
        been switched on", no offer to build the agent again. It gets a short
        reconnect scene instead, whose only job is to get it back on Wi-Fi.
        A genuinely fresh box still gets the full first-boot hello.
        """
        if INTRO_DONE["seen"]:
            self.render_index()
        elif is_returning():
            self.render_reconnect()
        else:
            self.render_intro()

    def render_reconnect(self):
        """The returning-device opening: brief, no first-boot framing."""
        self.send_html(render_template("reconnect.html"))

    def render_index(self, error=""):
        html = render_template(
            "index.html",
            NETWORKS_JSON="[]",
            ERROR=error,
            ERROR_CLASS="show" if error else "",
            DASHBOARD_URL=DASHBOARD_URL,
        )
        self.send_html(html)

    def render_success(self, ssid):
        """The page the owner holds while the device moves networks.

        It ships knowing only the .local name -- the numeric address does not
        exist until the join succeeds -- and fills the rest in from /joined.
        """
        dashboard = dashboard_url()
        self.send_html(render_template(
            "success.html",
            SSID=ssid,
            AP_NAME=AP_NAME,
            DASHBOARD_URL=dashboard,
        ))

    def serve_asset(self, rel):
        """Serve the vendored CSS/fonts. No CDN exists on a hotspot."""
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            self.send_error(400)
            return
        full = os.path.realpath(os.path.join(ASSET_DIR, rel))
        root = os.path.realpath(ASSET_DIR)
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            self.send_error(404)
            return
        ctype = ("text/css; charset=utf-8" if full.endswith(".css")
                 else "font/woff2" if full.endswith(".woff2")
                 else "image/svg+xml" if full.endswith(".svg")
                 else "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # These never change between boots; let the phone keep them.
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def handle_captive_probe(self):
        """Answer the OS's connectivity check with the portal itself.

        A 302 here costs a second round trip before anything is shown. Both
        iOS and Android only need the probe to return something other than
        their expected success token to raise the captive sheet, so serving
        the page inline makes it appear on the first request.
        """
        try:
            self.render_first_screen()
        except Exception:
            self.send_html(FALLBACK_HTML)

    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            base = f"http://{get_ap_ip()}/"

            # Answered in every state, including after setup is marked done:
            # the hotspot is deliberately still up then, handing the address
            # over, and this is what the waiting page is asking.
            if path == "/joined":
                self.send_bytes(json.dumps(JOIN).encode("utf-8"),
                                content_type="application/json")
                return

            if path.startswith("/assets/"):
                self.serve_asset(path[len("/assets/"):])
                return

            if os.path.exists(MARKER):
                # Setup is finished. Show the address rather than the portal --
                # and never redirect "/" to itself, which is what this used to
                # do the moment the AP outlived the marker.
                self.render_success(JOIN.get("ssid", ""))
                return

            if path in ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
                self.send_bytes(b"", code=204, content_type="text/plain")
                return

            if path == "/generate_204" or path in CAPTIVE_PATHS:
                self.handle_captive_probe()
                return

            if path == "/":
                self.render_first_screen()
                return

            # Where the intro's button goes, and the way back to the list from
            # anywhere. Reaching it is what marks the intro as played.
            if path == "/setup":
                INTRO_DONE["seen"] = True
                self.render_index()
                return

            if path == "/scan":
                data = json.dumps(scan_wifi()).encode("utf-8")
                self.send_bytes(data, content_type="application/json")
                return

            if path == "/health":
                self.send_bytes(b'{"ok":true}', content_type="application/json")
                return

            # Anything else: serve the portal rather than redirecting to it.
            # Apple moves its connectivity-check URL between iOS releases, so an
            # unrecognised path is most likely a probe. A 302 costs an extra
            # round trip and does not reliably raise the captive sheet;
            # answering with the page itself does.
            try:
                self.render_index()
            except Exception:
                self.send_html(FALLBACK_HTML)
        except Exception as exc:
            log.error("GET %s failed: %s\n%s", self.path, exc, traceback.format_exc())
            self.send_html(FALLBACK_HTML, code=200)

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            base = f"http://{get_ap_ip()}/"

            if parsed.path != "/connect":
                self.redirect(base)
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            params = urllib.parse.parse_qs(body)

            ssid = params.get("ssid", [""])[0]
            password = params.get("password", [""])[0]
            country = params.get("country", ["US"])[0]
            username = params.get("username", [""])[0].strip()

            if not ssid:
                self.redirect(base)
                return

            log.info("connect request ssid=%s enterprise=%s", ssid, bool(username))

            # Answer FIRST. Joining the home network tears down the AP this
            # request arrived over, so anything sent afterwards never lands --
            # the browser just loses the connection mid-page.
            set_join(state="joining", ssid=ssid, ip="", url="", error="")
            self.render_success(ssid)
            try:
                self.wfile.flush()
            except Exception:
                pass
            switch_to_wifi(ssid, password, country, username)
            return
        except Exception as exc:
            log.error("POST %s failed: %s\n%s", self.path, exc, traceback.format_exc())
            self.send_html(FALLBACK_HTML, code=200)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    if os.path.exists(MARKER):
        print("WiFi already configured — wifi-setup should not be running")
        raise SystemExit(0)

    # NetworkManager may not have brought wlan0 up yet. Retry the bind
    # rather than pausing a fixed few seconds before even trying — the
    # portal should be listening the instant the interface exists.
    server = None
    for attempt in range(60):
        try:
            server = ThreadingHTTPServer(("0.0.0.0", PORT), SetupHandler)
            break
        except OSError as exc:
            log.info("bind attempt %s failed (%s); retrying", attempt + 1, exc)
            time.sleep(0.5)
    if server is None:
        log.error("could not bind port %s", PORT)
        raise SystemExit(1)
    log.info("WiFi Setup listening on port %s", PORT)
    print(f"WiFi Setup running on port {PORT}")
    server.serve_forever()
