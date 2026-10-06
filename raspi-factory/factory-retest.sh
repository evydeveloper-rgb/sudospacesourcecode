#!/bin/bash
# factory-retest.sh — soft reset for end-to-end testing
# KEEPS: WiFi + dashboard password + device identity
# CLEARS: onboarding/profile, remote link, cloud-registered marker

exec > /boot/firmware/retest.log 2>&1
set -x

echo "=== factory-retest started $(date) ==="

# CRITICAL: clear cmdline FIRST so a crash never boot-loops
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

if [ ! -f /var/lib/wifi-setup-configured ]; then
    echo "WiFi not configured — refusing soft retest (use factory-reset instead)"
    bash /boot/firmware/install-factory.sh
    systemctl enable raspi-hotspot.service wifi-setup.service 2>/dev/null || true
else
    rm -f /var/lib/sudo-cloud-registered
    rm -f /var/lib/sudo-remote-enabled
    rm -f /etc/sudo/public_url
    systemctl disable --now sudo-remote-access.service 2>/dev/null || true

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
    "model", "remote_access", "pico_token",
):
    data.pop(key, None)
os.makedirs("/opt/sudo", exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
os.chmod(path, 0o600)
print("config soft-reset")
PY

    rm -rf /opt/sudo/agent-workspace
    mkdir -p /opt/sudo/agent-workspace
    rm -f /opt/sudo/picoclaw/config.json
    rm -rf /opt/sudo/openclaw

    bash /boot/firmware/install-factory.sh

    touch /var/lib/wifi-setup-configured
    systemctl enable sudo-dashboard.service sudo-cloud-init.service 2>/dev/null || true
    systemctl disable wifi-setup.service raspi-hotspot.service 2>/dev/null || true

    # Bring WiFi back BEFORE cloud-init (needs internet)
    systemctl restart NetworkManager
    sleep 8
    nmcli radio wifi on 2>/dev/null || true
    for con in $(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless"{print $1}'); do
        [ "$con" = "RaspiSetup" ] && continue
        nmcli connection modify "$con" connection.autoconnect yes 2>/dev/null || true
        nmcli connection up "$con" 2>/dev/null && break || true
    done

    if [ -x /usr/local/bin/sudo-cloud-init.sh ]; then
        /usr/local/bin/sudo-cloud-init.sh || true
        /usr/local/bin/sudo-configure-picoclaw.sh || true
    fi

    systemctl restart sudo-dashboard.service
    systemctl try-restart picoclaw-gateway.service 2>/dev/null || true
fi

rm -f /boot/firmware/factory-retest.sh /boot/firmware/retest-portal.sh
echo "=== factory-retest complete — rebooting $(date) ==="
reboot
