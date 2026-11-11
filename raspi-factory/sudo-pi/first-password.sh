#!/bin/bash
# first-password.sh — give the Pi's own account a password only this device has.
#
# The account is made from what Raspberry Pi Imager wrote on the card, so
# every card flashed with the same Imager settings had the same password.
# On the first boot (and the first boot after Reset setup, which clears the
# saved one) this replaces it with a random one and keeps a readable copy in
# /etc/sudo/ssh_password, where Settings > This device > Pi password shows it
# on the home network and the owner can change it. Terminal access uses the
# same password.
#
# Runs from sudo-first-password.service, which only starts while there is no
# saved password. The account can be created by cloud-init after this boot
# has begun, so it waits for it.
set -uo pipefail

LOG=/var/log/sudo-ssh-setup.log
PASS_FILE=/etc/sudo/ssh_password
exec >> "$LOG" 2>&1
echo "=== first-password $(date) ==="

[ -s "$PASS_FILE" ] && { echo "a password is already saved -- nothing to do"; exit 0; }

USER_NAME=""
for _ in $(seq 1 120); do
  USER_NAME="$(getent passwd 1000 | cut -d: -f1)"
  [ -n "$USER_NAME" ] && break
  sleep 5
done
[ -n "$USER_NAME" ] || { echo "ERROR: no uid-1000 user after 10 minutes"; exit 1; }

# Unambiguous alphabet, the same as Terminal access: no 0/O/1/l/I.
PW="$(tr -dc 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789' \
      < /dev/urandom | head -c 16)"
[ ${#PW} -eq 16 ] || { echo "ERROR: could not generate a password"; exit 1; }

if ! printf '%s:%s\n' "$USER_NAME" "$PW" | chpasswd; then
  echo "ERROR: chpasswd failed for ${USER_NAME}"; exit 1
fi
usermod -aG sudo "$USER_NAME" 2>/dev/null || true
mkdir -p /etc/sudo
umask 077
printf '%s\n' "$PW" > "$PASS_FILE"
chmod 600 "$PASS_FILE"
echo "random password set for ${USER_NAME}"
