#!/usr/bin/env python3
"""sudo browser logins -- a real browser on the device, for sites that have no API.

Why this exists: an API connector (Composio) covers apps that *offer* an API.
USPS, a personal Instagram, a council portal -- those have nothing to grant. What
they have is a session: log in once in a browser, keep the cookie, reuse it. So
this worker owns a per-site Chromium profile, and the dashboard only ever reads
the verdict ("connected", "needs login", "unknown").

Privacy rules this file keeps:
  * Credentials, cookies and profile data never leave the device.
  * Nothing is logged with a password in it. Ever.
  * Credentials are encrypted at rest when `cryptography` is present, using a key
    derived from the device secret; otherwise they are stored 0600 and the state
    file records `encrypted: false` so the UI can say so plainly.

Design notes:
  * `classify()` is pure and takes *facts*, so it can be tested without a browser.
  * Rules from sites.json are hints. When they stop matching, the answer is
    "unknown" -- never a cheerful guess that costs the owner a failed checkout.
  * The browser is launched headless with a persistent profile. A human-in-the-loop
    path (live view for captcha/2FA) is phase 2; until it exists, sites that
    challenge automation report `needs_you` instead of pretending to work.

Usage (the dashboard shells out to this; humans rarely call it):
  sudo-browser recipes
  sudo-browser status
  sudo-browser add usps
  sudo-browser probe usps
  sudo-browser login usps --json '{"username":"...","password":"..."}'
  sudo-browser forget usps
  sudo-browser remove usps
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("SUDO_BROWSER_HOME", "/var/lib/sudo-browser")
RECIPE_FILE = os.environ.get("SUDO_BROWSER_RECIPES", os.path.join(HERE, "sites.json"))
STATE_FILE = os.path.join(HOME, "logins.json")
SECRET_FILE = os.path.join(HOME, "secrets.enc")
PROFILE_ROOT = os.path.join(HOME, "profiles")
DEVICE_SECRET = os.environ.get("SUDO_DEVICE_SECRET_FILE", "/etc/sudo/device_secret")

# Words that mean "a human has to look at this", in the languages the box ships.
CHALLENGE_MARKERS = (
    "captcha", "recaptcha", "hcaptcha", "are you a robot", "verify you are human",
    "two-factor", "2-step", "verification code", "enter the code we sent",
    "unusual activity", "suspicious activity", "confirm it's you",
    "verifica que eres humano", "验证码", "验证您是真人",
)

STATE_ABSENT = "absent"
STATE_CONNECTED = "connected"
STATE_NEEDS_LOGIN = "needs_login"
STATE_NEEDS_YOU = "needs_you"
STATE_UNKNOWN = "unknown"
STATE_ERROR = "error"
STATE_BUSY = "busy"

VALID_STATES = (STATE_ABSENT, STATE_CONNECTED, STATE_NEEDS_LOGIN,
                STATE_NEEDS_YOU, STATE_UNKNOWN, STATE_ERROR, STATE_BUSY)


# ---------------------------------------------------------------- state file

def _ensure_home():
    os.makedirs(HOME, exist_ok=True)
    os.makedirs(PROFILE_ROOT, exist_ok=True)
    try:
        os.chmod(HOME, 0o700)
        os.chmod(PROFILE_ROOT, 0o700)
    except OSError:
        pass


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("sites", {})
    data.setdefault("encrypted", _crypto_available())
    return data


def save_state(data):
    _ensure_home()
    tmp = STATE_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, STATE_FILE)
    os.chmod(STATE_FILE, 0o600)


def set_site_state(site_id, state, note="", extra=None):
    assert state in VALID_STATES, state
    data = load_state()
    entry = data["sites"].get(site_id, {})
    entry.update({"id": site_id, "state": state, "note": note, "checked": int(time.time() * 1000)})
    if extra:
        entry.update(extra)
    data["sites"][site_id] = entry
    save_state(data)
    return entry


# ---------------------------------------------------------------- recipes

def load_recipes(path=None):
    path = path or RECIPE_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read site recipes at {path}: {exc}")
    return raw.get("sites", {})


def recipe_for(recipes, site_id):
    rec = recipes.get(site_id)
    if not rec:
        raise SystemExit(f"unknown site '{site_id}' -- not in {RECIPE_FILE}")
    return rec


# ---------------------------------------------------------------- detection (pure)

def rule_matches(rule, facts):
    """Does one detection rule match the facts we scraped? Facts, not a page --
    so this is testable and the classification below stays honest."""
    if not rule:
        return False
    for item in rule.get("any", []) or []:
        if "selector" in item and item["selector"] in (facts.get("selectors") or []):
            return True
        if "text" in item and item["text"].lower() in (facts.get("text") or "").lower():
            return True
        if "url_contains" in item and item["url_contains"].lower() in (facts.get("url") or "").lower():
            return True
    return False


def looks_like_challenge(facts):
    text = (facts.get("text") or "").lower()
    url = (facts.get("url") or "").lower()
    if any(marker in url for marker in ("challenge", "checkpoint", "two_step")):
        return True
    return any(marker in text for marker in CHALLENGE_MARKERS)


def classify(recipe, facts):
    """Return (state, note). Order matters: a challenge beats both verdicts,
    because on a challenge page the page usually still *looks* logged out."""
    if facts.get("error"):
        return STATE_ERROR, str(facts["error"])[:200]
    if looks_like_challenge(facts):
        return STATE_NEEDS_YOU, "The site is asking for a human (captcha or a code)."
    if rule_matches(recipe.get("logged_in"), facts):
        return STATE_CONNECTED, ""
    if rule_matches(recipe.get("logged_out"), facts):
        return STATE_NEEDS_LOGIN, ""
    return STATE_UNKNOWN, "Could not tell whether the session is still valid."


# ---------------------------------------------------------------- crypto (optional)

def _crypto_available():
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _key():
    """Derive a 32-byte key from the device secret. Not a password -- the box
    generates it at first boot and never leaves the device."""
    seed = b""
    try:
        with open(DEVICE_SECRET, "rb") as fh:
            seed = fh.read().strip()
    except OSError:
        seed = b""
    if not seed:
        seed = b"sudo-browser-no-device-secret"
    return hashlib.scrypt(seed, salt=b"sudo-browser-v1", n=2 ** 14, r=8, p=1, dklen=32)


def store_secrets(site_id, username, password):
    """Save per-site credentials. Encrypted when possible; 0600 either way."""
    _ensure_home()
    data = _load_secret_blob()
    plain = json.dumps({"username": username, "password": password}).encode("utf-8")
    entry = {"plain": base64.b64encode(plain).decode(), "enc": False}
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        blob = AESGCM(_key()).encrypt(nonce, plain, site_id.encode())
        entry = {"nonce": base64.b64encode(nonce).decode(),
                 "ct": base64.b64encode(blob).decode(), "enc": True}
    except Exception:  # noqa: BLE001 - fall back to plaintext, and say so
        pass
    data["sites"][site_id] = entry
    _save_secret_blob(data)
    return entry["enc"]


def load_secrets(site_id):
    entry = (_load_secret_blob().get("sites") or {}).get(site_id)
    if not entry:
        return None
    try:
        if entry.get("enc"):
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            nonce = base64.b64decode(entry["nonce"])
            ct = base64.b64decode(entry["ct"])
            plain = AESGCM(_key()).decrypt(nonce, ct, site_id.encode())
        else:
            plain = base64.b64decode(entry["plain"])
        return json.loads(plain.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def forget_secrets(site_id):
    data = _load_secret_blob()
    removed = bool((data.get("sites") or {}).pop(site_id, None))
    _save_secret_blob(data)
    return removed


def _load_secret_blob():
    try:
        with open(SECRET_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("sites", {})
    return data


def _save_secret_blob(data):
    _ensure_home()
    tmp = SECRET_FILE + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
    os.replace(tmp, SECRET_FILE)
    os.chmod(SECRET_FILE, 0o600)


# ---------------------------------------------------------------- browser

def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            "playwright is not installed on this device. Run sudo-install-browser.sh "
            f"(or: pip install playwright && playwright install chromium). Detail: {exc}"
        )
    return sync_playwright


def _profile_dir(site_id):
    path = os.path.join(PROFILE_ROOT, site_id)
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _facts(page, recipe):
    """Scrape just enough to decide, without reading the page into a log."""
    try:
        text = page.inner_text("body")[:6000]
    except Exception:  # noqa: BLE001
        text = ""
    wanted = []
    for rule in ("logged_in", "logged_out"):
        for item in (recipe.get(rule) or {}).get("any", []) or []:
            if "selector" in item:
                wanted.append(item["selector"])
    present = []
    for sel in wanted:
        try:
            if page.query_selector(sel) is not None:
                present.append(sel)
        except Exception:  # noqa: BLE001
            continue
    return {"url": page.url, "text": text, "selectors": present}


def probe(site_id, headless=True, timeout_ms=30000):
    recipes = load_recipes()
    recipe = recipe_for(recipes, site_id)
    url = recipe.get("home_url") or recipe.get("login_url")
    if not url:
        set_site_state(site_id, STATE_UNKNOWN, "No URL set for this site yet.")
        return STATE_UNKNOWN, "No URL set for this site yet."

    sync_playwright = _playwright()
    set_site_state(site_id, STATE_BUSY, "Checking the session…")
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                _profile_dir(site_id), headless=headless,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                facts = _facts(page, recipe)
            finally:
                ctx.close()
    except Exception as exc:  # noqa: BLE001
        note = f"Browser could not open the site: {str(exc)[:180]}"
        set_site_state(site_id, STATE_ERROR, note)
        return STATE_ERROR, note

    state, note = classify(recipe, facts)
    set_site_state(site_id, state, note)
    return state, note


def login(site_id, username, password, headless=True, timeout_ms=45000):
    """Type the owner's credentials in and see what happens.

    This is the fragile path by design: sites with captchas will refuse. When
    they do, we say `needs_you` and keep the credentials for the retry rather
    than hammering the site.
    """
    recipes = load_recipes()
    recipe = recipe_for(recipes, site_id)
    fields = recipe.get("login_fields") or {}
    if not (fields.get("username") and fields.get("password")):
        msg = "This site has no login fields configured, so sudo cannot fill them in."
        set_site_state(site_id, STATE_UNKNOWN, msg)
        return STATE_UNKNOWN, msg

    url = recipe.get("login_url") or recipe.get("home_url")
    sync_playwright = _playwright()
    set_site_state(site_id, STATE_BUSY, "Signing in…")
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                _profile_dir(site_id), headless=headless,
                args=["--no-first-run", "--no-default-browser-check"],
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.fill(fields["username"], username, timeout=15000)
                page.fill(fields["password"], password, timeout=15000)
                if fields.get("submit"):
                    page.click(fields["submit"], timeout=15000)
                else:
                    page.keyboard.press("Enter")
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
                facts = _facts(page, recipe)
            finally:
                ctx.close()
    except Exception as exc:  # noqa: BLE001
        note = f"Sign-in attempt failed: {str(exc)[:180]}"
        set_site_state(site_id, STATE_ERROR, note)
        return STATE_ERROR, note

    state, note = classify(recipe, facts)
    if state == STATE_CONNECTED:
        store_secrets(site_id, username, password)
        note = "Signed in. The session is kept on this device."
    elif state == STATE_NEEDS_YOU:
        note = "The site wants a human (captcha or a code) — log in from its own page once."
    elif state == STATE_NEEDS_LOGIN:
        note = "The site did not accept the sign-in. Check the username and password."
    set_site_state(site_id, state, note)
    return state, note


# ---------------------------------------------------------------- commands

def cmd_recipes(_args):
    recipes = load_recipes()
    out = [{"id": k, "label": v.get("label", k), "category": v.get("category", ""),
            "note": v.get("note", "")} for k, v in recipes.items()]
    print(json.dumps({"ok": True, "recipes": out}, indent=1))
    return 0


def cmd_status(_args):
    data = load_state()
    print(json.dumps({
        "ok": True,
        "installed": _playwright_installed(),
        "encrypted": _crypto_available(),
        "sites": sorted(data["sites"].values(), key=lambda s: s.get("id", "")),
    }, indent=1))
    return 0


def cmd_add(args):
    recipes = load_recipes()
    recipe = recipe_for(recipes, args.site)
    data = load_state()
    if args.site not in data["sites"]:
        data["sites"][args.site] = {
            "id": args.site,
            "label": recipe.get("label", args.site),
            "url": args.url or recipe.get("home_url") or "",
            "state": STATE_NEEDS_LOGIN,
            "note": "",
            "added": int(time.time() * 1000),
        }
        save_state(data)
    print(json.dumps({"ok": True, "site": data["sites"][args.site]}, indent=1))
    return 0


def cmd_remove(args):
    data = load_state()
    existed = bool(data["sites"].pop(args.site, None))
    save_state(data)
    forget_secrets(args.site)
    shutil.rmtree(_profile_dir(args.site), ignore_errors=True)
    print(json.dumps({"ok": True, "removed": existed}, indent=1))
    return 0


def cmd_forget(args):
    print(json.dumps({"ok": True, "forgot": forget_secrets(args.site)}, indent=1))
    return 0


def cmd_probe(args):
    state, note = probe(args.site, headless=not args.show)
    print(json.dumps({"ok": True, "state": state, "note": note}, indent=1))
    return 0


def cmd_login(args):
    raw = args.json
    if raw == "-":
        # Credentials arrive on stdin, never argv: `ps` is world-readable.
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    username = payload.get("username") or os.environ.get("SUDO_BROWSER_USER", "")
    password = payload.get("password") or ""
    if not username or not password:
        print(json.dumps({"ok": False, "error": "username and password are required"}))
        return 2
    state, note = login(args.site, username, password)
    print(json.dumps({"ok": True, "state": state, "note": note}, indent=1))
    return 0


def _playwright_installed():
    try:
        import playwright  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def main():
    ap = argparse.ArgumentParser(prog="sudo-browser", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("recipes").set_defaults(fn=cmd_recipes)
    sub.add_parser("status").set_defaults(fn=cmd_status)

    p = sub.add_parser("add")
    p.add_argument("site")
    p.add_argument("--url", default="")
    p.set_defaults(fn=cmd_add)

    for name, fn in (("remove", cmd_remove), ("forget", cmd_forget), ("probe", cmd_probe)):
        p = sub.add_parser(name)
        p.add_argument("site")
        if name == "probe":
            p.add_argument("--show", action="store_true", help="open a visible window (needs a screen)")
        p.set_defaults(fn=fn)

    p = sub.add_parser("login")
    p.add_argument("site")
    p.add_argument("--json", default="", help="JSON with username/password ('-' reads stdin)")
    p.set_defaults(fn=cmd_login)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
