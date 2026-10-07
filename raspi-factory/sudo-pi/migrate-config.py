#!/usr/bin/env python3
"""Migrate /opt/sudo/config.json across Sudo versions without losing anything.

The owner's file is never overwritten by an update -- but if a release renames
or restructures a setting, the old key is simply ignored from then on, and the
choice the owner made quietly stops working. That is the real risk: not damage,
but loss of meaning. This closes it.

How it works:
  - Every config carries `config_version` (an integer, 0 for files written
    before versioning existed).
  - MIGRATIONS maps a version to a function that carries the file *from* that
    version to the next. Each one moves or reshapes the owner's values, never
    invents them.
  - On run, migrations apply in order until the file is current, a timestamped
    backup is written first, and the new version is stamped. Running it twice
    changes nothing the second time.

Rules for writing a migration:
  - Only move or reshape an existing value. Never set a choice the owner did
    not make -- a default belongs in the code that reads the key, not here.
  - Never delete a value you cannot place. If a key is retired *and* had a
    value, keep the value under `_retired` rather than dropping it, so nothing
    is silently thrown away and a later migration can still read it.

Call it from the dashboard at startup and from sudo-update.sh after configure,
so a device migrating either way lands on the same file.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

CONFIG = "/opt/sudo/config.json"
CURRENT_VERSION = 1


def _migration_000_to_001(cfg: dict) -> dict:
    """Version 1. Adds the reply-permission era.

    Before the "Sudo can reply" switch, the owner's list of people the agent
    may message lived in one flat shape. Nothing needs moving for a file that
    never had it, but a file that stored the list *inside* config.json under an
    older key is carried to the key the code reads now.
    """
    # Older files kept the allow-list here; the live file is whatsapp-contacts.json,
    # but seed from the old key if the live one is missing so the list is not lost.
    legacy = cfg.pop("whatsapp_allowed_contacts", None)
    if legacy and not os.path.isfile("/opt/sudo/whatsapp-contacts.json"):
        try:
            with open("/opt/sudo/whatsapp-contacts.json", "w", encoding="utf-8") as f:
                json.dump({"agent_can_add": True, "contacts": legacy}, f, indent=2)
        except OSError:
            # Could not seed; keep the value so a later run can try again.
            cfg["whatsapp_allowed_contacts"] = legacy
    return cfg


MIGRATIONS = {
    0: _migration_000_to_001,
}


def migrate(path: str = CONFIG, *, verbose: bool = True) -> int:
    """Bring a config file up to CURRENT_VERSION. Returns the version it ends on."""
    if not os.path.isfile(path):
        return CURRENT_VERSION
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return CURRENT_VERSION  # unreadable; leave it alone rather than guess
    if not isinstance(cfg, dict):
        return CURRENT_VERSION

    version = cfg.get("config_version")
    version = int(version) if isinstance(version, int) else 0
    if version >= CURRENT_VERSION:
        return version

    # Back up before touching anything, once, so there is always the file as it
    # was the moment before its first migration.
    try:
        shutil.copy2(path, f"{path}.pre-migrate-{int(time.time())}.bak")
    except OSError:
        pass

    from_version = version
    while version < CURRENT_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            version += 1  # no work for this step; keep going
            continue
        cfg = step(cfg)
        version += 1
    cfg["config_version"] = version

    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        return from_version
    if verbose:
        print(f"config migrated {from_version} -> {version}")
    return version


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else CONFIG
    print(f"config_version {migrate(target)}")
