# NFC tag

What to write onto the tag that ships with a Sudo device.

Fixed values, from `start-hotspot.sh` in `install-factory.sh`:

    SSID      RaspiSetup
    password  12345678
    portal    http://192.168.4.1        (only while on the hotspot)
    dashboard http://raspberrypi.local  (after Wi-Fi setup, mDNS via avahi)

The SSID and password are identical on every unit, so one tag design covers
the whole product line — nothing is programmed per device.

## Use a URL record

**Write → Add a record → URL**

    http://raspberrypi.local

Both iOS and Android read NDEF URL tags in the background with no app
installed: tap the tag, a notification appears, tapping it opens the browser.

This is the right choice over a Wi-Fi record, which iOS ignores completely.

## Getting an iPhone onto the hotspot

A URL cannot make a phone join a Wi-Fi network. Joining is an OS action; no
web page can trigger it. Three things actually work:

**1. QR code — confirmed working on iPhone, use this.** `tag-assets/join-hotspot.svg` encodes
`WIFI:S:RaspiSetup;T:WPA;P:12345678;;`. The iOS Camera app reads this natively
and offers "Join Network" — the same payload iOS refuses over NFC. Android
reads it too. No infrastructure, no app, works on both. Print it next to the
NFC tag.

**2. Multi-record NFC tag.** One tag holding a Wi-Fi record *and* a URL record:
Android acts on the Wi-Fi record and joins, iOS ignores it and surfaces the
URL instead. Worth testing if you want a single tag to serve both — behaviour
varies by phone, so confirm on real hardware before committing to it.

**3. iOS configuration profile — built, but not needed.** A `.mobileconfig`
is Apple's own device-configuration format: an XML property list carrying
payloads (Wi-Fi, VPN, mail, certificates) that companies and schools use to
set up staff iPhones. `tag-assets/sudo-setup.mobileconfig` is a working one
for the RaspiSetup network, kept for reference.

It is the only route where a URL ends in the phone joining, but the cost is
real. The phone needs internet to fetch it. Installing is download, then
Settings, then General, then VPN & Device Management, then Install, passcode,
Install again — six or so taps versus two for the QR. Unsigned, iOS shows the
profile in red as unverified; removing that warning needs an Apple Developer
signing certificate. And users are widely told never to install configuration
profiles from untrusted sources, because it is a common phishing vector, so
asking a buyer to do it works against advice they may already have.

Since the QR code is confirmed working on iPhone, this stays a reference
only.

## What a URL tag does and does not do

It is an **everyday** tag, not a setup tag. Tap the device, your agent opens.

It cannot help with first-time setup. Before Wi-Fi is configured the phone is
not on the device's network, so no local URL resolves — and the hotspot the
phone would need to join is the thing the tag was supposed to save you from
joining. Ship the SSID and password printed on the box or quick-start card;
no tag format solves this on both platforms.

## Known issues

**Android mDNS is now the weak spot.** A URL tag fixes iPhone but moves the
problem: `.local` names rely on mDNS, which iOS resolves reliably (Bonjour is
Apple's own) while Android support varies by version and manufacturer. Expect
some Android phones to fail on `raspberrypi.local`.

**You cannot hardcode the IP instead.** The LAN address comes from the
router's DHCP and changes. The dashboard shows the current one under
Settings → Access & network if someone needs it, but it is not tag material.

**`http://` shows "Not secure".** Cosmetic — the dashboard is HTTP on the
local network by design — but buyers may notice the browser warning.

**Lock the tag before shipping.** An unlocked NDEF tag can be rewritten by
anyone who taps it with a writing app, including to a phishing URL. Most tag
tools have a "make read-only" option; this is irreversible, so write and test
first.

## Worth considering: a nicer hostname

`raspberrypi.local` is what the tag has to say today. Publishing `sudo.local`
as well (an avahi alias, or setting the system hostname) would read far better
on a tag and in the portal. It touches the address shown in the Wi-Fi portal,
the dashboard and these docs, so it is a deliberate change rather than a
drive-by one.
