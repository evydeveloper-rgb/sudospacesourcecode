#!/bin/bash
# repair-portal.sh — one-time fix / upgrade on already-flashed Pis

exec > /boot/firmware/repair.log 2>&1
set -x

echo "=== repair started $(date) ==="

# Back up anything edited on the device BEFORE we overwrite it. install-factory
# replaces /opt/sudo-dashboard, /opt/wifi-setup and /opt/sudo-pi wholesale, so
# hand-edits made on the Pi (e.g. with Claude Code) would otherwise vanish with
# no copy anywhere. Windows can read the boot partition, so stage it there.
RECOVER="/boot/firmware/recovered"
rm -rf "$RECOVER"
mkdir -p "$RECOVER"
for d in sudo-dashboard wifi-setup sudo-pi; do
    [ -d "/opt/$d" ] && cp -r "/opt/$d" "$RECOVER/" 2>/dev/null
done
# The rendered agent files too — configure-picoclaw re-renders these from
# templates, so any personality the owner tuned by hand is otherwise lost.
[ -d /opt/sudo/agent-workspace ] && cp -r /opt/sudo/agent-workspace "$RECOVER/" 2>/dev/null
date > "$RECOVER/BACKED-UP-AT.txt"
echo "Backed up pre-existing /opt files to $RECOVER"

bash /boot/firmware/install-factory.sh

systemctl restart NetworkManager
sleep 5

if [ -f /var/lib/wifi-setup-configured ]; then
    # Refresh PicoClaw model list / config from new template
    if [ -x /usr/local/bin/sudo-configure-picoclaw.sh ]; then
        /usr/local/bin/sudo-configure-picoclaw.sh || true
    fi
    # Re-bind device secret + refresh OpenRouter key binding if needed
    if [ -x /usr/local/bin/sudo-cloud-init.sh ]; then
        rm -f /var/lib/sudo-cloud-registered 2>/dev/null || true
        /usr/local/bin/sudo-cloud-init.sh || true
        /usr/local/bin/sudo-configure-picoclaw.sh || true
    fi
    systemctl restart sudo-dashboard.service
    systemctl restart picoclaw-gateway.service 2>/dev/null || true
else
    systemctl restart raspi-hotspot.service
    sleep 5
    systemctl restart wifi-setup.service
fi

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

rm -f /boot/firmware/repair-portal.sh
echo "=== repair complete — rebooting $(date) ==="
reboot
