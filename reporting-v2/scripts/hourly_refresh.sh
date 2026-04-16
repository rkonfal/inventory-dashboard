#!/bin/zsh
set -euo pipefail

ROOT="/Users/rudolfkonfal/.openclaw/workspace/reporting-v2"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

cd "$ROOT"
python3 scripts/refresh_data.py >> "$LOG_DIR/refresh.log" 2>&1

if [[ "${AUTO_PUBLISH_PREVIEW:-0}" == "1" ]]; then
  python3 scripts/publish_preview.py >> "$LOG_DIR/refresh.log" 2>&1 || true
fi

if [[ "${AUTO_PUSH:-0}" == "1" ]] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git add data/current site >> "$LOG_DIR/refresh.log" 2>&1 || true
  if ! git diff --cached --quiet; then
    git commit -m "Hourly reporting refresh" >> "$LOG_DIR/refresh.log" 2>&1 || true
    git push >> "$LOG_DIR/refresh.log" 2>&1 || true
  fi
fi
