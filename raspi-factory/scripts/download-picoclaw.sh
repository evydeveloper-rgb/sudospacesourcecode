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
# Unpack aside and take only the programs. The release tarball also carries
# PicoClaw's own README and LICENSE, and unpacking straight into picoclaw/
# overwrote our README (with its note on how Sudo uses PicoClaw).
mkdir -p "$tmpdir/x"
tar -xzf "$tmpdir/picoclaw.tar.gz" -C "$tmpdir/x"
for bin in picoclaw picoclaw-launcher; do
  found="$(find "$tmpdir/x" -type f -name "$bin" | head -n 1)"
  [ -n "$found" ] && install -m 755 "$found" "$DEST/$bin"
done
[ -f "$DEST/picoclaw" ] || { echo "ERROR: no picoclaw binary in the release" >&2; exit 1; }
echo "Installed: $DEST/picoclaw ($(du -h "$DEST/picoclaw" | cut -f1))"
