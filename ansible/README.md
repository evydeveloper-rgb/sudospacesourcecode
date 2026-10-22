# Installing sudo with Ansible

For people who manage their machines with [Ansible](https://docs.ansible.com):
install or update sudo on one or many Debian/Ubuntu machines (arm64 or
x86_64, with systemd) from your own computer. The playbook runs the same
[`install.sh`](../install.sh) you could run by hand on each machine, so both
ways install exactly the same thing.

The Sudo box doesn't need any of this: it sets itself up from its card and
updates itself from the dashboard.

## Use

```bash
cd ansible
cp inventory.example.ini inventory.ini        # list your machines
cp group_vars/all.example.yml group_vars/all.yml   # optional settings
ansible-playbook -i inventory.ini sudo.yml
```

Then open `http://<machine>.local` (or `:8080` when port 80 was already
taken) and the welcome walks you through the rest. The agent and the
on-device model finish downloading in the background for a few minutes.

## What it does

1. Checks each machine is Debian or Ubuntu, arm64 or x86_64, with systemd.
2. Finds the latest release (or the one in `sudo_version`).
3. Skips machines already on that version, and installs or updates the rest
   with `install.sh`: the app, the agent (OpenClaw), and an on-device model
   picked for the hardware (bigger on machines with an NVIDIA GPU).
4. Optionally saves an OpenRouter key from your vault.
5. Waits for the dashboard to answer.

Running it again is safe: owners' settings, chats and agents are kept.

## Settings

| Variable | Default | |
|---|---|---|
| `sudo_version` | `latest` | a release such as `1.0.36` |
| `sudo_model` | `auto` | the on-device model; `auto` picks for the hardware |
| `sudo_hostname` | (unchanged) | renames the machine, so the dashboard is at `<name>.local` |
| `sudo_port` | 80, or 8080 when taken | dashboard port |
| `sudo_force` | `false` | reinstall even when the version matches |
| `sudo_openrouter_key` | | keep it in an Ansible vault, never in plain text |

Settings can also go on a single host in the inventory, e.g.
`lab-server ansible_host=… sudo_hostname=lab-sudo`.

## Without Ansible

On the machine itself:

```bash
curl -fsSL https://raw.githubusercontent.com/evydeveloper-rgb/sudospacesourcecode/main/install.sh | sudo bash
```
