#!/usr/bin/env python3
"""Tests for the browser-login worker's decision logic.

Deliberately browser-free: the part that decides "connected / needs login /
unknown" is pure, so it can be tested on a laptop in a second. The Playwright
paths are thin wrappers around it.

Run: python3 test_browser_state.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("SUDO_BROWSER_HOME", tempfile.mkdtemp(prefix="sudo-browser-test-"))

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "browser_worker", os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser-worker.py")
)
bw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bw)

RECIPE = {
    "label": "Example",
    "home_url": "https://example.com/",
    "login_url": "https://example.com/login",
    "logged_in": {"any": [{"text": "Sign Out"}, {"selector": "a[href*='logout']"}]},
    "logged_out": {"any": [{"text": "Sign In"}, {"url_contains": "/login"}]},
}


class RuleMatching(unittest.TestCase):
    def test_text_match_is_case_insensitive(self):
        self.assertTrue(bw.rule_matches({"any": [{"text": "sign out"}]},
                                        {"text": "Welcome back — SIGN OUT here"}))

    def test_selector_match(self):
        self.assertTrue(bw.rule_matches({"any": [{"selector": "#logout"}]},
                                        {"selectors": ["#logout"]}))

    def test_url_match(self):
        self.assertTrue(bw.rule_matches({"any": [{"url_contains": "/login"}]},
                                        {"url": "https://example.com/Login?next=1"}))

    def test_empty_rule_never_matches(self):
        self.assertFalse(bw.rule_matches({"any": []}, {"text": "Sign Out"}))
        self.assertFalse(bw.rule_matches(None, {"text": "Sign Out"}))


class Classification(unittest.TestCase):
    def test_logged_in_wins_when_both_markers_present(self):
        # Real pages often keep a hidden "Sign In" in a menu; the logged-in
        # marker is the stronger signal, so it must win.
        state, _ = bw.classify(RECIPE, {"url": "https://example.com/", "text": "Sign Out · Sign In"})
        self.assertEqual(state, bw.STATE_CONNECTED)

    def test_logged_out(self):
        state, _ = bw.classify(RECIPE, {"url": "https://example.com/login", "text": ""})
        self.assertEqual(state, bw.STATE_NEEDS_LOGIN)

    def test_challenge_beats_everything(self):
        # A captcha page usually still renders the logged-out shell; saying
        # "needs login" there sends the owner round in circles.
        state, note = bw.classify(RECIPE, {"url": "https://example.com/", "text": "Sign In — verify you are human"})
        self.assertEqual(state, bw.STATE_NEEDS_YOU)
        self.assertIn("human", note)

    def test_two_factor_counts_as_human(self):
        state, _ = bw.classify(RECIPE, {"url": "https://example.com/", "text": "Enter the verification code we sent"})
        self.assertEqual(state, bw.STATE_NEEDS_YOU)

    def test_unknown_is_an_allowed_answer(self):
        state, _ = bw.classify(RECIPE, {"url": "https://example.com/", "text": "hello"})
        self.assertEqual(state, bw.STATE_UNKNOWN)

    def test_error_facts_short_circuit(self):
        state, note = bw.classify(RECIPE, {"error": "net::ERR_NAME_NOT_RESOLVED"})
        self.assertEqual(state, bw.STATE_ERROR)
        self.assertIn("ERR_", note)


class StateFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sudo-browser-home-")
        self._old = bw.HOME, bw.STATE_FILE, bw.SECRET_FILE, bw.PROFILE_ROOT
        bw.HOME = self.tmp
        bw.STATE_FILE = os.path.join(self.tmp, "logins.json")
        bw.SECRET_FILE = os.path.join(self.tmp, "secrets.enc")
        bw.PROFILE_ROOT = os.path.join(self.tmp, "profiles")
        bw._ensure_home()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        bw.HOME, bw.STATE_FILE, bw.SECRET_FILE, bw.PROFILE_ROOT = self._old

    def test_state_round_trip_and_perms(self):
        bw.set_site_state("example", bw.STATE_CONNECTED, "ok")
        data = bw.load_state()
        self.assertEqual(data["sites"]["example"]["state"], bw.STATE_CONNECTED)
        self.assertEqual(os.stat(bw.STATE_FILE).st_mode & 0o777, 0o600)

    def test_invalid_state_is_refused(self):
        with self.assertRaises(AssertionError):
            bw.set_site_state("example", "totally_fine")

    def test_secrets_round_trip(self):
        encrypted = bw.store_secrets("example", "user@example.com", "hunter2")
        got = bw.load_secrets("example")
        self.assertEqual(got["username"], "user@example.com")
        self.assertEqual(got["password"], "hunter2")
        # Whatever the mode, the file must not be world-readable.
        self.assertEqual(os.stat(bw.SECRET_FILE).st_mode & 0o777, 0o600)
        if not encrypted:
            # Documented fallback: plaintext-but-0600, and the state says so.
            self.assertIs(bw._crypto_available(), False)

    def test_forget_removes_only_that_site(self):
        bw.store_secrets("a", "u", "p")
        bw.store_secrets("b", "u", "p")
        self.assertTrue(bw.forget_secrets("a"))
        self.assertIsNone(bw.load_secrets("a"))
        self.assertIsNotNone(bw.load_secrets("b"))

    def test_corrupt_state_file_does_not_crash(self):
        with open(bw.STATE_FILE, "w") as fh:
            fh.write("{not json")
        self.assertEqual(bw.load_state()["sites"], {})


class Recipes(unittest.TestCase):
    def test_shipped_recipes_parse_and_have_urls(self):
        recipes = bw.load_recipes()
        self.assertIn("usps", recipes)
        for key, rec in recipes.items():
            self.assertIn("label", rec, key)
            if key != "generic":
                self.assertTrue(rec.get("home_url") or rec.get("login_url"), key)

    def test_unknown_site_is_a_clean_exit(self):
        with self.assertRaises(SystemExit):
            bw.recipe_for({"usps": {}}, "nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
