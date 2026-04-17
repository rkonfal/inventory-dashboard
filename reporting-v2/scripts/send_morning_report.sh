#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

ROOT="/Users/rudolfkonfal/.openclaw/workspace/reporting-v2"
OPENCLAW_ROOT="/Users/rudolfkonfal/Desktop/openclaw-main"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/morning-report.log"
mkdir -p "$LOG_DIR"

CHANNEL="${MORNING_REPORT_CHANNEL:-telegram}"
TARGET="${MORNING_REPORT_TARGET:-7056032500}"
DETAIL_URL="${MORNING_REPORT_DETAIL_URL:-https://rkonfal.github.io/diamond-plus-reporting-preview/site/index.html}"
AUTO_PUBLISH="${AUTO_PUBLISH_PREVIEW:-1}"
DRY_RUN="${MORNING_REPORT_DRY_RUN:-0}"

cd "$ROOT"
export MORNING_REPORT_DETAIL_URL="$DETAIL_URL"

python3 scripts/refresh_data.py >> "$LOG_FILE" 2>&1

if [[ "$AUTO_PUBLISH" == "1" ]]; then
  python3 scripts/publish_preview.py >> "$LOG_FILE" 2>&1 || true
fi

REPORT_FILE="$ROOT/data/current/morning_report_previous_day_telegram.txt"
if [[ ! -s "$REPORT_FILE" ]]; then
  echo "Missing morning report file: $REPORT_FILE" >> "$LOG_FILE"
  exit 1
fi

MESSAGE="$(cat "$REPORT_FILE")"
NODE_BIN="${NODE_BIN:-$(command -v node || true)}"
if [[ -z "$NODE_BIN" ]]; then
  echo "Node binary not found in PATH" >> "$LOG_FILE"
  exit 1
fi
CMD=("$NODE_BIN" "$OPENCLAW_ROOT/openclaw.mjs" message send --channel "$CHANNEL" --target "$TARGET" --message "$MESSAGE")
if [[ "$DRY_RUN" == "1" ]]; then
  CMD+=(--dry-run)
fi
"${CMD[@]}" >> "$LOG_FILE" 2>&1
