═══════════════════════════════════════════════
 SUDO RASPI FACTORY — SETUP GUIDE
═══════════════════════════════════════════════

WHAT THIS FACTORY DOES
──────────────────────
Boot 1 (hotspot):  WiFi captive portal
After WiFi:          Dashboard opens directly on the home network
Home access:       http://raspberrypi.local/
Anywhere access:   https://….trycloudflare.com  (Quick Tunnel, no domain)
User flow:         WiFi → onboarding → chat

═══════════════════════════════════════════════
BEFORE YOU FLASH
═══════════════════════════════════════════════
Nothing Cloudflare to fill in.
No domain. No API token. No cloudflare.conf.

  ./prepare-sd.sh

(Downloads PicoClaw + cloudflared if missing.)

See CLOUDFLARE-SETUP.txt for how remote links work.

═══════════════════════════════════════════════
USER FLOW (NON-TECHNICAL)
═══════════════════════════════════════════════
1. Connect to RaspiSetup hotspot → pick home WiFi
2. Tap Connect & restart
3. Open raspberrypi.local
4. Complete onboarding → tap "Yes — link anywhere"
5. Public https link appears → bookmark it
   (may change after a device reboot)

No terminal. No domain typing. No port-forward.

═══════════════════════════════════════════════
FLASH STEPS
═══════════════════════════════════════════════
  ./prepare-sd.sh          # fresh / upgrade copy to SD
  ./prepare-sd.sh --repair # upgrade existing Pi
  ./prepare-sd.sh --reset  # wipe WiFi → RaspiSetup hotspot again

CONSTANT LED BLINK / NO HOTSPOT / NO WIFI
──────────────────────────────────────────
Usually corrupt boot firmware (missing start.elf / kernel), NOT WiFi.
Do NOT keep running --repair/--retest on a blinking card.

  ./repair-boot-firmware.sh   # restore firmware from Imager cache
  ./prepare-sd.sh --reset
  eject → boot Pi → RaspiSetup / 12345678 → http://192.168.4.1/

If repair fails: re-flash Raspberry Pi OS with Imager, then ./prepare-sd.sh

═══════════════════════════════════════════════
SECURITY
──────────────────────
  Dashboard is passwordless; only expose it on networks you trust
  Device secret for cloud billing API
  Quick Tunnel: no Cloudflare API token on the device
  See CLOUDFLARE-SETUP.txt

═══════════════════════════════════════════════
LOGS
═══════════════════════════════════════════════
  /var/log/sudo-remote.log
  journalctl -u sudo-remote-access -f

═══════════════════════════════════════════════
