#!/bin/bash
# setup-ssh.sh — privileged SSH changes for the dashboard's Developer access.
#
# The dashboard runs with ProtectSystem=full and ProtectHome=true, so it
# cannot touch /etc/shadow, /etc/ssh, or /home. It invokes this via
# systemd-run, which starts a transient unit outside that sandbox.
#
#   setup-ssh.sh enable-password   generate a password for the login user
#   setup-ssh.sh enable-key        install /etc/sudo/ssh_key_pending
#   setup-ssh.sh disable           turn SSH off and clear what we added
#   setup-ssh.sh set-password      set /etc/sudo/login_password_pending as the
#                                  login user's password (Settings, Pi password)
#
# /etc/sudo/ssh_password is the readable copy of the login user's password:
# the one SSH asks for, and the one sudo asks for. Turning SSH off or using a
# key does not change that password, so neither forgets it.
set -uo pipefail

LOG="/var/log/sudo-ssh-setup.log"
PASS_FILE="/etc/sudo/ssh_password"
PENDING_KEY="/etc/sudo/ssh_key_pending"
PENDING_PW="/etc/sudo/login_password_pending"
# Sorts before install-factory.sh's 10-sudo-dev.conf; sshd honours the first
# value it sees for a keyword, so the owner's choice wins over the dev default.
DROPIN="/etc/ssh/sshd_config.d/05-sudo-user.conf"

exec >> "$LOG" 2>&1
echo "=== setup-ssh $(date) — $* ==="

USER_NAME="$(getent passwd 1000 | cut -d: -f1)"
USER_HOME="$(getent passwd 1000 | cut -d: -f6)"
[ -n "$USER_NAME" ] || { echo "ERROR: no uid-1000 user"; exit 1; }

write_dropin() {
  mkdir -p "$(dirname "$DROPIN")"
  {
    echo "# Managed by the Sudo dashboard — edits here are overwritten."
    echo "PasswordAuthentication $1"
    echo "PermitRootLogin no"
  } > "$DROPIN"
  chmod 644 "$DROPIN"
}

case "${1:-}" in
  enable-password)
    # The owner's Pi password if there is one (set on the Pi password card,
    # or by an earlier time this was on); otherwise a new one. Unambiguous
    # alphabet: no 0/O/1/l/I, so it survives being read off a screen.
    PW="$(head -n1 "$PASS_FILE" 2>/dev/null | tr -d '\r\n')"
    if [ -z "$PW" ]; then
      PW="$(tr -dc 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789' \
            < /dev/urandom | head -c 16)"
      [ ${#PW} -eq 16 ] || { echo "ERROR: could not generate password"; exit 1; }
    fi
    if ! echo "${USER_NAME}:${PW}" | chpasswd; then
      echo "ERROR: chpasswd failed for ${USER_NAME}"; exit 1
    fi
    umask 077
    printf '%s\n' "$PW" > "$PASS_FILE"
    chmod 600 "$PASS_FILE"
    write_dropin yes
    systemctl enable ssh && systemctl restart ssh
    echo "password login enabled for ${USER_NAME}"
    ;;

  enable-key)
    [ -s "$PENDING_KEY" ] || { echo "ERROR: no pending key"; exit 1; }
    install -d -m 700 -o "$USER_NAME" -g "$USER_NAME" "${USER_HOME}/.ssh"
    AUTH="${USER_HOME}/.ssh/authorized_keys"
    touch "$AUTH"
    # Append without duplicating if the same key is submitted twice.
    if ! grep -qxFf "$PENDING_KEY" "$AUTH" 2>/dev/null; then
      cat "$PENDING_KEY" >> "$AUTH"
    fi
    chmod 600 "$AUTH"
    chown "${USER_NAME}:${USER_NAME}" "$AUTH"
    # The readable password stays: sudo still asks for it.
    rm -f "$PENDING_KEY"
    # The key makes passwords unnecessary, so close that door.
    write_dropin no
    systemctl enable ssh && systemctl restart ssh
    echo "key login enabled for ${USER_NAME}, passwords off"
    ;;

  disable)
    systemctl disable --now ssh
    rm -f "$PENDING_KEY" "$DROPIN"
    echo "ssh disabled"
    ;;

  set-password)
    [ -s "$PENDING_PW" ] || { echo "ERROR: no pending password"; exit 1; }
    PW="$(head -n1 "$PENDING_PW" | tr -d '\r\n')"
    rm -f "$PENDING_PW"
    [ ${#PW} -ge 8 ] || { echo "ERROR: password too short"; exit 1; }
    if ! printf '%s:%s\n' "$USER_NAME" "$PW" | chpasswd; then
      echo "ERROR: chpasswd failed for ${USER_NAME}"; exit 1
    fi
    # The password is for sudo as much as for logging in.
    usermod -aG sudo "$USER_NAME" 2>/dev/null || true
    umask 077
    printf '%s\n' "$PW" > "$PASS_FILE"
    chmod 600 "$PASS_FILE"
    echo "password set for ${USER_NAME}"
    ;;

  *)
    echo "Usage: $0 enable-password|enable-key|disable|set-password"
    exit 1
    ;;
esac

echo "=== setup-ssh done $(date) ==="
