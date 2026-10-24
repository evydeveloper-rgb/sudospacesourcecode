#!/usr/bin/env python3
"""hardware-profile.py — what this machine is, and which local model fits it.

Sudo started on the Raspberry Pi 5 and now installs on other Linux machines
too (install.sh, the Ansible playbook). This is the one place that tells them
apart, so the installer and the dashboard agree:

  kind   raspberry-pi | jetson | x86 | arm
  board  "Raspberry Pi 5", "NVIDIA Jetson Orin Nano", the PC's model name...
  ram_gb, cores, cpu
  gpu    an NVIDIA card (name, memory) or the Jetson's shared memory, else null
  model  the on-device model installed by default (a model from the
         dashboard's catalogue)

  hardware-profile.py          JSON
  hardware-profile.py --model  just the model id

How the default model is picked. First from the table in
local-model-picks.py (beside this file): hardware someone has tried, or
decided on, gets that model. A machine the table does not cover gets one
worked out here: with a GPU the biggest model that fits in the GPU's memory,
because there it runs fast; without one, a model small enough to answer at a
readable speed on the processor (memory bandwidth decides that: about 9 GB/s
on a Pi 5, 4 on a Pi 4, 20 on an ordinary PC) that still leaves the agent
room.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

# id, size on disk in GB (the dashboard's catalogue, smallest first).
MODELS = [
    ("gemma3:270m", 0.29), ("qwen3:0.6b", 0.52), ("gemma3:1b", 0.81), ("qwen3:1.7b", 1.36),
    ("llama3.2:3b", 2.02), ("qwen3:4b", 2.5), ("gemma3:4b", 3.35), ("llama3.1:8b", 4.92),
    ("qwen3:8b", 5.23), ("gemma3:12b", 8.16), ("qwen3:14b", 9.28),
]
AGENT_RESERVE_GB = 1.6      # what the agent, dashboard and OS keep (server.py LOCAL_RESERVE_GB)
CPU_BANDWIDTH_GBS = 20.0    # an ordinary PC without a GPU (server.py local_speed_estimate)
CPU_MIN_TOKENS_S = 8.0      # readable


def read(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read().replace("\x00", "").strip()
    except OSError:
        return ""


def ram_gb():
    for line in read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            return round(int(line.split()[1]) / 1024 / 1024, 2)
    return 0.0


def cpu_name():
    for line in read("/proc/cpuinfo").splitlines():
        if line.lower().startswith(("model name", "hardware")):
            return line.split(":", 1)[1].strip()
    return ""


def nvidia_gpu():
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=15).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    name, mem = [x.strip() for x in out[0].split(",", 1)]
    try:
        return {"name": name, "memory_gb": round(int(mem) / 1024, 1), "shared": False}
    except ValueError:
        return None


def profile():
    dt_model = re.sub(r"\s+Rev\s+\S+$", "", read("/proc/device-tree/model"))
    arch = os.uname().machine
    ram = ram_gb()
    gpu = None
    if "Raspberry Pi" in dt_model:
        kind, board = "raspberry-pi", re.sub(r"\s+Model\s.*$", "", dt_model)
    elif "Jetson" in dt_model or os.path.exists("/etc/nv_tegra_release"):
        kind, board = "jetson", dt_model or "NVIDIA Jetson"
        # The Jetson's GPU shares the main memory.
        gpu = {"name": "Jetson GPU (shared memory)", "memory_gb": round(max(0.0, ram - 2.5), 1), "shared": True}
    elif arch in ("x86_64", "amd64"):
        kind = "x86"
        vendor, product = read("/sys/class/dmi/id/sys_vendor"), read("/sys/class/dmi/id/product_name")
        board = " ".join(x for x in (vendor, product) if x and "o be filled" not in x.lower()) or "PC"
        gpu = nvidia_gpu()
    else:
        kind, board = "arm", dt_model or "Arm computer"
        gpu = nvidia_gpu()
    model, why = pick_model(kind, ram, gpu, board)
    return {"kind": kind, "board": board, "arch": arch, "ram_gb": ram, "cores": os.cpu_count() or 0,
            "cpu": cpu_name(), "gpu": gpu, "model": model, "model_why": why}


def fits(size_gb, room_gb):
    return size_gb * 1.1 + 0.3 <= room_gb


def load_picks():
    """The table from local-model-picks.py: beside this file in the repo, and
    as sudo-local-model-picks.py beside sudo-hardware-profile.py on a device.
    Missing or broken means no table, never a failed install."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("sudo-local-model-picks.py", "local-model-picks.py"):
        path = os.path.join(here, name)
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("sudo_local_model_picks", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return list(getattr(mod, "PICKS", []))
        except Exception:  # noqa: BLE001 - fall back to working it out
            return []
    return []


def rule_matches(rule, kind, ram, gpu, board):
    if rule.get("kind") and rule["kind"] != kind:
        return False
    if rule.get("board") and rule["board"].lower() not in (board or "").lower():
        return False
    if "ram_min" in rule and ram < rule["ram_min"]:
        return False
    if "ram_max" in rule and ram >= rule["ram_max"]:
        return False
    if "gpu_min" in rule and ((gpu or {}).get("memory_gb") or 0) < rule["gpu_min"]:
        return False
    return True


def pick_model(kind, ram, gpu, board=""):
    """(model, why). The table first; then the biggest model that fits and
    still answers at a readable speed."""
    sizes = dict(MODELS)
    for rule in load_picks():
        model = rule.get("model")
        if not model or not rule_matches(rule, kind, ram, gpu, board):
            continue
        # A table entry that cannot fit this particular machine is skipped,
        # not obeyed: a model that will not load is worse than a smaller one.
        if model in sizes and not fits(sizes[model], ram - AGENT_RESERVE_GB) and not (gpu or {}).get("memory_gb"):
            continue
        return model, "table" + ("" if rule.get("tested") else " (untested)")
    if gpu and gpu.get("memory_gb"):
        room = gpu["memory_gb"] - (0 if gpu.get("shared") else 0.5)
        best = [m for m, size in MODELS if fits(size, room)]
        if best:
            return best[-1], "fits the GPU"
    bandwidth = (9.0 if "Pi 5" in board else 4.0 if "Pi 4" in board
                 else CPU_BANDWIDTH_GBS if kind == "x86" else 6.0)
    room = ram - AGENT_RESERVE_GB
    max_size = bandwidth / CPU_MIN_TOKENS_S
    best = [m for m, size in MODELS if fits(size, room) and size <= max_size]
    return (best[-1], "fits memory and speed") if best else ("gemma3:270m", "smallest")


if __name__ == "__main__":
    p = profile()
    print(p["model"] if "--model" in sys.argv else json.dumps(p, indent=2))
