#!/bin/bash
# Factory install — called from firstrun.sh, repair-portal.sh, factory-reset.sh

AP_IP="192.168.4.1"
BOOT="/boot/firmware"

echo "=== install-factory $(date) ==="

# ── WiFi setup portal ────────────────────────────────────────────────────────
mkdir -p /opt/wifi-setup/templates
cp "${BOOT}/wifi-setup/app.py" /opt/wifi-setup/
cp "${BOOT}/wifi-setup/templates/"*.html /opt/wifi-setup/templates/
cp "${BOOT}/wifi-setup/templates/brand.css" /opt/wifi-setup/templates/
mkdir -p /opt/wifi-setup/assets/fonts
cp "${BOOT}/wifi-setup/assets/"*.css /opt/wifi-setup/assets/
cp "${BOOT}/wifi-setup/assets/sudo-wordmark.svg" /opt/wifi-setup/assets/
cp "${BOOT}/wifi-setup/assets/fonts/"*.woff2 /opt/wifi-setup/assets/fonts/

# ── Sudo Dashboard (served after WiFi is configured) ─────────────────────────
mkdir -p /opt/sudo-dashboard
cp "${BOOT}/sudo-dashboard/index.html" /opt/sudo-dashboard/
cp "${BOOT}/sudo-dashboard/server.py" /opt/sudo-dashboard/
cp "${BOOT}/sudo-dashboard/app.css" /opt/sudo-dashboard/
cp "${BOOT}/sudo-dashboard/manifest.json" /opt/sudo-dashboard/
cp "${BOOT}/sudo-dashboard/sudo-wordmark.svg" /opt/sudo-dashboard/

# ── WhatsApp bridge (source only -- npm install happens on demand, once
# there is internet to fetch it with; see sudo-pi/install-whatsapp-bridge.sh)
mkdir -p /opt/whatsapp-bridge /opt/sudo/whatsapp-auth
cp "${BOOT}/whatsapp-bridge/package.json" /opt/whatsapp-bridge/
cp "${BOOT}/whatsapp-bridge/bridge.js" /opt/whatsapp-bridge/

# ── PicoClaw (pre-bundled binary, no npm on Pi) ─────────────────────────────
mkdir -p /opt/picoclaw /opt/sudo/picoclaw /opt/sudo/agent-workspace
if [ -f "${BOOT}/picoclaw/picoclaw" ]; then
    cp "${BOOT}/picoclaw/picoclaw" /usr/local/bin/picoclaw
    chmod +x /usr/local/bin/picoclaw
fi
if [ -f "${BOOT}/picoclaw/config.template.json" ]; then
    cp "${BOOT}/picoclaw/config.template.json" /opt/picoclaw/config.template.json
fi
# Agent workspace templates — configure-picoclaw.sh renders these into
# /opt/sudo/agent-workspace with the user's agent/user name filled in.
if [ -d "${BOOT}/picoclaw/workspace" ]; then
    mkdir -p /opt/picoclaw/workspace
    cp "${BOOT}/picoclaw/workspace/"*.md /opt/picoclaw/workspace/
fi

# ── cloudflared (remote link, bundled like picoclaw) ────────────────────────
if [ -f "${BOOT}/cloudflared/cloudflared" ]; then
    cp "${BOOT}/cloudflared/cloudflared" /usr/local/bin/cloudflared
    chmod +x /usr/local/bin/cloudflared
fi
cp "${BOOT}/sudo-pi/configure-picoclaw.sh" /usr/local/bin/sudo-configure-picoclaw.sh
chmod +x /usr/local/bin/sudo-configure-picoclaw.sh

cat > /etc/systemd/system/picoclaw-gateway.service << 'EOF'
[Unit]
Description=PicoClaw Gateway
After=network-online.target sudo-cloud-init.service
Wants=network-online.target
ConditionPathExists=/opt/sudo/picoclaw/config.json

[Service]
Type=simple
Environment=HOME=/root
Environment=PICOCLAW_CONFIG=/opt/sudo/picoclaw/config.json
ExecStart=/usr/local/bin/picoclaw gateway
Restart=on-failure
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

# ── Sudo cloud / device identity ─────────────────────────────────────────────
mkdir -p /opt/sudo /etc/sudo /opt/sudo-pi

# Sudo-managed credits + cloud registration are OPT-IN and OFF by default: the
# box ships zero-cloud, the owner pastes their own API key. Set SUDO_BILLING=1
# in the environment to build a device that can use Sudo credits -- that flips
# on the cloud-registration unit and the dashboard billing card. Recorded here
# so the wifi app, the init script and the dashboard all agree.
SUDO_BILLING="${SUDO_BILLING:-0}"
case "${SUDO_BILLING}" in
    1|true|yes|on) BILLING_ON=1 ;;
    *)            BILLING_ON=0 ;;
esac
if [ "${BILLING_ON}" = "1" ]; then
    echo "billing=1" > /etc/sudo/billing-enabled
else
    echo "billing=0" > /etc/sudo/billing-enabled
fi
cp "${BOOT}/sudo-pi/device-id.sh" /usr/local/bin/sudo-device-id.sh
cp "${BOOT}/sudo-pi/device-secrets.sh" /usr/local/bin/sudo-device-secrets.sh
cp "${BOOT}/sudo-pi/cloud-init.sh" /usr/local/bin/sudo-cloud-init.sh
cp "${BOOT}/sudo-pi/setup-remote-access.sh" /usr/local/bin/sudo-remote-access.sh
cp "${BOOT}/sudo-pi/install-devtools.sh" /usr/local/bin/sudo-install-devtools.sh
cp "${BOOT}/sudo-pi/setup-ssh.sh" /usr/local/bin/sudo-setup-ssh.sh
cp "${BOOT}/sudo-pi/install-local-model.sh" /usr/local/bin/sudo-install-local-model.sh
cp "${BOOT}/sudo-pi/set-hostname.sh" /usr/local/bin/sudo-set-hostname.sh
cp "${BOOT}/sudo-pi/install-whatsapp-bridge.sh" /usr/local/bin/sudo-install-whatsapp-bridge.sh
cp "${BOOT}/sudo-pi/unlink-whatsapp.sh" /usr/local/bin/sudo-unlink-whatsapp.sh
cp "${BOOT}/sudo-pi/sudo-heartbeat.sh" /usr/local/bin/sudo-heartbeat.sh
cp "${BOOT}/sudo-pi/spend-guard.py" /usr/local/bin/sudo-spend-guard.py
cp "${BOOT}/sudo-pi/sudo-update.sh" /usr/local/bin/sudo-update.sh
cp "${BOOT}/sudo-pi/trigger-cloud-init.py" /opt/sudo-pi/
# The bundle this device was built from, kept on-device as the update baseline.
# Lets an update (or a rollback) apply over Wi-Fi without a network round-trip.
if [ -f "${BOOT}/sudo-factory.tar.gz" ]; then
    cp "${BOOT}/sudo-factory.tar.gz" /usr/local/bin/sudo-factory.tar.gz
    [ -f "${BOOT}/sudo-factory.sha256" ] && cp "${BOOT}/sudo-factory.sha256" /usr/local/bin/sudo-factory.sha256
    tr -d ' \t\r\n' < "${BOOT}/sudo-factory.tar.gz.ver" > /opt/sudo/version 2>/dev/null \
        || printf '%s\n' "1.0.0" > /opt/sudo/version
    chmod 644 /opt/sudo/version 2>/dev/null || true
fi
chmod +x /usr/local/bin/sudo-device-id.sh \
         /usr/local/bin/sudo-device-secrets.sh \
         /usr/local/bin/sudo-cloud-init.sh \
         /usr/local/bin/sudo-remote-access.sh \
         /usr/local/bin/sudo-install-devtools.sh \
         /usr/local/bin/sudo-setup-ssh.sh \
         /usr/local/bin/sudo-install-local-model.sh \
         /usr/local/bin/sudo-set-hostname.sh \
         /usr/local/bin/sudo-install-whatsapp-bridge.sh \
         /usr/local/bin/sudo-unlink-whatsapp.sh \
         /usr/local/bin/sudo-heartbeat.sh \
         /usr/local/bin/sudo-spend-guard.py \
         /usr/local/bin/sudo-update.sh

if [ -f "${BOOT}/sudo-api/api.url.default" ] && [ "${BILLING_ON}" = "1" ]; then
    cp "${BOOT}/sudo-api/api.url.default" /etc/sudo/api.url
fi

# Offline secrets (device_secret + dashboard password) — NO internet needed
/usr/local/bin/sudo-device-id.sh >/dev/null 2>&1 || true
/usr/local/bin/sudo-device-secrets.sh >/dev/null 2>&1 || true

# Remote access uses Cloudflare Quick Tunnel (no domain / no API token on device)
mkdir -p /var/lib/sudo-cloudflared-quick
rm -f /etc/sudo/cloudflare.conf "${BOOT}/sudo-pi/cloudflare.conf" 2>/dev/null || true
echo "Remote access: Quick Tunnel (trycloudflare.com) — no domain required"

# ── Captive portal DNS (hotspot only) ───────────────────────────────────────
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/raspi-captive.conf << EOF
address=/#/${AP_IP}
EOF

# ── WiFi setup service (only before first WiFi config) ───────────────────────
cat > /etc/systemd/system/wifi-setup.service << 'EOF'
[Unit]
Description=Sudo WiFi Setup Portal
After=NetworkManager.service
Wants=NetworkManager.service
ConditionPathExists=!/var/lib/wifi-setup-configured

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/wifi-setup/app.py
Restart=always
RestartSec=2
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# ── WhatsApp bridge -- disabled until someone actually connects WhatsApp.
# install-whatsapp-bridge.sh is what runs `systemctl enable --now` on this;
# nothing here starts it, and no-one who never opens Settings > Channels
# ever has this process running or its dependencies installed at all.
cat > /etc/systemd/system/sudo-whatsapp-bridge.service << 'EOF'
[Unit]
Description=Sudo WhatsApp Bridge
After=network-online.target sudo-dashboard.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/whatsapp-bridge
ExecStart=/usr/bin/node /opt/whatsapp-bridge/bridge.js
Restart=always
RestartSec=5
User=root
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/sudo/whatsapp-auth /opt/sudo/whatsapp /var/lib /var/log
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
# No enable/disable call here on purpose: writing this file does not start
# or enable anything by itself, and a re-run of this script -- dev-reset,
# repair, factory-retest -- must not silently turn off a bridge someone
# already paired. install-whatsapp-bridge.sh is the only thing that ever
# calls `systemctl enable` on this unit; whoever never ran that never has
# it running, and whoever did keeps it running across a reset.

# ── Proactive heartbeat (device speaks first, occasionally) ──────────────────
# The timer wakes every 30 min; the script decides whether there is anything
# worth saying and usually does nothing. It only reaches the owner's WhatsApp
# once they have messaged the device, so the service is idle until the bridge
# is paired and used -- same "costs nothing until asked for" shape as the
# bridge itself.
#
# ON BY DEFAULT. Shipping this off would make the agent a thing that only ever
# answers, never one that speaks first -- which is the whole point of the box.
# The owner can switch it off in Settings -> What sudo can do (that toggle runs
# `systemctl enable/disable --now sudo-heartbeat.timer`), so the default is a
# starting point, not a cage. The timer is enabled at the end of this script.
cat > /etc/systemd/system/sudo-heartbeat.service << 'EOF'
[Unit]
Description=Sudo Proactive Heartbeat
After=network-online.target sudo-dashboard.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/sudo-heartbeat.sh
User=root
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/sudo /var/lib /var/log
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/sudo-heartbeat.timer << 'EOF'
[Unit]
Description=Sudo Proactive Heartbeat Timer

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload 2>/dev/null || true
# Enabled by default -- see the note above. Settings can turn it back off.
systemctl enable --now sudo-heartbeat.timer 2>/dev/null || true

# ── Sudo Dashboard (LAN + remote tunnel both use :80) ───────────────────────
cat > /etc/systemd/system/sudo-dashboard.service << 'EOF'
[Unit]
Description=Sudo Dashboard
After=network-online.target NetworkManager.service
Wants=network-online.target
ConditionPathExists=/var/lib/wifi-setup-configured

[Service]
Type=simple
ExecStartPre=/usr/local/bin/sudo-device-secrets.sh
ExecStart=/usr/bin/python3 /opt/sudo-dashboard/server.py
Restart=always
RestartSec=3
User=root
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/opt/sudo /etc/sudo /var/lib /var/log /opt/sudo-dashboard

[Install]
WantedBy=multi-user.target
EOF

# ── Remote access (Cloudflare Quick Tunnel — no domain) ─────────────────────
cat > /etc/systemd/system/sudo-remote-access.service << 'EOF'
[Unit]
Description=Sudo Remote Access (Cloudflare Quick Tunnel)
After=network-online.target sudo-dashboard.service
Wants=network-online.target
ConditionPathExists=/var/lib/sudo-remote-enabled

[Service]
Type=simple
ExecStart=/usr/local/bin/sudo-remote-access.sh run
Restart=on-failure
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
EOF

# ── Cloud registration (opt-in; only when SUDO_BILLING=1) ────────
cat > /etc/systemd/system/sudo-cloud-init.service << 'EOF'
[Unit]
Description=Sudo Cloud Registration
After=network-online.target sudo-dashboard.service
Wants=network-online.target
ConditionPathExists=/var/lib/wifi-setup-configured
ConditionPathExists=!/var/lib/sudo-cloud-registered

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 15
ExecStart=/usr/local/bin/sudo-cloud-init.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# ── Hotspot service (only before first WiFi config) ──────────────────────────
cat > /etc/systemd/system/raspi-hotspot.service << 'EOF'
[Unit]
Description=Sudo Setup Hotspot
After=NetworkManager.service wifi-setup.service
Wants=NetworkManager.service wifi-setup.service
ConditionPathExists=!/var/lib/wifi-setup-configured

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/start-hotspot.sh
Restart=on-failure
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/start-hotspot.sh << 'HOTSPOT'
#!/bin/bash
LOG=/var/log/raspi-hotspot.log
# Mirrored onto the FAT boot partition: when the hotspot fails there is by
# definition no network to log in over, and /var/log is on ext4 which a
# Windows host cannot read. Truncated each boot so it stays small and always
# describes the run you just did.
BOOTLOG=/boot/firmware/hotspot.log
# Deliberately NOT piped through tee. A pipe here is dangerous: if tee cannot
# write the FAT partition it exits, stdout becomes a broken pipe, and the next
# echo kills this script by SIGPIPE -- leaving the AP up but never reaching the
# lines below that hand out DHCP. Write to one plain file, copy it on the way
# out, and nothing in the hotspot path depends on the copy succeeding.
exec >> "$LOG" 2>&1
trap 'tail -c 20000 "$LOG" > "$BOOTLOG" 2>/dev/null || true' EXIT
echo "=== hotspot start $(date) ==="
echo "regdomain: $(iw reg get 2>/dev/null | grep -m1 country || echo unknown)"

AP_IP="192.168.4.1"
SSID="RaspiSetup"
# No password: the setup network is open. See start_ap below.

if [ -f /var/lib/wifi-setup-configured ]; then
    echo "WiFi already configured — hotspot disabled by design"
    exit 0
fi

rfkill unblock wifi 2>/dev/null || true
for f in /var/lib/systemd/rfkill/*:wlan*; do
    [ -e "$f" ] && echo 0 > "$f" 2>/dev/null || true
done
REGDOM="$(sed -n 's/.*cfg80211\.ieee80211_regdom=\([A-Z][A-Z]\).*/\1/p' /boot/firmware/cmdline.txt 2>/dev/null)"
raspi-config nonint do_wifi_country "${REGDOM:-US}" 2>/dev/null || true

# Wait for NetworkManager + wlan0 (up to 90s)
for i in $(seq 1 45); do
    if nmcli general status >/dev/null 2>&1 && \
       nmcli -t -f DEVICE,TYPE dev 2>/dev/null | grep -q "^wlan0:wifi$"; then
        break
    fi
    sleep 2
done

# Drop any existing WiFi connection so AP can start
for con in $(nmcli -t -f NAME,TYPE con show --active 2>/dev/null | awk -F: '$2=="wifi"{print $1}'); do
    nmcli connection down "$con" 2>/dev/null || true
done

nmcli radio wifi on 2>/dev/null || true
nmcli device set wlan0 managed yes 2>/dev/null || true
nmcli connection down "$SSID" 2>/dev/null || true
nmcli connection delete "$SSID" 2>/dev/null || true

start_ap() {
    # Built by hand rather than through `nmcli device wifi hotspot`: that
    # helper always attaches WPA, inventing a password when none is given,
    # and this network is deliberately open. The profile was deleted just
    # above, so a fresh one carries no security settings at all.
    nmcli con add type wifi ifname wlan0 con-name "$SSID" autoconnect no ssid "$SSID" 2>&1
    nmcli con modify "$SSID" 802-11-wireless.mode ap
    nmcli con modify "$SSID" 802-11-wireless.band bg
    nmcli con modify "$SSID" 802-11-wireless.channel 6
    # Defensive: strip security in case an older secured profile survived.
    nmcli con modify "$SSID" remove wifi-sec 2>/dev/null || true
    nmcli con modify "$SSID" ipv4.method shared
    nmcli con modify "$SSID" ipv4.addresses "${AP_IP}/24"
    if nmcli con up "$SSID" 2>&1; then
        return 0
    fi
    echo "open AP on channel 6 failed — retrying with an automatic channel"
    nmcli con modify "$SSID" 802-11-wireless.channel 0 2>/dev/null || true
    nmcli con up "$SSID" 2>&1
}

start_ap

# No security pinning here any more: the setup network is open, and the old
# wpa-psk/rsn/ccmp/pmf line would have put WPA straight back on.
# Pin band and channel. Left to choose, NetworkManager can land the AP on
# channel 12 or 13 — legal under regulatory domains like SA, but plenty of
# phones (US-market iPhones especially) will list such a network and then
# refuse to associate with it. The choice is made per boot, so it shows up as
# a hotspot that works one day and "cannot join" the next. 6 is safe anywhere.
nmcli connection modify "$SSID" 802-11-wireless.band bg 2>/dev/null || true
nmcli connection modify "$SSID" 802-11-wireless.channel 6 2>/dev/null || true
nmcli connection modify "$SSID" ipv4.addresses "${AP_IP}/24" 2>/dev/null || true
nmcli connection modify "$SSID" ipv4.method shared 2>/dev/null || true
nmcli connection down "$SSID" 2>/dev/null || true
nmcli connection up "$SSID" 2>/dev/null || true

sleep 4
if ip addr show wlan0 2>/dev/null | grep -q "${AP_IP}"; then
    echo "Hotspot active at ${AP_IP}"
    # Record the channel actually in use — if a phone still cannot join, this
    # is the first thing worth knowing.
    iw dev wlan0 info 2>/dev/null | grep -E "channel|ssid|type" | sed 's/^/  /' || true

    # A phone that associates but gets no address reports it the same way as
    # one that could not associate at all: "unable to join". These lines
    # separate the two cases. `shared` is what makes NetworkManager run
    # dnsmasq for DHCP and DNS; if that failed to start, nothing here hands
    # the phone an address and the AP looks fine from this side regardless.
    echo "DHCP/DNS check:"
    pgrep -a dnsmasq | sed 's/^/  /' || echo "  dnsmasq NOT running — no DHCP will be offered"
    ss -ulnp 2>/dev/null | grep -E ":67|:53" | sed 's/^/  /' || echo "  nothing listening on DHCP/DNS ports"
    nmcli -g ipv4.method,ipv4.addresses connection show "$SSID" 2>/dev/null | sed 's/^/  ipv4: /' || true
    echo "security (empty means open):"
    nmcli -g 802-11-wireless-security.key-mgmt connection show "$SSID" 2>/dev/null | sed 's/^/  key-mgmt: /' || true
    echo "recent NetworkManager entries:"
    journalctl -u NetworkManager --no-pager -n 25 2>/dev/null | sed 's/^/  /' || true
    ip addr show wlan0
    exit 0
fi

echo "Hotspot failed — diagnostics:"
nmcli dev status || true
nmcli radio || true
nmcli -f GENERAL,WIFI-PROPERTIES device show wlan0 2>/dev/null || true
iw dev wlan0 info 2>/dev/null || true
iw reg get 2>/dev/null | head -5 || true
rfkill list 2>/dev/null || true
ip addr show wlan0 || true
journalctl -u NetworkManager --no-pager -n 40 2>/dev/null || true
exit 1
HOTSPOT
chmod +x /usr/local/bin/start-hotspot.sh

# ── mDNS — reach device as http://raspberrypi.local ─────────────────────────
# Enable only -- installing is impossible here. This runs during first boot,
# before the hotspot exists, so there is no network and apt cannot reach a
# mirror. If the package is absent, the portal installs it right after the
# Wi-Fi join instead (ensure_mdns in wifi-setup/app.py), which is the first
# moment the device is actually online.
if systemctl list-unit-files avahi-daemon.service 2>/dev/null | grep -q avahi-daemon; then
    systemctl enable avahi-daemon.service 2>/dev/null || true
    systemctl start avahi-daemon.service 2>/dev/null || true
else
    echo "avahi-daemon not installed — .local will be set up after Wi-Fi join"
fi

# ── Remove legacy services ───────────────────────────────────────────────────
systemctl disable captive-portal.service iptables-restore.service iptables-save.service 2>/dev/null || true
systemctl stop captive-portal.service 2>/dev/null || true
rm -f /etc/systemd/system/captive-portal.service
rm -f /etc/systemd/system/iptables-restore.service
rm -f /etc/systemd/system/iptables-save.service
rm -f /usr/local/bin/captive-portal.sh

# ── Dev SSH access (only when prepare-sd.sh --dev staged a key) ─────────────
if [ -f "${BOOT}/dev-authorized_keys" ]; then
    DEV_USER="$(getent passwd 1000 | cut -d: -f1)"
    DEV_HOME="$(getent passwd 1000 | cut -d: -f6)"
    if [ -n "$DEV_USER" ] && [ -d "$DEV_HOME" ]; then
        mkdir -p "${DEV_HOME}/.ssh"
        cat "${BOOT}/dev-authorized_keys" >> "${DEV_HOME}/.ssh/authorized_keys"
        # Drop duplicates if this ran before
        sort -u "${DEV_HOME}/.ssh/authorized_keys" -o "${DEV_HOME}/.ssh/authorized_keys"
        chmod 700 "${DEV_HOME}/.ssh"
        chmod 600 "${DEV_HOME}/.ssh/authorized_keys"
        chown -R "${DEV_USER}:${DEV_USER}" "${DEV_HOME}/.ssh"

        # Key is installed, so passwords are no longer needed to get in.
        mkdir -p /etc/ssh/sshd_config.d
        cat > /etc/ssh/sshd_config.d/10-sudo-dev.conf << 'EOF'
PasswordAuthentication no
PermitRootLogin no
EOF
        echo "Dev SSH: key installed for ${DEV_USER}, password login disabled"
    else
        echo "Dev SSH: no uid-1000 user found — skipped key install"
    fi
    systemctl enable ssh 2>/dev/null || true
    systemctl start ssh 2>/dev/null || true
    rm -f "${BOOT}/dev-authorized_keys"
fi


# ── Recovery: reopen setup if the saved Wi-Fi is gone ───────────────────────
# Once configured, the hotspot and portal are disabled by design. That is
# right at home and wrong everywhere else: carry the device to a new place,
# or change the router password, and it comes up with no way in and no way to
# tell it the new network. Reflashing to move a box between rooms is not a
# product.
#
# This watches for a usable address after boot. If none appears it clears the
# configured marker and starts the hotspot and portal, so RaspiSetup shows up
# again in Wi-Fi settings exactly like a new device. Reconnecting rewrites the
# marker and the device returns to normal on its own.
cat > /usr/local/bin/sudo-netwatch.sh << 'NETWATCH'
#!/bin/bash
LOG=/var/log/sudo-netwatch.log
BOOTLOG=/boot/firmware/netwatch.log
exec >> "$LOG" 2>&1
trap 'tail -c 8000 "$LOG" > "$BOOTLOG" 2>/dev/null || true' EXIT
echo "=== netwatch $(date) ==="

MARKER=/var/lib/wifi-setup-configured
GRACE=${SUDO_NETWATCH_GRACE:-120}

if [ ! -f "$MARKER" ]; then
    echo "not configured yet — the first-run flow owns the hotspot"
    exit 0
fi

# Any global IPv4 that is not the AP's own address counts, so a device on
# ethernet is left alone: it is reachable, just not over Wi-Fi.
have_network() {
    local addrs a
    addrs="$(ip -4 -o addr show scope global 2>/dev/null | awk '$2 != "lo" {print $4}' | cut -d/ -f1)"
    for a in $addrs; do
        [ "$a" = "192.168.4.1" ] && continue
        echo "  address: $a"
        return 0
    done
    return 1
}

waited=0
while [ "$waited" -lt "$GRACE" ]; do
    if have_network; then
        echo "network up after ${waited}s — nothing to do"
        exit 0
    fi
    sleep 2
    waited=$((waited + 2))
done

echo "no usable address after ${GRACE}s — reopening setup"
# Clearing the marker is what flips the ConditionPathExists on both units.
rm -f "$MARKER"
# The dashboard is already holding port 80 from this boot, and the portal
# wants the same port. Stop it first or the portal cannot bind and the
# hotspot appears with nothing serving it.
systemctl stop sudo-dashboard.service 2>&1 || true
systemctl start raspi-hotspot.service 2>&1 || true
systemctl start wifi-setup.service 2>&1 || true
systemctl is-active wifi-setup.service | sed 's/^/  portal: /' || true
sleep 5
ip addr show wlan0 2>/dev/null | grep -q "192.168.4.1" \
    && echo "RaspiSetup is back at 192.168.4.1" \
    || echo "hotspot did not come up — see /boot/firmware/hotspot.log"
NETWATCH
chmod +x /usr/local/bin/sudo-netwatch.sh

cat > /etc/systemd/system/sudo-netwatch.service << 'EOF'
[Unit]
Description=Sudo network watchdog — reopen setup if the saved Wi-Fi is unreachable
After=NetworkManager.service network.target
Wants=NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=no
ExecStart=/usr/local/bin/sudo-netwatch.sh
TimeoutStartSec=400

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# ── On-device greeter ───────────────────────────────────────────────────────
# Runs on boot rather than from the portal: the portal reboots seconds after a
# successful join, which would kill a multi-minute download halfway. This
# retries each boot and exits immediately once the model is in place, so a
# device that was offline the first time picks it up later on its own.
cat > /etc/systemd/system/sudo-local-model.service << 'EOF'
[Unit]
Description=Install the on-device greeter model
After=network-online.target NetworkManager.service
Wants=network-online.target
ConditionPathExists=/var/lib/wifi-setup-configured

[Service]
Type=oneshot
RemainAfterExit=no
ExecStart=/usr/local/bin/sudo-install-local-model.sh
# systemd units get no HOME, and the ollama CLI panics without one.
Environment=HOME=/root
TimeoutStartSec=1800
# Nothing depends on this: the box works without it, chat just falls back.
SuccessExitStatus=0 1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# Enable based on setup state
if [ -f /var/lib/wifi-setup-configured ]; then
    systemctl enable sudo-dashboard.service sudo-remote-access.service
    # Cloud registration only if this build opted into Sudo credits.
    if [ "${BILLING_ON}" = "1" ]; then
        systemctl enable sudo-cloud-init.service
    else
        systemctl disable sudo-cloud-init.service 2>/dev/null || true
    fi
    systemctl disable wifi-setup.service raspi-hotspot.service 2>/dev/null || true
    systemctl enable picoclaw-gateway.service 2>/dev/null || true
else
    systemctl enable wifi-setup.service raspi-hotspot.service
    systemctl disable sudo-dashboard.service sudo-cloud-init.service sudo-remote-access.service 2>/dev/null || true
fi

# Always on: it is the only thing that can recover a device whose saved
# network has gone away, and it is a no-op before first setup.
systemctl enable sudo-netwatch.service 2>/dev/null || true
systemctl enable sudo-local-model.service 2>/dev/null || true

echo "=== install-factory complete ==="
echo "Dashboard credentials: /etc/sudo/dashboard_credentials.txt"
echo "Remote link: enabled from onboarding (no domain needed)"
