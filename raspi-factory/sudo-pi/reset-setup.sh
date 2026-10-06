#!/bin/bash
# reset-setup.sh — full "new user" reset, triggered from the dashboard.
#
# Same user-facing result as factory-reset.sh: Wi-Fi wiped, RaspiSetup hotspot
# back, onboarding cleared, so the next boot walks the whole
# RaspiSetup -> captive portal -> welcome -> chat flow from zero, exactly like
# a device out of the box.
#
# The ONE deliberate difference from factory-reset.sh: this does NOT tear down
# terminal access. sshd stays on, the installed dev key / password are left in
# place, and Claude Code in /home is untouched. That is what makes it testable
# over the network -- a full factory-reset would cut the very path you are
# using to drive the test. Use factory-reset.sh (SD card, off-device) for a
# genuine customer unit.
#
# Everything else is a true factory reset:
#   - all Wi-Fi profiles deleted, configured marker cleared -> hotspot returns
#   - onboarding answers AND saved API keys cleared from config.json
#   - remote-access / cloud markers cleared
#   - agent workspace wiped (identity/memory regenerated on next configure)
#   - dashboard disabled, hotspot + setup portal enabled
# The device reboots at the end; that reboot is owned by the caller
# (server.py schedules it), so this script is safe to run and watch.

set -uo pipefail

LOG=/var/log/sudo-reset-setup.log
BOOTLOG=/boot/firmware/reset-setup.log
exec >> "$LOG" 2>&1
trap 'tail -c 8000 "$LOG" > "$BOOTLOG" 2>/dev/null || true' EXIT
echo "=== reset-setup started $(date) ==="

# 1) Clear any one-shot boot hook left on cmdline. Belt-and-braces: a reset
#    must never plant a hook that could loop us back here.
CMDLINE=/boot/firmware/cmdline.txt
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

# 2) Stop the services that own Wi-Fi / the portal / the bridge.
systemctl stop sudo-dashboard.service sudo-remote-access.service \
    wifi-setup.service raspi-hotspot.service picoclaw-gateway.service sudo-openclaw-gateway.service \
    sudo-whatsapp-bridge.service 2>/dev/null || true
systemctl disable sudo-dashboard.service sudo-cloud-init.service \
    sudo-remote-access.service 2>/dev/null || true

# 3) Wipe ALL Wi-Fi / portal state so the hotspot comes back.
rm -f /var/lib/wifi-setup-configured /var/lib/wifi-setup-pending
rm -f /var/lib/sudo-cloud-registered /var/lib/sudo-remote-enabled
rm -f /etc/sudo/public_url

rfkill unblock wifi 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true
for con in $(nmcli -t -f NAME,TYPE connection show 2>/dev/null \
             | awk -F: '$2=="802-11-wireless" || $2=="wifi"{print $1}'); do
    echo "Deleting WiFi profile: $con"
    nmcli connection delete "$con" 2>/dev/null || true
done
for con in $(nmcli -t -f NAME,TYPE con show --active 2>/dev/null \
             | awk -F: '$2=="wifi"{print $1}'); do
    nmcli connection down "$con" 2>/dev/null || true
    nmcli connection delete "$con" 2>/dev/null || true
done

# 4) Clear onboarding answers AND owner keys — a factory unit must not inherit
#    either. Mirrors factory-reset.sh's key list exactly.
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
for key in (
    "profile_done", "user_name", "agent_name", "personality",
    "model", "remote_access", "pico_token", "theme",
    "exec_enabled", "ssh_enabled", "ssh_auth",
    "openrouter_key_source", "user_openrouter_key", "composio_api_key",
    "composio_enabled", "routing_mode", "prefer_local",
):
    data.pop(key, None)
os.makedirs("/opt/sudo", exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
os.chmod(path, 0o600)
print("kept:", sorted(data.keys()))
PY

# 5) Wipe the agent workspace + rendered picoclaw config. Both are regenerated
#    from templates on the next successful configure.
rm -rf /opt/sudo/agent-workspace
mkdir -p /opt/sudo/agent-workspace
rm -f /opt/sudo/picoclaw/config.json
rm -rf /opt/sudo/openclaw
rm -f /opt/sudo/dashboard-chat.json

# NOTE: no SSH teardown here on purpose — see the header. factory-reset.sh
# owns that, and only for a device that is truly leaving.

# 6) Bring the first-run services back the way install-factory.sh arranges
#    them for an unconfigured device: hotspot + portal on, dashboard off.
raspi-config nonint do_wifi_country "${SUDO_WIFI_COUNTRY:-US}" 2>/dev/null || true
systemctl restart NetworkManager 2>/dev/null || true
sleep 6
systemctl enable raspi-hotspot.service wifi-setup.service 2>/dev/null || true
systemctl restart raspi-hotspot.service 2>/dev/null || true
sleep 5
systemctl restart wifi-setup.service 2>/dev/null || true

ip addr show wlan0 2>/dev/null | grep -q 192.168.4.1 \
    && echo "hotspot back at 192.168.4.1" \
    || echo "hotspot not up yet — will retry on reboot"

echo "=== reset-setup complete — rebooting $(date) ==="
echo "Hotspot: RaspiSetup (open) -> http://192.168.4.1/"
sync
