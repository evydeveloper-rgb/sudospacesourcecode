#!/bin/bash
# wifi-heal.sh — clear stuck boot hooks and bring home WiFi back
# Keeps existing NetworkManager WiFi profiles (does not wipe).

exec > /boot/firmware/wifi-heal.log 2>&1
set -x

echo "=== wifi-heal started $(date) ==="

# 1) Clear cmdline FIRST so we never boot-loop again
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

# 2) Refresh factory files if present
if [ -f /boot/firmware/install-factory.sh ]; then
    bash /boot/firmware/install-factory.sh || true
fi

rfkill unblock wifi 2>/dev/null || true
raspi-config nonint do_wifi_country ID 2>/dev/null || true

systemctl enable NetworkManager 2>/dev/null || true
systemctl restart NetworkManager
sleep 8

nmcli radio wifi on 2>/dev/null || true
nmcli device set wlan0 managed yes 2>/dev/null || true

# Bring up any saved WiFi profiles (not RaspiSetup)
for con in $(nmcli -t -f NAME,TYPE,TIMESTAMP-REAL connection show 2>/dev/null | awk -F: '$2=="802-11-wireless"{print $1}'); do
    [ "$con" = "RaspiSetup" ] && continue
    echo "Trying connection: $con"
    nmcli connection modify "$con" connection.autoconnect yes 2>/dev/null || true
    nmcli connection up "$con" 2>/dev/null && break || true
done

# If a wifi-setup marker exists, prefer dashboard; else hotspot
if [ -f /var/lib/wifi-setup-configured ]; then
    systemctl enable sudo-dashboard.service 2>/dev/null || true
    systemctl disable wifi-setup.service raspi-hotspot.service 2>/dev/null || true
    systemctl restart sudo-dashboard.service 2>/dev/null || true
else
    systemctl enable wifi-setup.service raspi-hotspot.service 2>/dev/null || true
    systemctl restart raspi-hotspot.service 2>/dev/null || true
    systemctl restart wifi-setup.service 2>/dev/null || true
fi

ip addr show wlan0 || true
nmcli -t -f NAME,DEVICE,TYPE,STATE connection show --active || true

rm -f /boot/firmware/wifi-heal.sh /boot/firmware/factory-retest.sh
echo "=== wifi-heal complete — rebooting $(date) ==="
reboot
