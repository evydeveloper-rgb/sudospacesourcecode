#!/bin/bash
# repair-boot-firmware.sh — restore GPU firmware + kernel onto a corrupted bootfs
#
# Fixes: constant LED blink, no hotspot, no WiFi, missing start.elf / kernel*.img,
# corrupt config.txt / tiny initramfs (FSCK damage). Does NOT reflash the rootfs.
#
# Uses the Raspberry Pi Imager download cache when present (no extra download).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE_DEFAULT="$HOME/Library/Caches/Raspberry Pi/Raspberry Pi Imager/lastdownload.cache"
IMG_TMP="$SCRIPT_DIR/.boot-repair.img"
GOOD_MOUNT=""

usage() {
    cat <<EOF
Usage: $0 [BOOT_VOLUME] [CACHE_XZ]

Examples:
  $0
  $0 "/Volumes/bootfs 1"
  $0 /Volumes/bootfs ~/Downloads/raspios.img.xz
EOF
}

find_boot_volume() {
    local vol
    for vol in /Volumes/bootfs /Volumes/boot /Volumes/BOOT; do
        if [ -d "$vol" ]; then
            echo "$vol"
            return 0
        fi
    done
    for vol in /Volumes/bootfs*; do
        if [ -d "$vol" ]; then
            echo "$vol"
            return 0
        fi
    done
    return 1
}

# Prefer the SD (has FSCK or missing start.elf) over a donor image mount
pick_sd_boot() {
    local vol best=""
    for vol in /Volumes/bootfs*; do
        [ -d "$vol" ] || continue
        if [ ! -f "$vol/start4.elf" ] || [ -f "$vol/FSCK0001.REC" ]; then
            echo "$vol"
            return 0
        fi
        best="$vol"
    done
    if [ -n "$best" ]; then
        echo "$best"
        return 0
    fi
    return 1
}

cleanup() {
    if [ -n "${GOOD_MOUNT:-}" ] && [ -d "$GOOD_MOUNT" ]; then
        diskutil unmount "$GOOD_MOUNT" >/dev/null 2>&1 || true
    fi
    # Detach any disk attached from our img
    if [ -f "$IMG_TMP" ]; then
        local node
        node="$(hdiutil info 2>/dev/null | awk -v img="$IMG_TMP" '
            $0 ~ img {found=1}
            found && /\/dev\/disk[0-9]+/ {print $1; exit}
        ' || true)"
        if [ -n "${node:-}" ]; then
            hdiutil detach "$node" -force >/dev/null 2>&1 || true
        fi
    fi
}
trap cleanup EXIT

BOOT="${1:-}"
CACHE="${2:-$CACHE_DEFAULT}"

if [ -z "$BOOT" ]; then
    BOOT="$(pick_sd_boot 2>/dev/null || find_boot_volume)" || {
        echo "ERROR: No boot volume mounted. Insert SD and remount bootfs."
        usage
        exit 1
    }
fi

[ -d "$BOOT" ] || { echo "ERROR: Not a directory: $BOOT"; exit 1; }
[ -f "$CACHE" ] || {
    echo "ERROR: No Pi OS image cache at: $CACHE"
    echo "Flash once with Raspberry Pi Imager, or pass path to a .img.xz"
    exit 1
}

echo "SD boot volume: $BOOT"
echo "Image source:   $CACHE"

# Preserve this card's root PARTUUID before any file overwrite
OLD_CMDLINE=""
ROOT_PART=""
if [ -f "$BOOT/cmdline.txt" ]; then
    OLD_CMDLINE="$(tr -d '\n' < "$BOOT/cmdline.txt")"
    ROOT_PART="$(printf '%s' "$OLD_CMDLINE" | grep -oE 'root=PARTUUID=[^ ]+' | head -1 || true)"
fi
if [ -z "$ROOT_PART" ]; then
    # Last known PARTUUID for this factory card (MBR unchanged by FAT repair)
    ROOT_PART="root=PARTUUID=1924feb1-02"
    echo "WARNING: no root=PARTUUID on card — using $ROOT_PART"
else
    echo "Preserving $ROOT_PART"
fi

echo "Extracting boot partition (512 MiB) from image…"
python3 - "$CACHE" "$IMG_TMP" <<'PY'
import lzma, os, sys
cache, out = sys.argv[1], sys.argv[2]
start, size = 16384 * 512, 1048576 * 512
written = 0
with lzma.open(cache, "rb") as f:
    f.seek(start)
    with open(out, "wb") as o:
        remaining = size
        while remaining:
            chunk = f.read(min(8 * 1024 * 1024, remaining))
            if not chunk:
                sys.exit("unexpected EOF extracting boot partition")
            o.write(chunk)
            remaining -= len(chunk)
            written += len(chunk)
print(f"extracted {written} bytes → {out}")
PY

echo "Attaching donor boot image…"
ATTACH_OUT="$(hdiutil attach -readonly -nobrowse "$IMG_TMP")"
echo "$ATTACH_OUT"
GOOD_MOUNT="$(echo "$ATTACH_OUT" | awk '/\/Volumes\//{print $NF; exit}')"
if [ -z "$GOOD_MOUNT" ] || [ ! -d "$GOOD_MOUNT" ]; then
    # Sometimes mounts as /Volumes/bootfs while SD is bootfs 1
    for vol in /Volumes/bootfs*; do
        if [ "$vol" != "$BOOT" ] && [ -f "$vol/start4.elf" ]; then
            GOOD_MOUNT="$vol"
            break
        fi
    done
fi
[ -n "$GOOD_MOUNT" ] && [ -f "$GOOD_MOUNT/start4.elf" ] || {
    echo "ERROR: Could not mount donor boot partition"
    exit 1
}
echo "Donor mount: $GOOD_MOUNT"

rm -f "$BOOT"/FSCK*.REC "$BOOT"/FSCK*.CHK 2>/dev/null || true

echo "Syncing firmware (keeping factory folders)…"
# Do not --delete factory dirs; copy firmware/kernel/config from donor
rsync -a \
    --exclude='wifi-setup' \
    --exclude='sudo-dashboard' \
    --exclude='sudo-pi' \
    --exclude='sudo-api' \
    --exclude='picoclaw' \
    --exclude='cloudflared' \
    --exclude='firstrun.sh' \
    --exclude='factory-reset.sh' \
    --exclude='factory-retest.sh' \
    --exclude='repair-portal.sh' \
    --exclude='wifi-heal.sh' \
    --exclude='install-factory.sh' \
    --exclude='.fseventsd' \
    --exclude='.Spotlight-V100' \
    --exclude='.Trashes' \
    --exclude='.TemporaryItems' \
    --exclude='.DS_Store' \
    --exclude='._*' \
    "$GOOD_MOUNT"/ "$BOOT"/

# Rewrite cmdline: donor PARTUUID must not stick; keep this card's root
BASE="$(tr -d '\n' < "$BOOT/cmdline.txt")"
BASE="$(printf '%s' "$BASE" | sed -E \
    's/root=PARTUUID=[^ ]+/'"$ROOT_PART"'/;
     s/ systemd\.run=[^ ]*//g;
     s/ systemd\.run_success_action=[^ ]*//g;
     s/ systemd\.unit=[^ ]*//g')"
# Drop first-boot-only resize if already provisioned (harmless either way)
printf '%s\n' "$BASE" > "$BOOT/cmdline.txt"

echo "---- verify ----"
for f in start.elf start4.elf kernel8.img kernel_2712.img config.txt cmdline.txt; do
    if [ -f "$BOOT/$f" ] && [ -s "$BOOT/$f" ]; then
        echo "OK  $f ($(wc -c < "$BOOT/$f" | tr -d ' ') bytes)"
    else
        echo "BAD $f"
        exit 1
    fi
done
echo "cmdline: $(tr -d '\n' < "$BOOT/cmdline.txt")"
echo ""
echo "Boot firmware restored."
echo "Next:  ./prepare-sd.sh --reset \"$BOOT\""
echo "Then eject SD, boot the Pi, wait ~2 min for RaspiSetup."
