#!/bin/bash
# Install the pinned OpenClaw into /opt/openclaw, then hand over to it.
# Prefers a prebuilt bundle staged on the boot partition; falls back to npm.
set -euo pipefail

OPENCLAW_VERSION="2026.6.11"   # proven in long-running use; newer needs Node 24
NODE_MAJOR=22
OC_DIR=/opt/openclaw
BUNDLE=/boot/firmware/openclaw/openclaw-bundle.tar.gz
LOG=/var/log/sudo-openclaw-install.log
# Mirrored to the boot partition so a failed first-boot install can be read
# from any computer the card is plugged into.
BOOT_LOG=/boot/firmware/openclaw-install.log

exec > >(tee -a "$BOOT_LOG" >> "$LOG") 2>&1
echo "=== install-openclaw $OPENCLAW_VERSION $(date) ==="

node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>22||(a===22&&b>=19)?0:1)'
}
if ! node_ok; then
  echo "Installing Node.js ${NODE_MAJOR}"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash -
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi
node_ok || { echo "Node.js >= 22.19 required, have $(node -v 2>/dev/null)"; exit 1; }

installed=$(node -p "require('$OC_DIR/node_modules/openclaw/package.json').version" 2>/dev/null || true)
if [ "$installed" = "$OPENCLAW_VERSION" ]; then
  echo "OpenClaw $OPENCLAW_VERSION already installed"
else
  staging="${OC_DIR}.new"
  rm -rf "$staging"
  mkdir -p "$staging"
  if [ -f "$BUNDLE" ]; then
    echo "Unpacking bundle $BUNDLE"
    tar -xzf "$BUNDLE" -C "$staging"
  else
    echo "No bundle on the card — installing from npm"
    printf '{"private":true}\n' > "$staging/package.json"
    (cd "$staging" && npm install --omit=dev --no-audit --no-fund "openclaw@${OPENCLAW_VERSION}")
  fi
  # Swap in only once the new tree is complete, so a failed install never
  # leaves the device without a working brain.
  rm -rf "${OC_DIR}.old"
  [ -d "$OC_DIR" ] && mv "$OC_DIR" "${OC_DIR}.old"
  mv "$staging" "$OC_DIR"
  rm -rf "${OC_DIR}.old"
fi
"$OC_DIR/node_modules/.bin/openclaw" --version

/usr/local/bin/sudo-configure-picoclaw.sh || true
echo "=== install-openclaw complete $(date) ==="
