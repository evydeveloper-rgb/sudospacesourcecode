# Voice calls — design (Phase 1)

Status: **implemented (Phase 1)** — `sudo-voice/`, pending hardware test.
Owner decisions were settled 2026-10-23: owners bring their own Gemini key, a
second number is a hard requirement, and the agent may not start calls on its
own in this phase.

## What the owner gets

Their sudo box can **ring them** and they can **ring it**, over WhatsApp, with a natural
speech-to-speech voice that still has access to the agent's memory, calendar and tools mid-call.
Same feature already running on the developer machine.

## Why it isn't just "enable a flag"

WhatsApp voice calls are not exposed by the messaging stack sudo already uses. The existing
`whatsapp-bridge` (Node/Baileys) sends and receives messages; it cannot place or answer calls.
Calls need a sidecar that speaks the WhatsApp VoIP protocol.

We use the upstream MIT-licensed sidecar
[`hermes-whatsapp-voice`](https://github.com/ashwanthbalakrishnan5/hermes-whatsapp-voice):

```
 owner phone ──WhatsApp call──▶ bot number ──▶ hermes-wa-voice (linked device, Rust)
                                                   │  call audio (16 kHz in / 24 kHz out)
                                                   ▼
                                             Gemini Live (speech-to-speech)
                                                   │  ask_hermes(...) / end_call
                                                   ▼
                                             voice-bridge.py  ──▶  local OpenClaw gateway
```

The sidecar expects a Hermes-style agent API (`/v1/runs`). We don't run Hermes, so
`voice-bridge.py` implements that contract and forwards to the device's own OpenClaw gateway —
the same shim design already proven on the developer machine.

## Hardware

| Platform | Support |
|---|---|
| Raspberry Pi 5 / Pi 4 / CM4 (64-bit OS) | ✅ prebuilt `linux_arm64` |
| x86_64 mini PC / NUC | ✅ prebuilt `linux_amd64` |
| 32-bit ARM (Pi Zero 2 W on a 32-bit image, Pi 3 32-bit) | ❌ no upstream build — would need a cross-compile from source |
| macOS | buildable, not a sudo target |

The installer detects `uname -m` and refuses politely on anything else, instead of crash-looping.

## Components (all new, under `raspi-factory/sudo-voice/`)

| File | Role |
|---|---|
| `install-sudo-voice.sh` | dashboard-triggered installer: arch check → checksum-verified download → `/opt/sudo-voice` → two systemd units → status file |
| `voice-bridge.py` | Hermes API shim → local OpenClaw gateway; live snapshot; run audit log; stdlib only |
| `sudo-voice-call` | the agent-facing tool: `sudo-voice-call "reason"`, `--list`, `--status` |
| dashboard hooks | Settings → Channels → Voice calls: set-up, QR pairing, status, "Call me now" |
| `VOICE-CALLS.md` | this file |

Units: `sudo-voice-sidecar.service` (the binary, `serve`) and `sudo-voice-bridge.service`
(the shim), both hardened in the style of `sudo-whatsapp-bridge.service`, state in
`/var/lib/sudo-voice/` (0700).

Control surface, loopback only, Bearer token: `GET /health`, `GET /calls`,
`POST /calls {"reason": "..."}` on the sidecar — the `reason` string *is* the call's brief.

## Two hard requirements

1. **A second WhatsApp number for the bot.** WhatsApp cannot call itself, so voice calls do not
   work when the device is linked to the owner's own account (self-chat mode). The owner needs a
   spare number — eSIM, second SIM, or a number they already own — to be the bot's account.
   Onboarding must say this before pairing, not after.
2. **A Gemini API key on the device.** Gemini Live does the speech-to-speech. Roughly $0.005/min
   audio in and $0.018/min out — about **$1.40/hour**, or a few cents per typical check-in call.
   Keys come from aistudio.google.com/apikey.

## Privacy

Audio is bridged to Gemini Live during the call, the same as any cloud STT/TTS. Transcripts,
summaries and the audit log stay on the device under `/var/lib/sudo-voice/`. No recording is kept
beyond the transcript. This must be stated in onboarding in one sentence.

## Open decisions (Evy)

1. **Whose Gemini key?** A key per device (owner brings their own) vs. sudo issuing one shared key
   and metering usage against the device's spend guard. Shared is a better first-run experience and
   a real cost exposure.
2. **How does a user get the second number?** Require their own spare number, or ship an ordering
   flow for one? This is the single biggest drop-off risk in onboarding.
3. **Proactive calling policy** — should the agent be allowed to *start* calls on its own
   (reminders, urgent alerts), or only call in reply? Default in Phase 1: reply-only plus explicit
   "call me" requests.
4. **Pricing** — included, or a paid add-on per device?
