#!/bin/bash
# Download PicoClaw Linux arm64 binary into picoclaw/ (run on Mac before prepare-sd.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${PICOCLAW_VERSION:-v0.3.1}"
URL="https://github.com/sipeed/picoclaw/releases/download/${VERSION}/picoclaw_Linux_arm64.tar.gz"
DEST="$ROOT/picoclaw"

mkdir -p "$DEST"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "Downloading PicoClaw $VERSION..."
curl -fsSL "$URL" -o "$tmpdir/picoclaw.tar.gz"
tar -xzf "$tmpdir/picoclaw.tar.gz" -C "$DEST"
chmod +x "$DEST/picoclaw" "$DEST/picoclaw-launcher" 2>/dev/null || chmod +x "$DEST/picoclaw"
echo "Installed: $DEST/picoclaw ($(du -h "$DEST/picoclaw" | cut -f1))"
