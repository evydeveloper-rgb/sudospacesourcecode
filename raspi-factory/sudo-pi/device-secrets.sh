#!/bin/bash
# Generate device secrets offline (no internet required).
# Runs during factory install / first boot.
set -euo pipefail

SUDO_DIR="/etc/sudo"
SECRET_FILE="${SUDO_DIR}/device_secret"
PASS_FILE="${SUDO_DIR}/dashboard_password"
PASS_HASH_FILE="${SUDO_DIR}/dashboard_password.hash"
CREDS_FILE="${SUDO_DIR}/dashboard_credentials.txt"

mkdir -p "$SUDO_DIR"
chmod 700 "$SUDO_DIR"

rand_hex() {
  local nbytes="${1:-32}"
  if [ -r /dev/urandom ]; then
    od -An -N"$nbytes" -tx1 /dev/urandom | tr -d ' \n'
  else
    # Extremely unlikely fallback on Pi
    date +%s%N | sha256sum | awk '{print $1}'
  fi
}

rand_password() {
  # 16 chars, unambiguous alphabet (no 0/O/1/l/I)
  local alphabet="ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
  local out="" i idx
  for i in $(seq 1 16); do
    idx=$(( $(od -An -N1 -tu1 /dev/urandom | tr -d ' ') % ${#alphabet} ))
    out="${out}${alphabet:$idx:1}"
  done
  printf '%s' "$out"
}

if [ ! -f "$SECRET_FILE" ]; then
  rand_hex 32 > "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
  echo "Generated device_secret"
fi

if [ ! -f "$PASS_FILE" ]; then
  PASS="$(rand_password)"
  printf '%s\n' "$PASS" > "$PASS_FILE"
  chmod 600 "$PASS_FILE"

  # PBKDF2 hash via python3 stdlib (no pip)
  python3 - "$PASS" "$PASS_HASH_FILE" <<'PY'
import hashlib, os, sys
password = sys.argv[1].encode("utf-8")
out = sys.argv[2]
salt = os.urandom(16)
dk = hashlib.pbkdf2_hmac("sha256", password, salt, 200_000)
with open(out, "w") as f:
    f.write(f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}\n")
os.chmod(out, 0o600)
PY

  HOST="$(hostname 2>/dev/null || echo raspberrypi)"
  cat > "$CREDS_FILE" <<EOF
═══════════════════════════════════════
 sudo dashboard credentials
═══════════════════════════════════════
Hostname : ${HOST}.local
Username : sudo
Password : ${PASS}

Save this password. You will need it to
open the dashboard after WiFi setup.

Change it later from Settings if needed.
═══════════════════════════════════════
EOF
  chmod 600 "$CREDS_FILE"
  echo "Generated dashboard password"
fi

# Always ensure hash exists if password file exists
if [ -f "$PASS_FILE" ] && [ ! -f "$PASS_HASH_FILE" ]; then
  PASS="$(tr -d '\n' < "$PASS_FILE")"
  python3 - "$PASS" "$PASS_HASH_FILE" <<'PY'
import hashlib, os, sys
password = sys.argv[1].encode("utf-8")
out = sys.argv[2]
salt = os.urandom(16)
dk = hashlib.pbkdf2_hmac("sha256", password, salt, 200_000)
with open(out, "w") as f:
    f.write(f"pbkdf2_sha256$200000${salt.hex()}${dk.hex()}\n")
os.chmod(out, 0o600)
PY
fi

echo "device-secrets ready"
