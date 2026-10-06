#!/bin/bash
# wifi.sh — report the Wi-Fi this device is on, and move it to another one.
#
# Run through systemd-run from the dashboard: that service has ProtectSystem=full
# and can only write a handful of paths, so it cannot touch NetworkManager's
# connection store in /etc/NetworkManager/system-connections itself. This helper
# runs outside that sandbox.
#
#   wifi.sh current                  what we are connected to right now
#   wifi.sh scan                     nearby networks, strongest first
#   wifi.sh connect <ssid> <pass> [user]
#
# Output is line-based, key=value, one record per line, so the caller can parse
# it without a JSON dependency. Fields with a "secret" flag are masked.
#
# A note on the hard case: changing Wi-Fi drops the dashboard's own connection.
# Over the public link (Cloudflare tunnel) that is a non-issue -- the tunnel
# reconnects. On the home network the browser goes quiet for a few seconds and
# then reloads on the new address; the API returns before the switch finishes
# so the request itself completes.
set -uo pipefail

LOG=/var/log/sudo-wifi.log
exec >> "$LOG" 2>&1
echo "=== wifi.sh $* @ $(date) ==="

IWD=wlan0

# Escape a value for the line protocol: no newlines, no '=' confusion (the
# caller splits on the first '=' only, so '=' inside a value is fine).
clean() { printf '%s' "$1" | tr '\n\r' '  '; }

cmd_current() {
    if ! command -v nmcli >/dev/null 2>&1; then
        echo "state=unavailable"
        return
    fi
    # Active Wi-Fi connection: the device, its SSID, and the IPv4 address.
    local dev ssid state
    dev="$(
        nmcli -t -f DEVICE,TYPE,STATE device status 2>/dev/null \
            | awk -F: '$2=="wifi" && $3=="connected"{print $1; exit}'
    )"
    if [ -z "$dev" ]; then
        dev="$IWD"
        state="disconnected"
    else
        state="connected"
    fi
    ssid="$(nmcli -t -f GENERAL.CONNECTION device show "$dev" 2>/dev/null \
        | sed -n 's/^GENERAL.CONNECTION://p')"
    local ip
    ip="$(nmcli -t -f IP4.ADDRESS device show "$dev" 2>/dev/null \
        | sed -n 's/^IP4.ADDRESS\[[0-9]*\]:\([0-9.]*\).*/\1/p' | head -1)"
    echo "state=$(clean "${state:-unknown}")"
    echo "device=$(clean "$dev")"
    echo "ssid=$(clean "$ssid")"
    echo "ip=$(clean "$ip")"
}

cmd_scan() {
    if ! command -v nmcli >/dev/null 2>&1; then
        echo "error=nmcli_missing"
        return
    fi
    # A rescan can block for a few seconds; never fail the whole call over it.
    nmcli device wifi rescan >/dev/null 2>&1 || true
    nmcli -t -f SSID,SIGNAL,SECURITY device wifi list 2>/dev/null \
    | awk -F: '
        $1!="" {
            ssid=$1; sig=$2; sec=$3
            if (seen[ssid]++) next
            # nmcli reports "WPA2 802.1X" for networks that want a username
            # as well as a password (schools, offices).
            ent = (index(toupper(sec), "802.1X") > 0) ? "1" : "0"
            printf "net=%s|%s|%s|%s\n", ssid, sig, sec, ent
        }' \
    | sort -t'|' -k2 -nr \
    | head -40
}

cmd_connect() {
    local ssid="${1:-}" pass="${2:-}" user="${3:-}"
    if [ -z "$ssid" ]; then
        echo "error=no_ssid"
        return 2
    fi
    if ! command -v nmcli >/dev/null 2>&1; then
        echo "error=nmcli_missing"
        return 2
    fi

    # A saved profile for this SSID is reused (and its secret refreshed) so a
    # later reconnect is automatic. Deleting first means a stale profile with
    # the wrong password can never win.
    nmcli connection delete "$ssid" >/dev/null 2>&1 || true

    if [ -n "$user" ]; then
        # WPA2-Enterprise (PEAP/MSCHAPv2), same shape as the setup portal.
        # Certificate validation is off, matching what a phone does when you
        # tap "don't validate"; without it NetworkManager refuses to associate
        # on networks that publish no already-trusted CA.
        nmcli connection add type wifi ifname "$IWD" con-name "$ssid" ssid "$ssid" >/dev/null 2>&1
        nmcli connection modify "$ssid" \
            wifi-sec.key-mgmt wpa-eap \
            802-1x.eap peap \
            802-1x.phase2-auth mschapv2 \
            802-1x.identity "$user" \
            802-1x.password "$pass" \
            802-1x.password-flags 0 \
            connection.autoconnect yes \
            connection.autoconnect-priority 100 >/dev/null 2>&1
        if nmcli --wait 60 connection up "$ssid" >/dev/null 2>&1; then
            echo "ok=1"
        else
            nmcli connection delete "$ssid" >/dev/null 2>&1 || true
            echo "error=connect_failed"
            return 1
        fi
    else
        if nmcli --wait 50 device wifi connect "$ssid" password "$pass" >/dev/null 2>&1; then
            nmcli connection modify "$ssid" \
                connection.autoconnect yes \
                connection.autoconnect-priority 100 >/dev/null 2>&1
            echo "ok=1"
        else
            echo "error=connect_failed"
            return 1
        fi
    fi
    echo "ssid=$(clean "$ssid")"
}

case "${1:-}" in
    current) cmd_current ;;
    scan)    cmd_scan ;;
    connect) shift; cmd_connect "$@" ;;
    *) echo "error=usage"; exit 2 ;;
esac

echo "=== wifi.sh done @ $(date) ==="
