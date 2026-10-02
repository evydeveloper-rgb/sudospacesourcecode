#!/usr/bin/env python3
"""Trigger cloud registration after WiFi success (before reboot)."""
import subprocess
import threading


def schedule_cloud_init(delay=20):
    def run():
        subprocess.run(
            ["/usr/local/bin/sudo-cloud-init.sh"],
            capture_output=True,
            timeout=120,
        )

    threading.Thread(target=run, daemon=True).start()


# Called from wifi-setup after successful connect
if __name__ == "__main__":
    schedule_cloud_init(5)
