#!/usr/bin/env python3
"""make-openclaw-import.py -- package an existing OpenClaw agent for Sudo.

Reads an OpenClaw state directory from an old device (a mounted backup image,
or the device itself) and writes the package sudo-pi/import-openclaw.sh moves
into a fresh Sudo on first boot:

    sudo python3 make-openclaw-import.py \\
        --root /mnt/old            # the old device's filesystem root
        --home /home/alex          # the user whose ~/.openclaw this is
        --agent-name Juno --user-name Alex \\
        --out openclaw-import.tar.gz

then: prepare-sd.sh --import openclaw-import.tar.gz /path/to/bootfs

The package holds the agent's API key and WhatsApp login. It is written 0600,
and the importer deletes it from the card as soon as it has moved in.
Nothing here prints a secret.
"""
import argparse, glob, json, os, re, shutil, sqlite3, tarfile, tempfile

SKIP_STATE = {"npm", "cache", "logs", "completions", "tui", "memory", "identity", "devices",
              "workspace-attestations"}


def sqlite_snapshot(src, dst):
    """A consistent single-file copy, WAL included, without writing to src."""
    tmp = tempfile.mkdtemp()
    base = os.path.join(tmp, os.path.basename(src))
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(src + ext):
            shutil.copy2(src + ext, base + ext)
    s = sqlite3.connect(base)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()
    shutil.rmtree(tmp)


def e164(value):
    digits = re.sub(r"[^\d]", "", str(value or "").split("@")[0].split(":")[0])
    return f"+{digits}" if digits else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="old device's filesystem root")
    ap.add_argument("--home", required=True, help="home dir on the old device, e.g. /home/alex")
    ap.add_argument("--agent-name", required=True)
    ap.add_argument("--user-name", required=True)
    ap.add_argument("--template", default=os.path.join(os.path.dirname(__file__), "..", "openclaw", "models.json"))
    ap.add_argument("--no-tailscale", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    root = a.root.rstrip("/")
    old_state = f"{a.home.rstrip('/')}/.openclaw"
    src = root + old_state
    oc = json.load(open(os.path.join(src, "openclaw.json"), encoding="utf-8"))
    stage = tempfile.mkdtemp(prefix="oc-import-")
    os.chmod(stage, 0o700)
    st = os.path.join(stage, "state")

    # Workspace: wherever the old config pointed it.
    ws_old = ((oc.get("agents") or {}).get("defaults") or {}).get("workspace") or f"{old_state}/workspace"
    shutil.copytree(root + ws_old, os.path.join(stage, "workspace"), symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "server.log"))

    # State: history, agent db, media, plugin skills, the WhatsApp login.
    os.makedirs(st)
    for name in sorted(os.listdir(src)):
        p = os.path.join(src, name)
        if name in SKIP_STATE or not os.path.isdir(p) or name in ("workspace", "credentials", "state"):
            continue
        shutil.copytree(p, os.path.join(st, name), symlinks=True)
    for db in glob.glob(os.path.join(st, "agents", "*", "agent", "*.sqlite")):
        for ext in ("-wal", "-shm"):
            if os.path.exists(db + ext):
                os.remove(db + ext)
        sqlite_snapshot(src + db[len(st):], db)
    os.makedirs(os.path.join(st, "state"))
    sqlite_snapshot(os.path.join(src, "state", "openclaw.sqlite"), os.path.join(st, "state", "openclaw.sqlite"))

    wa_dir = os.path.join(src, "credentials", "whatsapp")
    accounts = [d for d in sorted(os.listdir(wa_dir)) if os.path.isfile(os.path.join(wa_dir, d, "creds.json"))] \
        if os.path.isdir(wa_dir) else []
    wa_acct = accounts[0] if accounts else None
    agent_number = ""
    if wa_acct:
        shutil.copytree(os.path.join(wa_dir, wa_acct), os.path.join(st, "credentials", "whatsapp", wa_acct))
        me = json.load(open(os.path.join(wa_dir, wa_acct, "creds.json"))).get("me") or {}
        agent_number = e164(me.get("id"))

    # Paths inside small JSON bookkeeping files follow the move. Transcripts
    # are left as written; the importer links the old paths instead.
    ws_new, st_new = "/opt/sudo/agent-workspace", "/opt/sudo/openclaw"
    for p in glob.glob(os.path.join(st, "agents", "*", "sessions", "*.json")):
        t = open(p, encoding="utf-8").read()
        t2 = t.replace(ws_old, ws_new).replace(old_state, st_new)
        if t2 != t:
            open(p, "w", encoding="utf-8").write(t2)

    # The API key, from the agent's auth store (never printed).
    key = ""
    for db in glob.glob(os.path.join(st, "agents", "*", "agent", "openclaw-agent.sqlite")):
        c = sqlite3.connect(db)
        try:
            for (store,) in c.execute("select store_json from auth_profile_store"):
                for name, prof in (json.loads(store).get("profiles") or {}).items():
                    if prof.get("provider") == "openrouter":
                        key = key or prof.get("key") or prof.get("apiKey") or ""
        except sqlite3.Error:
            pass
        c.close()

    channel = (oc.get("channels") or {}).get("whatsapp") or {}
    owner = e164((channel.get("allowFrom") or [""])[0])

    primary = (((oc.get("agents") or {}).get("defaults") or {}).get("model") or {}).get("primary", "")
    alias = ""
    try:
        for mdl in json.load(open(a.template, encoding="utf-8")).get("model_list", []):
            if mdl.get("model") == primary:
                alias = mdl.get("model_name", "")
                break
    except OSError:
        pass

    crontab = []
    cron_file = os.path.join(root, "var/spool/cron/crontabs", os.path.basename(a.home))
    if os.path.isfile(cron_file):
        crontab = [l.rstrip("\n") for l in open(cron_file) if l.strip() and not l.startswith("#")]

    apt = []
    py = " ".join(open(f, errors="ignore").read() for f in glob.glob(os.path.join(stage, "workspace", "**", "*.py"), recursive=True))
    for mod, deb in (("requests", "python3-requests"), ("dateutil", "python3-dateutil"),
                     ("numpy", "python3-numpy"), ("yaml", "python3-yaml")):
        if re.search(rf"^\s*(import|from)\s+{mod}\b", py, re.M):
            apt.append(deb)

    if not a.no_tailscale and os.path.isfile(os.path.join(root, "var/lib/tailscale/tailscaled.state")):
        os.makedirs(os.path.join(stage, "tailscale"))
        shutil.copy2(os.path.join(root, "var/lib/tailscale/tailscaled.state"), os.path.join(stage, "tailscale"))

    shutil.copy2(os.path.join(src, "openclaw.json"), os.path.join(stage, "openclaw.json"))
    manifest = {
        "format": 1,
        "agent_name": a.agent_name, "user_name": a.user_name,
        "old_state_dir": old_state, "old_workspace": ws_old,
        "whatsapp_account": wa_acct, "owner_number": owner, "agent_number": agent_number,
        "openrouter_key": key, "model_alias": alias,
        "crontab": crontab, "apt": apt,
    }
    json.dump(manifest, open(os.path.join(stage, "manifest.json"), "w"), indent=2)

    fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f, tarfile.open(fileobj=f, mode="w:gz") as tar:
        for name in sorted(os.listdir(stage)):
            tar.add(os.path.join(stage, name), arcname=name)
    shutil.rmtree(stage)

    shown = {k: v for k, v in manifest.items() if k != "openrouter_key"}
    shown["openrouter_key"] = "found" if key else "MISSING"
    print(json.dumps(shown, indent=2))
    print(f"wrote {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
