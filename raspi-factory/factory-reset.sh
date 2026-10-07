#!/bin/bash
# factory-reset.sh — full factory WiFi reset (hotspot from zero)
# Wipes home WiFi + setup markers, restores RaspiSetup captive portal.

exec > /boot/firmware/factory-reset.log 2>&1
set -x

echo "=== factory-reset started $(date) ==="

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

# 2) Stop services that own WiFi / portal
systemctl stop sudo-dashboard.service sudo-remote-access.service \
    wifi-setup.service raspi-hotspot.service picoclaw-gateway.service sudo-openclaw-gateway.service \
    2>/dev/null || true
systemctl disable sudo-dashboard.service sudo-cloud-init.service \
    sudo-remote-access.service 2>/dev/null || true

# 3) Wipe ALL WiFi / portal state
rm -f /var/lib/wifi-setup-configured /var/lib/wifi-setup-pending
rm -f /var/lib/sudo-cloud-registered /var/lib/sudo-remote-enabled
rm -f /etc/sudo/public_url
rm -f /etc/sudo/autoupdate

rfkill unblock wifi 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true

# Delete every WiFi connection profile (home + RaspiSetup leftovers)
for con in $(nmcli -t -f NAME,TYPE connection show 2>/dev/null | awk -F: '$2=="802-11-wireless" || $2=="wifi"{print $1}'); do
    echo "Deleting WiFi profile: $con"
    nmcli connection delete "$con" 2>/dev/null || true
done
# Also catch by active wifi
for con in $(nmcli -t -f NAME,TYPE con show --active 2>/dev/null | awk -F: '$2=="wifi"{print $1}'); do
    nmcli connection down "$con" 2>/dev/null || true
    nmcli connection delete "$con" 2>/dev/null || true
done

# 4) Soft-clear onboarding prefs (keep device secret / password files)
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
    # Owner-controlled settings — a factory unit must not inherit these
    "exec_enabled", "ssh_enabled", "ssh_auth",
    "openrouter_key_source", "user_openrouter_key", "composio_api_key",
    # Added since: keys, linked numbers and choices a new owner must not inherit.
    "provider_keys", "github_token", "github_username", "github_enabled",
    "whatsapp_mode", "whatsapp_numbers", "whatsapp_owner_number",
    "whatsapp_read_others", "whatsapp_in_summary",
    "full_access_enabled", "subagent_enabled", "skills_enabled",
    "agent_language", "ui_language", "dashboard_session",
    "spend_enabled", "spend_max_per_day", "spend_max_per_minute", "spend_cooldown_min",
    "remote_password_enabled", "balance_usd", "subscription_status",
):
    data.pop(key, None)
os.makedirs("/opt/sudo", exist_ok=True)
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
os.chmod(path, 0o600)
PY

rm -rf /opt/sudo/agent-workspace /var/lib/sudo-agent-files.json
mkdir -p /opt/sudo/agent-workspace
rm -f /opt/sudo/picoclaw/config.json
rm -rf /opt/sudo/openclaw
rm -f /etc/sudo/agent.env
rm -f /opt/sudo/dashboard-chat.json

# 4b) Tear down any terminal access — a factory unit ships with sshd off,
# no stored password, and none of our injected keys or sshd drop-ins.
systemctl disable --now ssh 2>/dev/null || true
rm -f /etc/sudo/ssh_password
rm -f /etc/ssh/sshd_config.d/05-sudo-user.conf /etc/ssh/sshd_config.d/10-sudo-dev.conf
DEV_HOME="$(getent passwd 1000 | cut -d: -f6)"
if [ -n "$DEV_HOME" ] && [ -f "${DEV_HOME}/.ssh/authorized_keys" ]; then
    rm -f "${DEV_HOME}/.ssh/authorized_keys"
    echo "Removed installed SSH keys for factory state"
fi
# Deliberately NOT removing the boot-partition dev artifacts here.
# prepare-sd.sh owns what is staged on the boot partition — it deletes
# ssh / dev-authorized_keys / dev-setup.sh unless --dev was passed. If we
# also removed them at this point, install-factory.sh (which runs a few
# lines below) could never reinstall the key, and `--reset --dev` would
# hand back a device with no way to log in. This script owns installed
# state; the flashing script owns the card.

# 5) Reinstall factory services (enables hotspot because marker is gone)
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

# Verify AP if possible
ip addr show wlan0 || true
nmcli -t -f NAME,DEVICE,TYPE,STATE connection show --active || true

# 6) Remove one-shot boot scripts (cmdline already clean)
rm -f /boot/firmware/factory-reset.sh \
      /boot/firmware/wifi-heal.sh \
      /boot/firmware/repair-portal.sh \
      /boot/firmware/factory-retest.sh \
      /boot/firmware/firstrun.sh

echo "=== factory-reset complete — rebooting $(date) ==="
echo "Hotspot: RaspiSetup (open, no password) → http://192.168.4.1/"
reboot
