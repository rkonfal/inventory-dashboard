#!/bin/zsh
set -euo pipefail

WORKSPACE="/Users/rudolfkonfal/.openclaw/workspace"
LOG="$WORKSPACE/knowledge/cron_mozek_eshopu.log"
MAX_BYTES=$((512 * 1024))
KEEP_LINES=400

mkdir -p "$WORKSPACE/knowledge"

if [[ -f "$LOG" ]]; then
  size=$(wc -c < "$LOG" | tr -d ' ')
  if [[ "$size" -gt "$MAX_BYTES" ]]; then
    tail -n "$KEEP_LINES" "$LOG" > "$LOG.tmp"
    mv "$LOG.tmp" "$LOG"
  fi
fi

exec /opt/homebrew/bin/node "$WORKSPACE/knowledge/refresh_mozek_eshopu.mjs"
