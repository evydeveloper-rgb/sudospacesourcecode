#!/bin/bash
# prepare-sd.sh — copy factory bundle to SD card boot partition (macOS / Linux)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIRSTRUN="$SCRIPT_DIR/firstrun.sh"
REPAIR="$SCRIPT_DIR/repair-portal.sh"
RESET="$SCRIPT_DIR/factory-reset.sh"
RETEST="$SCRIPT_DIR/factory-retest.sh"
DEVRESET="$SCRIPT_DIR/dev-reset.sh"
INSTALL="$SCRIPT_DIR/install-factory.sh"
WIFI_SETUP="$SCRIPT_DIR/wifi-setup"
SUDO_DASH="$SCRIPT_DIR/sudo-dashboard"
SUDO_PI="$SCRIPT_DIR/sudo-pi"
WHATSAPP_BRIDGE="$SCRIPT_DIR/whatsapp-bridge"
SUDO_API="$SCRIPT_DIR/sudo-api"
PICOCLAW="$SCRIPT_DIR/picoclaw"
CLOUDFLARED="$SCRIPT_DIR/cloudflared"
DOWNLOAD_PICO="$SCRIPT_DIR/scripts/download-picoclaw.sh"
DOWNLOAD_CF="$SCRIPT_DIR/scripts/download-cloudflared.sh"

MODE="install"
BOOT=""
DEV=0
DEV_KEYS="$SCRIPT_DIR/dev/authorized_keys"

usage() {
    cat <<EOF
Usage: $0 [--repair | --reset | --retest] [BOOT_VOLUME_PATH]

  (default)     First-time factory install (firstrun.sh on next boot)
  --repair      Upgrade bundle — keeps WiFi + existing setup state
  --reset       Wipe WiFi config and bring RaspiSetup hotspot back
  --retest      Soft reset for full UX test — KEEPS WiFi, clears onboarding/remote
  --devreset    Repeat the new-user run WITHOUT losing your setup. Wipes WiFi
                and onboarding like --reset, but keeps SSH, your API keys,
                ability toggles and Claude Code. Use this for repeat testing.

  --forget-keys With --devreset, ALSO clear saved API keys, so the device
                comes up with no key at all. Without it keys are kept.

  --dev         ALSO enable SSH on this card (in-house units only).
                Combine with any mode: $0 --repair --dev
                Put your public key at dev/authorized_keys first and it is
                installed for key-only login. NEVER use on a customer unit.

Examples:
  $0                  # fresh flash
  $0 --reset          # hotspot missing because WiFi was already configured
  $0 --repair
  $0 --retest          # test onboarding again without redoing WiFi
EOF
}

find_boot_volume() {
    local vol
    for vol in /Volumes/bootfs /Volumes/boot /Volumes/BOOT; do
        if [ -d "$vol" ] && [ -f "$vol/cmdline.txt" ]; then
            echo "$vol"
            return 0
        fi
    done
    # macOS renames colliding volumes to "bootfs 1", "bootfs 2", …
    shopt -s nullglob 2>/dev/null || true
    for vol in /Volumes/bootfs*; do
        if [ -d "$vol" ] && [ -f "$vol/cmdline.txt" ]; then
            echo "$vol"
            return 0
        fi
    done
    return 1
}

# Refuse to overlay a card that cannot boot Linux (constant LED blink / no hotspot).
assert_boot_firmware() {
    local boot="$1"
    local missing=()
    local f

    for f in start.elf start4.elf kernel8.img cmdline.txt; do
        if [ ! -f "$boot/$f" ] || [ ! -s "$boot/$f" ]; then
            missing+=("$f")
        fi
    done

    # config.txt must be real text, not a null-filled / FSCK-damaged blob
    if [ ! -f "$boot/config.txt" ]; then
        missing+=("config.txt")
    elif ! grep -qE '^[#[:space:]]*[a-zA-Z]' "$boot/config.txt" 2>/dev/null; then
        missing+=("config.txt(corrupt)")
    fi

    # Tiny initramfs files are a common FSCK failure mode on this card
    if [ -f "$boot/initramfs_2712" ]; then
        local isize
        isize="$(wc -c < "$boot/initramfs_2712" | tr -d ' ')"
        if [ "$isize" -lt 100000 ]; then
            missing+=("initramfs_2712(corrupt:${isize}B)")
        fi
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo "ERROR: Boot partition is missing/corrupt firmware: ${missing[*]}"
        echo ""
        echo "Constant LED blink + no hotspot/WiFi usually means the FAT boot"
        echo "partition cannot load the GPU firmware / kernel — NOT a WiFi bug."
        echo ""
        echo "Fix: restore boot firmware, then re-run this script:"
        echo "  ./repair-boot-firmware.sh \"$boot\""
        echo "  $0 --reset \"$boot\""
        echo ""
        echo "Or re-flash Raspberry Pi OS with Imager, then:"
        echo "  $0 /Volumes/bootfs"
        exit 1
    fi
}

cleanup_leftovers() {
    local boot="$1"
    if [ -d "$boot/{wifi-setup" ]; then
        rm -rf "$boot/{wifi-setup"
        echo "Removed leftover broken folder: {wifi-setup"
    fi
    rm -f "$boot/install-wifi-setup.sh" 2>/dev/null || true
    find "$boot" -maxdepth 3 -name '.DS_Store' -delete 2>/dev/null || true
    find "$boot" -maxdepth 3 -name '._*' -delete 2>/dev/null || true
}

patch_cmdline() {
    local boot="$1"
    local script_name="$2"
    local cmdline="$boot/cmdline.txt"

    if [ ! -f "$cmdline" ]; then
        echo "ERROR: cmdline.txt not found at $cmdline"
        exit 1
    fi

    local content root_part
    content="$(tr -d '\n' < "$cmdline")"
    # Preserve this card's root=PARTUUID=… (never inherit one from a donor image)
    root_part="$(printf '%s' "$content" | grep -oE 'root=PARTUUID=[^ ]+' | head -1 || true)"
    content="$(printf '%s' "$content" | sed -E \
        's/ systemd\.run=[^ ]*//g; s/ systemd\.run_success_action=[^ ]*//g; s/ systemd\.unit=[^ ]*//g')"
    if [ -n "$root_part" ] && ! printf '%s' "$content" | grep -q 'root=PARTUUID='; then
        content="$(printf '%s' "$content" | sed -E "s|root=[^ ]+|${root_part}|")"
    fi
    printf '%s systemd.run=/boot/firmware/%s systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n' \
        "$content" "$script_name" > "$cmdline"
    echo "cmdline.txt patched → will run ${script_name} on next boot"
    if [ -n "$root_part" ]; then
        echo "Kept $root_part"
    fi
}

copy_factory_files() {
    local boot="$1"

    if [ ! -f "$PICOCLAW/picoclaw" ]; then
        echo "PicoClaw binary missing — downloading..."
        bash "$DOWNLOAD_PICO"
    fi

    if [ ! -f "$CLOUDFLARED/cloudflared" ]; then
        echo "cloudflared binary missing — downloading..."
        bash "$DOWNLOAD_CF"
    fi

    rm -rf "$boot/wifi-setup" "$boot/sudo-dashboard" "$boot/sudo-pi" "$boot/sudo-api" "$boot/picoclaw" "$boot/cloudflared" "$boot/whatsapp-bridge"
    mkdir -p "$boot/wifi-setup/templates" "$boot/sudo-dashboard" "$boot/sudo-pi" "$boot/sudo-api" "$boot/picoclaw" "$boot/cloudflared" "$boot/whatsapp-bridge"

    cp "$WIFI_SETUP/app.py" "$boot/wifi-setup/"
    cp "$WIFI_SETUP/templates/"*.html "$boot/wifi-setup/templates/"
    cp "$WIFI_SETUP/templates/brand.css" "$boot/wifi-setup/templates/"
    mkdir -p "$boot/wifi-setup/assets/fonts"
    cp "$WIFI_SETUP/assets/"*.css "$boot/wifi-setup/assets/"
    cp "$WIFI_SETUP/assets/sudo-wordmark.svg" "$boot/wifi-setup/assets/"
    cp "$WIFI_SETUP/assets/fonts/"*.woff2 "$boot/wifi-setup/assets/fonts/"

    cp "$SUDO_DASH/index.html" "$boot/sudo-dashboard/"
    cp "$SUDO_DASH/server.py" "$boot/sudo-dashboard/"
    cp "$SUDO_DASH/app.css" "$boot/sudo-dashboard/"
    cp "$SUDO_DASH/manifest.json" "$boot/sudo-dashboard/"
    cp "$SUDO_DASH/sudo-wordmark.svg" "$boot/sudo-dashboard/"

    cp "$PICOCLAW/picoclaw" "$boot/picoclaw/"
    cp "$PICOCLAW/config.template.json" "$boot/picoclaw/"
    # Agent workspace templates (SOUL/IDENTITY/USER/AGENTS/HEARTBEAT)
    mkdir -p "$boot/picoclaw/workspace"
    cp "$PICOCLAW/workspace/"*.md "$boot/picoclaw/workspace/"
    cp "$CLOUDFLARED/cloudflared" "$boot/cloudflared/"

    cp "$SUDO_PI/"*.sh "$boot/sudo-pi/"
    # Same glob sudo-update.sh uses for helpers. Naming one .py here is how
    # spend-guard.py went missing: install-factory.sh expects it on the card.
    cp "$SUDO_PI/"*.py "$boot/sudo-pi/"
    cp "$WHATSAPP_BRIDGE/package.json" "$boot/whatsapp-bridge/"
    cp "$WHATSAPP_BRIDGE/bridge.js" "$boot/whatsapp-bridge/"
    # Do NOT copy cloudflare.conf — Quick Tunnel needs no API token / domain
    rm -f "$boot/sudo-pi/cloudflare.conf" 2>/dev/null || true
    cp "$SUDO_API/api.url.default" "$boot/sudo-api/"

    cp "$INSTALL" "$boot/install-factory.sh"
    chmod +x "$boot/install-factory.sh"
}

# Everything copied above runs on Linux. If the flashing machine's git has
# core.autocrlf=true (the Windows default), the scripts arrive with CRLF and
# bash reads the trailing CR as part of each command — "$BOOT/x" becomes
# "$BOOT\r/x", every path breaks, and install-factory.sh dies with a syntax
# error at EOF. The card looks perfect from the host while the device
# installs nothing. Strip CR here so this cannot depend on git config.
normalize_line_endings() {
    local boot="$1"
    local f fixed=0
    while IFS= read -r f; do
        if LC_ALL=C grep -qU $'\r' "$f" 2>/dev/null; then
            sed -i 's/\r$//' "$f" && fixed=$((fixed + 1))
        fi
    done < <(find "$boot" -maxdepth 3 -type f \( -name '*.sh' -o -name '*.py'              -o -name '*.html' -o -name '*.css' -o -name '*.json' -o -name '*.md' -o -name '*.js' \) 2>/dev/null)
    if [ "$fixed" -gt 0 ]; then
        echo "Normalised CRLF -> LF in $fixed script(s) on the card"
    fi
}

# SSH for in-house development only. Off unless --dev is passed, so a
# customer card never ships with a listening sshd.
apply_dev_access() {
    local boot="$1"

    if [ "$DEV" -ne 1 ]; then
        # Make sure a card reused from dev work doesn't keep SSH enabled.
        rm -f "$boot/ssh" "$boot/dev-authorized_keys" "$boot/dev-setup.sh" 2>/dev/null || true
        return 0
    fi

    # Raspberry Pi OS enables sshd on boot when this file exists, then removes it.
    : > "$boot/ssh"

    # Prefer an explicit dev/authorized_keys, otherwise just use the key this
    # machine already has. Nothing to generate or copy by hand in the common case.
    local key=""
    if [ -f "$DEV_KEYS" ] && [ -s "$DEV_KEYS" ]; then
        key="$DEV_KEYS"
        echo "DEV: SSH enabled, key-only login (dev/authorized_keys)"
    else
        local candidate
        for candidate in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_ecdsa.pub" "$HOME/.ssh/id_rsa.pub"; do
            if [ -f "$candidate" ] && [ -s "$candidate" ]; then
                key="$candidate"
                echo "DEV: SSH enabled, key-only login (using $(basename "$candidate"))"
                break
            fi
        done
    fi

    if [ -n "$key" ]; then
        cp "$key" "$boot/dev-authorized_keys"
    else
        echo "DEV: SSH enabled with PASSWORD login — no SSH key found on this machine."
        echo "     For key-only access, create one and re-run:"
        echo "       ssh-keygen -t ed25519"
    fi

    if [ -f "$SCRIPT_DIR/dev-setup.sh" ]; then
        cp "$SCRIPT_DIR/dev-setup.sh" "$boot/dev-setup.sh"
        chmod +x "$boot/dev-setup.sh"
    fi
}

prepare_boot() {
    local boot="$1"

    echo "Using boot volume: $boot"
    echo "Mode: $MODE"

    assert_boot_firmware "$boot"

    for f in "$INSTALL" "$WIFI_SETUP/app.py" "$SUDO_DASH/index.html" "$SUDO_DASH/server.py" "$SUDO_PI/device-secrets.sh" "$SUDO_PI/setup-remote-access.sh" \
             "$PICOCLAW/config.template.json" "$PICOCLAW/workspace/SOUL.md" "$PICOCLAW/workspace/AGENTS.md"; do
        if [ ! -f "$f" ]; then
            echo "ERROR: Missing required file: $f"
            exit 1
        fi
    done

    cleanup_leftovers "$boot"
    copy_factory_files "$boot"
    normalize_line_endings "$boot"
    apply_dev_access "$boot"

    case "$MODE" in
        reset)
            cp "$RESET" "$boot/factory-reset.sh"
            chmod +x "$boot/factory-reset.sh"
            patch_cmdline "$boot" "factory-reset.sh"
            echo ""
            echo "Reset mode — will wipe WiFi config and restart RaspiSetup hotspot."
            ;;
        devreset)
            cp "$DEVRESET" "$boot/dev-reset.sh"
            chmod +x "$boot/dev-reset.sh"
            patch_cmdline "$boot" "dev-reset.sh"
            # A marker rather than a second script: dev-reset.sh reads it on
            # the device and clears the saved API keys, so the default stays
            # "keep my keys" and forgetting them is always something asked for.
            if [ "${FORGET_KEYS:-0}" = "1" ]; then
                : > "$boot/forget-keys"
                echo ""
                echo "Dev reset — SSH and Claude Code kept, API keys WILL be cleared."
            else
                rm -f "$boot/forget-keys" 2>/dev/null || true
                echo ""
                echo "Dev reset — full new-user run, but SSH / keys / Claude Code are kept."
            fi
            ;;
        retest)
            cp "$RETEST" "$boot/factory-retest.sh"
            chmod +x "$boot/factory-retest.sh"
            patch_cmdline "$boot" "factory-retest.sh"
            echo ""
            echo "Retest mode — keeps WiFi, clears onboarding / remote link / cloud marker."
            echo "After reboot: login → setup preferences → chat / billing / public link."
            ;;
        repair)
            cp "$REPAIR" "$boot/repair-portal.sh"
            chmod +x "$boot/repair-portal.sh"
            patch_cmdline "$boot" "repair-portal.sh"
            echo ""
            echo "Repair mode — upgrades bundle, keeps existing WiFi if configured."
            echo "Hotspot will NOT return if WiFi was already set up."
            echo "Use --reset instead to bring hotspot back."
            ;;
        install)
            cp "$FIRSTRUN" "$boot/firstrun.sh"
            chmod +x "$boot/firstrun.sh"
            patch_cmdline "$boot" "firstrun.sh"
            echo ""
            echo "Install mode — firstrun.sh runs on next boot."
            ;;
    esac

    echo ""
    echo "Eject SD → boot Pi → wait ~2 min"
    if [ "$MODE" = "reset" ] || [ "$MODE" = "install" ] || [ "$MODE" = "devreset" ]; then
        echo "Hotspot: RaspiSetup (open, no password) → http://192.168.4.1/"
    else
        echo "WiFi kept → http://raspberrypi.local"
    fi
}

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --repair) MODE="repair"; shift ;;
        --reset)  MODE="reset"; shift ;;
        --retest) MODE="retest"; shift ;;
        --devreset) MODE="devreset"; shift ;;
        --dev)    DEV=1; shift ;;
        --forget-keys) FORGET_KEYS=1; shift ;;
        *) BOOT="$1"; shift ;;
    esac
done

if [ -z "$BOOT" ]; then
    BOOT="$(find_boot_volume)" || {
        echo "ERROR: Could not find boot volume."
        usage
        exit 1
    }
fi

[ -d "$BOOT" ] || { echo "ERROR: Not a directory: $BOOT"; exit 1; }
[ "$MODE" = "repair" ] && [ ! -f "$REPAIR" ] && { echo "ERROR: Missing repair-portal.sh"; exit 1; }
[ "$MODE" = "reset" ]  && [ ! -f "$RESET" ]  && { echo "ERROR: Missing factory-reset.sh"; exit 1; }
[ "$MODE" = "retest" ] && [ ! -f "$RETEST" ] && { echo "ERROR: Missing factory-retest.sh"; exit 1; }
[ "$MODE" = "devreset" ] && [ ! -f "$DEVRESET" ] && { echo "ERROR: Missing dev-reset.sh"; exit 1; }
[ "$MODE" = "install" ] && [ ! -f "$FIRSTRUN" ] && { echo "ERROR: Missing firstrun.sh"; exit 1; }

prepare_boot "$BOOT"
