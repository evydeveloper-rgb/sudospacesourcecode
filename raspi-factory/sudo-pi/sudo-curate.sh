#!/bin/bash
# sudo-curate.sh — keep the agent's notes from silting up.
#
# The agent writes freely: daily notes (memory/YYYY-MM-DD.md) pile up, and
# MEMORY.md is read at the start of every conversation, so if it only ever
# grows the agent gets slower and vaguer over months. This is the housekeeping
# pass that stops that.
#
# Deliberately NOT an LLM turn. The valuable, judgment-heavy distillation --
# "this fact belongs in knowledge/people.md" -- is the agent's job and already
# in its instructions (AGENTS.md). What actually rots is mechanical and can be
# done for free:
#
#   * daily notes older than a fortnight move to memory/archive/, so the
#     live folder stays small and the recent-notes list stays readable (the
#     archived files remain searchable -- this is tidying, not deletion);
#   * MEMORY.md, if it has grown past a healthy size, gets a note appended
#     asking the agent (in its next turn) to fold what belongs elsewhere into
#     knowledge/ and trim itself.
#
# No model call, so this can run often without touching anyone's budget.
#
# Not `set -e`: housekeeping is best-effort and must never page anyone.
set -uo pipefail

WORKSPACE=/opt/sudo/agent-workspace
MEMORY="$WORKSPACE/MEMORY.md"
ARCHIVE="$WORKSPACE/memory/archive"
LOG=/var/log/sudo-curate.log

# Daily notes older than this leave the live folder.
KEEP_DAYS=14
# Above this size, MEMORY.md gets a nudge to trim. It is read every turn, so
# the cap is about prompt budget, not disk.
MEMORY_CAP_BYTES=6000

log() { echo "$(date -Is) $*"; }

{
echo "=== sudo-curate $(date -Is) ==="

[ -d "$WORKSPACE/memory" ] || { log "no memory folder yet"; exit 0; }

# 1. Rotate old daily notes out of the live folder.
mkdir -p "$ARCHIVE"
moved=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  mv "$f" "$ARCHIVE/" 2>/dev/null && moved=$((moved + 1))
done < <(find "$WORKSPACE/memory" -maxdepth 1 -type f -name '*.md' \
           ! -name 'MEMORY.md' -mtime "+${KEEP_DAYS}" 2>/dev/null)
log "archived ${moved} daily note(s) older than ${KEEP_DAYS}d"

# 2. Flag MEMORY.md when it has outgrown a healthy size.
if [ -f "$MEMORY" ]; then
  size=$(wc -c < "$MEMORY" 2>/dev/null || echo 0)
  if [ "$size" -gt "$MEMORY_CAP_BYTES" ]; then
    stamp="$(date -Is)"
    # One marker at a time; replace any earlier one so they don't stack up.
    python3 - "$MEMORY" "$size" "$stamp" << 'PY'
import sys, pathlib
path, size, stamp = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
text = p.read_text(encoding="utf-8")
start, end = "<!-- sudo:trim -->", "<!-- /sudo:trim -->"
import re
text = re.sub(rf"\n*{re.escape(start)}.*?{re.escape(end)}\n*", "\n\n", text, flags=re.S)
note = (f"{start}\n_MEMORY.md is {size} bytes (cap ~6000). Next turn: move settled facts that belong "
        f"to a person, project or routine into knowledge/, fold the rest into a few tight lines, and "
        f"remove what has stopped being true. Keep only what future-you actually needs._\n{end}\n")
# Sit the note right under the title line (or at the top).
title, _, rest = text.partition("\n")
p.write_text(f"{title}\n\n{note}\n{rest.lstrip()}\n" if rest else f"{title}\n\n{note}", encoding="utf-8")
PY
    log "MEMORY.md at ${size} bytes — trim note added"
  else
    log "MEMORY.md ${size} bytes — ok"
  fi
fi

log "done"
} >> "$LOG" 2>&1
