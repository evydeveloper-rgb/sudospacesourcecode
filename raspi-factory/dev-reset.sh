#!/bin/bash
# dev-reset.sh — re-run the new-user experience without losing your setup.
#
# Same user-facing result as factory-reset.sh: Wi-Fi wiped, hotspot back,
# onboarding cleared, so the next boot walks the full RaspiSetup -> captive
# portal -> welcome -> chat flow from zero.
#
# Unlike factory-reset.sh it does NOT touch anything you need to keep working
# on the device:
#   - SSH stays on, authorized_keys / password / sshd drop-ins untouched
#   - Claude Code and its credentials (in /home) untouched, as always
#   - your own OpenRouter / Composio keys and key-source choice kept
#   - ability toggles (exec, sub-agents, skills) kept
#   - device identity kept
#
# Use this for repeat testing. Use factory-reset.sh for a real customer unit.

exec > /boot/firmware/dev-reset.log 2>&1
set -x

echo "=== dev-reset started $(date) ==="

# 1) Clear cmdline FIRST — never leave a boot hook that can loop
CMDLINE="/boot/firmware/cmdline.txt"
if [ -f "$CMDLINE" ]; then
    sed -i.bak \
        -e 's/ systemd\.run=[^ ]*//g' \
        -e 's/ systemd\.run_success_action=[^ ]*//g' \
        -e 's/ systemd\.unit=[^ ]*//g' \
        "$CMDLINE" 2>/dev/null || \
    sed -i \
        -e 's/ systemd\.run=[^ ]*//g' \
        -e 's/ systemd\.run_success_action=[^ ]*//g' \
        -e 's/ systemd\.unit=[^ ]*//g' \
        "$CMDLINE"
fi

# 2) Keep a copy of on-device edits before install-factory overwrites /opt
RECOVER="/boot/firmware/recovered"
rm -rf "$RECOVER"
mkdir -p "$RECOVER"
for d in sudo-dashboard wifi-setup sudo-pi; do
    [ -d "/opt/$d" ] && cp -r "/opt/$d" "$RECOVER/" 2>/dev/null
done
[ -d /opt/sudo/agent-workspace ] && cp -r /opt/sudo/agent-workspace "$RECOVER/" 2>/dev/null
date > "$RECOVER/BACKED-UP-AT.txt"

# 3) Stop the services that own Wi-Fi / portal
systemctl stop sudo-dashboard.service sudo-remote-access.service \
    wifi-setup.service raspi-hotspot.service picoclaw-gateway.service sudo-openclaw-gateway.service \
    2>/dev/null || true
systemctl disable sudo-dashboard.service sudo-cloud-init.service \
    sudo-remote-access.service 2>/dev/null || true

# 4) Wipe Wi-Fi / portal state so the hotspot comes back
rm -f /var/lib/wifi-setup-configured /var/lib/wifi-setup-pending
rm -f /var/lib/sudo-cloud-registered /var/lib/sudo-remote-enabled
rm -f /etc/sudo/public_url

rfkill unblock wifi 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true

for con in $(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless" || $2=="wifi"{print $1}'); do
    echo "Deleting WiFi profile: $con"
    nmcli connection delete "$con" 2>/dev/null || true
done
for con in $(nmcli -t -f NAME,TYPE con show --active 2>/dev/null | awk -F: '$2=="wifi"{print $1}'); do
    nmcli connection down "$con" 2>/dev/null || true
    nmcli connection delete "$con" 2>/dev/null || true
done

# 5) Clear ONLY the onboarding answers. Everything else in config.json —
#    your API keys, key source, ability toggles, ssh state — is left alone,
#    which is the whole point of this script versus factory-reset.sh.
python3 - <<'PY' || true
import json, os
path = "/opt/sudo/config.json"
data = {}
if os.path.isfile(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
keys = [
    "profile_done", "user_name", "agent_name", "personality",
    "model", "remote_access", "pico_token", "theme",
    "routing_mode", "prefer_local",
]
# Keys are kept unless this flash explicitly asked to forget them. Testing
# the no-key path otherwise means editing config.json by hand on a device
# whose whole point is that you never have to.
if os.path.isfile("/boot/firmware/forget-keys"):
    keys += ["provider_keys", "user_openrouter_key", "openrouter_key_source",
             "openrouter_key", "composio_api_key", "composio_enabled"]
    print("forget-keys marker present — clearing saved API keys")
for key in keys:
    data.pop(key, None)
os.makedirs("/opt/sudo", exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
os.chmod(path, 0o600)
print("kept:", sorted(data.keys()))
PY

# Agent identity/memory is regenerated from templates on next configure.
rm -rf /opt/sudo/agent-workspace
mkdir -p /opt/sudo/agent-workspace
rm -f /opt/sudo/picoclaw/config.json
rm -rf /opt/sudo/openclaw
rm -f /opt/sudo/dashboard-chat.json
rm -f /boot/firmware/forget-keys

# NOTE: no SSH teardown here on purpose. factory-reset.sh disables sshd and
# removes authorized_keys; this script leaves your way in exactly as it was.

# 6) Reinstall the bundle (enables the hotspot, since the marker is gone)
bash /boot/firmware/install-factory.sh

REGDOM="$(sed -n 's/.*cfg80211\.ieee80211_regdom=\([A-Z][A-Z]\).*/\1/p' /boot/firmware/cmdline.txt 2>/dev/null)"
raspi-config nonint do_wifi_country "${REGDOM:-US}" 2>/dev/null || true
systemctl restart NetworkManager
sleep 8

systemctl enable raspi-hotspot.service wifi-setup.service
systemctl disable sudo-dashboard.service 2>/dev/null || true
systemctl restart raspi-hotspot.service
sleep 6
systemctl restart wifi-setup.service

ip addr show wlan0 || true
nmcli -t -f NAME,DEVICE,TYPE,STATE connection show --active || true

# 7) Remove the one-shot boot script (cmdline already cleaned in step 1)
rm -f /boot/firmware/dev-reset.sh

echo "=== dev-reset complete — rebooting $(date) ==="
echo "Hotspot: RaspiSetup (open, no password) → http://192.168.4.1/"
reboot
