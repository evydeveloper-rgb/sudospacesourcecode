#!/bin/bash
# Download cloudflared Linux arm64 into cloudflared/ (run on Mac before prepare-sd.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${CLOUDFLARED_VERSION:-2024.12.2}"
DEST="$ROOT/cloudflared"
ARCH="arm64"
OS="linux"

mkdir -p "$DEST"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

URL="https://github.com/cloudflare/cloudflared/releases/download/${VERSION}/cloudflared-${OS}-${ARCH}"

echo "Downloading cloudflared ${VERSION} (${OS}-${ARCH})..."
curl -fsSL "$URL" -o "$DEST/cloudflared"
chmod +x "$DEST/cloudflared"
echo "Installed: $DEST/cloudflared ($(du -h "$DEST/cloudflared" | cut -f1))"
