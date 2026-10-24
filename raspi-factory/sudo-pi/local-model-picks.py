#!/usr/bin/env python3
"""local-model-picks.py — which on-device model a new machine starts with.

The table hardware-profile.py reads before working anything out itself. Edit
it as models are tried on real hardware: the first rule that matches wins,
and a machine no rule matches gets a model computed from its memory and
speed (see pick_model in hardware-profile.py).

A rule matches on any of:
  kind      raspberry-pi | jetson | x86 | arm
  board     text the board name contains ("Pi 5", "Orin Nano")
  ram_min   GB of memory, at least (MemTotal, so an 8GB board reads ~7.9)
  ram_max   GB of memory, below
  gpu_min   GB of GPU memory, at least

"tested" says whether the pick has been run on that hardware yet, and the
note says how it went. Only machines setting up their first model use this:
one that already has a model keeps it, and the owner can always switch in
Settings -> Agent & models -> On-device models.

  local-model-picks.py   prints the table
"""

PICKS = [
    {"kind": "raspberry-pi", "ram_max": 2.5, "model": "gemma3:270m", "tested": True,
     "note": "2GB: the greeter is the only model that leaves the agent room."},
    {"kind": "raspberry-pi", "ram_max": 4.5, "model": "gemma3:270m", "tested": True,
     "note": "4GB Pi 5: answers in about a second and leaves the agent its memory."},
    {"kind": "raspberry-pi", "ram_max": 8.5, "model": "gemma3:1b", "tested": False,
     "note": "8GB Pi 5: about 1GB in memory, around 10 words a second expected. "
             "Next to try: qwen3:1.7b."},
    {"kind": "raspberry-pi", "model": "gemma3:1b", "tested": False,
     "note": "16GB Pi 5: speed, not memory, is the limit on a Pi. "
             "Next to try: qwen3:1.7b, llama3.2:3b."},
    {"kind": "jetson", "board": "Orin Nano", "model": "gemma3:4b", "tested": False,
     "note": "8GB Orin Nano: runs on the GPU, which shares the main memory."},
]


if __name__ == "__main__":
    for rule in PICKS:
        where = ", ".join(f"{k}={v}" for k, v in rule.items() if k not in ("model", "tested", "note"))
        print(f"{rule['model']:14s} {'tested' if rule['tested'] else 'untested':9s} {where}")
