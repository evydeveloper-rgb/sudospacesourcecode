# Sudo — Open Loops + Embedded Chat (spec)

Status: proposed — direction approved 2026-10-13, building Phase A.
Owner: Claudia (orchestrator); implementation via Claude Code.

## Why

Two problems with the current agent experience for a sudo user:

1. **The agent has no place to remember unfinished work.** Its workspace ships
   `SOUL/IDENTITY/USER/AGENTS/MEMORY/HEARTBEAT` but nothing that says "I owe
   follow-up on X". Deferred/promised work falls into daily notes and is lost.
2. **The agent only lives in the chat page.** If the person is in the health app
   or the home app, they have to leave, go to chat, and re-explain context.

And one confusion source: **PicoClaw answers in the chat window until OpenClaw
finishes installing**, so for the first minutes the person is talking to a
different brain under the same UI.

## Phase A — build now

### A1. Retire PicoClaw as a *chat* fallback — done; removed entirely in 1.0.36
- Replace the "PicoClaw answers while OpenClaw installs" behaviour with an
  explicit **onboarding/progress state** in the chat UI: "Your agent is getting
  set up (~2–3 min). You can keep this page open; it'll switch over on its own."
- Keep the picoclaw binary on-card **only as a silent last-resort recovery** if
  OpenClaw install can't complete (no wifi / Node install failed). No identity,
  no persona surfaced in chat.
- `agent_backend()` no longer returns `picoclaw` just because OpenClaw isn't
  installed yet; it reports a `provisioning` state instead. The owner can still
  pin `agent_backend: picoclaw` explicitly (support/debug escape hatch).
- Success = a new user never sees "I'm picoclaw…" in chat.

### A2. Open-loops file in the agent workspace
- New template `openclaw/workspace/PENDING.md` (rendered into
  `/opt/sudo/agent-workspace/PENDING.md` like the other workspace files).
  One line per loop: `- [added YYYY-MM-DD] what's open → next action`.
- AGENTS.md lines: read PENDING.md at session start; add a line the moment
  something is deferred/promised; delete the line the moment it resolves; a
  loop older than ~7 days gets surfaced or dropped.

### A3. Tasks tab shows loops
- `sudo-dashboard` Tasks panel gains a persistent **Open loops** section above
  the existing transient **Runs** section:
  - read/write `/opt/sudo/agent-workspace/PENDING.md` via a new endpoint
    (`GET/POST/PATCH/DELETE /api/agent/loops`),
  - each loop: text, `added` date, **status** (Open / Started / Working / Done),
  - add + delete from the UI,
  - delete-from-UI should keep the file the single source of truth (agent reads
    the same file).
- Status is stored in the file too (small tagged suffix), so the agent and the
  UI agree.

## Phase B — spec'd, build after A

### B1. Start button on a loop (hybrid, subagent-first)
- Primary: click → spins up a background run (the existing `/api/agent/runs`
  mechanism), loop flips to **Working**, answer lands back in chat.
- Secondary "Discuss": opens chat with the loop's text pre-filled in the box.
- Recommendation recorded: subagent-first, because paste-only costs extra turns.

### B2. One shared embedded chat widget across all apps
- A single web component / injected widget loaded by every sudo app
  (health app, home app, dashboard, …) — one implementation, not one per app.
- Hits the same agent endpoint; carries the current app + page context.
- **Contextual prompt suggestions per app**, e.g.
  - health: "log a headache", "why did my sleep drop this week"
  - home: "shut off my daughter's internet at 9 pm", "add a guest wifi"
  - files: "find my tax PDF from last year"
- Suggestions must be genuinely app-aware, not a generic canned list.

## Non-goals
- No change to the OpenClaw install flow itself (Phase A only changes what the
  user sees while it runs).
- No new auth scheme for the embedded widget — reuses the dashboard's existing
  session/local link.

## Open questions
- Keep the picoclaw binary on the card at all, or drop it entirely?
- Loop statuses: is Open / Started / Working / Done the right set, or fewer?
