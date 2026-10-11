#!/bin/bash
# install-adguard.sh — the filter behind Home's kids profiles and screen time.
#
# AdGuard Home (open source, GPL-3.0) answers DNS for the devices pointed at
# this Pi: it can force safe search, block adult sites and chosen apps per
# device, and keeps a log of which services each device talked to, which is
# what the screen-time estimate is counted from. Sudo drives it through its
# API; its own web screen listens on loopback only.
#
# Nothing changes for the rest of the network: only a device whose DNS is set
# to this Pi (or a router told to hand this Pi out) goes through the filter.
#
# Run from the dashboard via systemd-run. Progress goes to a status file.
set -uo pipefail

VERSION="v0.107.79"
ARCH_FILE="AdGuardHome_linux_arm64.tar.gz"
BASE="https://github.com/AdguardTeam/AdGuardHome/releases/download/${VERSION}"
BIN_DIR=/opt/adguard
WORK_DIR=/opt/sudo/adguard
CRED=/etc/sudo/adguard.json
STATUS=/var/lib/sudo-adguard-status.json
UNIT=/etc/systemd/system/sudo-adguard.service
WEB_PORT=3010
LOG=/var/log/sudo-adguard.log
exec >> "$LOG" 2>&1
echo "=== install-adguard $VERSION $(date) ==="

status() {
  python3 -c 'import json,sys,time
json.dump({"state": sys.argv[1], "message": sys.argv[2], "updated": int(time.time()*1000)}, open(sys.argv[3], "w"))' "$1" "$2" "$STATUS"
}
fail() { status error "$1"; echo "FAILED: $1"; exit 1; }

case "$(uname -m)" in aarch64|arm64) ;; *) fail "This filter needs a 64-bit Raspberry Pi";; esac

if ss -lun | grep -qE '[:.]53\s' && ! systemctl is-active --quiet sudo-adguard; then
  fail "Something else on this Pi already answers DNS (port 53)"
fi

if [ ! -x "$BIN_DIR/AdGuardHome" ] || ! "$BIN_DIR/AdGuardHome" --version 2>/dev/null | grep -q "${VERSION#v}"; then
  status installing "Downloading the filter (about 11 MB)…"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  curl -fsSL --max-time 300 -o "$tmp/$ARCH_FILE" "$BASE/$ARCH_FILE" || fail "Could not download the filter -- check the internet connection"
  curl -fsSL --max-time 60 -o "$tmp/checksums.txt" "$BASE/checksums.txt" || fail "Could not download the filter's checksum"
  want=$(grep " $ARCH_FILE\$\| \./$ARCH_FILE\$\|  $ARCH_FILE\$" "$tmp/checksums.txt" | awk '{print $1}' | head -1)
  have=$(sha256sum "$tmp/$ARCH_FILE" | awk '{print $1}')
  [ -n "$want" ] && [ "$want" = "$have" ] || fail "The download did not match its checksum -- not installed"
  tar -xzf "$tmp/$ARCH_FILE" -C "$tmp" || fail "Could not unpack the filter"
  install -d -m 755 "$BIN_DIR"
  install -m 755 "$tmp/AdGuardHome/AdGuardHome" "$BIN_DIR/AdGuardHome"
  install -m 644 "$tmp/AdGuardHome/LICENSE.txt" "$BIN_DIR/LICENSE.txt" 2>/dev/null || true
  echo "installed $("$BIN_DIR/AdGuardHome" --version)"
fi

install -d -m 700 "$WORK_DIR"
cat > "$UNIT" <<EOF
[Unit]
Description=Sudo Home filter (AdGuard Home)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$BIN_DIR/AdGuardHome --no-check-update -w $WORK_DIR -c $WORK_DIR/AdGuardHome.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sudo-adguard.service

# First run: AdGuard waits in setup mode. Configure it through its own API:
# DNS on every interface, its web screen on loopback only, a random login.
status installing "Starting the filter…"
for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:3000/" && break; curl -s -o /dev/null "http://127.0.0.1:$WEB_PORT/" && break; sleep 1; done
if [ ! -f "$CRED" ]; then
  pass=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 28)
  python3 - "$CRED" "$pass" <<'PY'
import json, os, sys
json.dump({"user": "sudo", "password": sys.argv[2], "web": "http://127.0.0.1:3010"}, open(sys.argv[1], "w"))
os.chmod(sys.argv[1], 0o600)
PY
fi
pass=$(python3 -c 'import json;print(json.load(open("/etc/sudo/adguard.json"))["password"])')
if curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:3000/control/install/get_addresses" | grep -q 200; then
  body=$(python3 -c 'import json,sys;print(json.dumps({"web":{"ip":"127.0.0.1","port":3010},"dns":{"ip":"0.0.0.0","port":53},"username":"sudo","password":sys.argv[1]}))' "$pass")
  code=$(curl -s -o /tmp/ag-install.out -w '%{http_code}' -H 'Content-Type: application/json' -d "$body" http://127.0.0.1:3000/control/install/configure)
  [ "$code" = "200" ] || fail "The filter would not accept its first settings ($code)"
  systemctl restart sudo-adguard.service
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$WEB_PORT/" && break; sleep 1; done
fi

# Upstreams: whatever this Pi already uses (the network's own DNS), so no new
# company sees anyone's lookups. Keep a week of query log for screen time.
python3 - <<'PY' || fail "Could not apply the filter's settings"
import base64, json, re, urllib.request
cred = json.load(open("/etc/sudo/adguard.json"))
auth = "Basic " + base64.b64encode(f'{cred["user"]}:{cred["password"]}'.encode()).decode()
def call(path, body=None, method=None):
    req = urllib.request.Request(cred["web"] + "/control" + path, method=method or ("POST" if body is not None else "GET"),
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()
ups = []
for line in open("/etc/resolv.conf"):
    m = re.match(r"\s*nameserver\s+(\S+)", line)
    if m and not m.group(1).startswith("127.") and m.group(1) != "::1":
        ups.append(m.group(1) if ":" not in m.group(1) else f"[{m.group(1)}]")
ups = ups or ["9.9.9.9"]
call("/dns_config", {"upstream_dns": ups, "bootstrap_dns": [u.strip("[]") for u in ups], "upstream_mode": "load_balance"})
call("/querylog/config/update", {"enabled": True, "interval": 7 * 24 * 3600 * 1000, "anonymize_client_ip": False, "ignored": []}, "PUT")
print("upstreams:", ups)
PY
status ready ""
echo "=== install-adguard done $(date) ==="
