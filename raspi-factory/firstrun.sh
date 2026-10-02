#!/bin/bash
# firstrun.sh — runs once on first boot (triggered via cmdline.txt)

exec > /boot/firmware/firstrun.log 2>&1
set -x

echo "=== firstrun started $(date) ==="

if [ -f /usr/lib/raspberrypi-sys-mods/imager_custom ]; then
    if [ -f /boot/firmware/firstrun.imager.bak ]; then
        bash /boot/firmware/firstrun.imager.bak || true
    fi
fi

# Raspberry Pi Imager configures the device with cloud-init (ds=nocloud in
# cmdline.txt). It creates the login user on this same boot, and
# install-factory.sh needs that user to exist -- it installs the dev SSH key
# into uid 1000's home and silently skips when there is no such user.
#
# Wait for THAT and nothing more. Do not wait for cloud-init overall: its
# package step (avahi-daemon) needs the network, and at this point there is no
# network by design -- the whole purpose of the next few lines is to bring up
# the setup hotspot so the owner can give us one. Waiting for cloud-init to
# finish here deadlocks: no network until the hotspot, no hotspot until
# cloud-init. The users module runs early, well before packages.
if command -v cloud-init >/dev/null 2>&1; then
    echo "waiting for the login user (max 120s)..."
    for _ in $(seq 1 60); do
        getent passwd 1000 >/dev/null 2>&1 && break
        sleep 2
    done
    echo "uid 1000: $(getent passwd 1000 | cut -d: -f1 2>/dev/null || echo 'still absent')"
fi

rfkill unblock wifi 2>/dev/null || true
for f in /var/lib/systemd/rfkill/*:wlan*; do
    [ -e "$f" ] && echo 0 > "$f" 2>/dev/null || true
done
# Imager writes the owner's choice into cmdline.txt as
# cfg80211.ieee80211_regdom. Honour it; fall back to US only if absent.
REGDOM="$(sed -n 's/.*cfg80211\.ieee80211_regdom=\([A-Z][A-Z]\).*/\1/p' /boot/firmware/cmdline.txt 2>/dev/null)"
raspi-config nonint do_wifi_country "${REGDOM:-US}" 2>/dev/null || true

bash /boot/firmware/install-factory.sh

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

rm -f /boot/firmware/firstrun.sh /boot/firmware/firstrun.imager.bak
echo "=== firstrun complete — rebooting $(date) ==="
reboot
