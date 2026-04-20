#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

ROOT="/Users/rudolfkonfal/.openclaw/workspace/reporting-v2"
OPENCLAW_ROOT="/Users/rudolfkonfal/Desktop/openclaw-main"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/morning-report.log"
mkdir -p "$LOG_DIR"

CHANNEL="${MORNING_REPORT_CHANNEL:-telegram}"
TARGETS_RAW="${MORNING_REPORT_TARGET:-7056032500}"
DETAIL_URL="${MORNING_REPORT_DETAIL_URL:-https://rkonfal.github.io/diamond-plus-reporting-preview/site/index.html}"
AUTO_PUBLISH="${AUTO_PUBLISH_PREVIEW:-1}"
DRY_RUN="${MORNING_REPORT_DRY_RUN:-0}"
REFRESH_TIMEOUT_SECONDS="${MORNING_REPORT_REFRESH_TIMEOUT_SECONDS:-1800}"
PUBLISH_TIMEOUT_SECONDS="${MORNING_REPORT_PUBLISH_TIMEOUT_SECONDS:-300}"
REPORT_FILE="$ROOT/data/current/morning_report_previous_day_telegram.txt"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

run_python_with_timeout() {
  local timeout_seconds="$1"
  shift
  python3 - <<'PY' "$timeout_seconds" "$@"
import subprocess
import sys

timeout = int(sys.argv[1])
cmd = sys.argv[2:]
completed = subprocess.run(cmd, timeout=timeout)
sys.exit(completed.returncode)
PY
}

cd "$ROOT"
export MORNING_REPORT_DETAIL_URL="$DETAIL_URL"

log "Morning report run started"
run_python_with_timeout "$REFRESH_TIMEOUT_SECONDS" python3 scripts/refresh_data.py >> "$LOG_FILE" 2>&1

if [[ "$AUTO_PUBLISH" == "1" ]]; then
  if ! run_python_with_timeout "$PUBLISH_TIMEOUT_SECONDS" python3 scripts/publish_preview.py >> "$LOG_FILE" 2>&1; then
    log "Preview publish failed or timed out, continuing without blocking report delivery"
  fi
fi

if [[ ! -s "$REPORT_FILE" ]]; then
  log "Missing morning report file: $REPORT_FILE"
  exit 1
fi

MESSAGE="$(cat "$REPORT_FILE")"
NODE_BIN="${NODE_BIN:-/opt/homebrew/bin/node}"
if [[ ! -x "$NODE_BIN" ]]; then
  NODE_BIN="$(PATH="$PATH" command -v node || true)"
fi
if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
  log "Node binary not found in PATH"
  exit 1
fi

set -A TARGET_LIST ${(s:,:)TARGETS_RAW}
for TARGET in "${TARGET_LIST[@]}"; do
  TARGET="${TARGET//[[:space:]]/}"
  [[ -z "$TARGET" ]] && continue
  CMD=("$NODE_BIN" "$OPENCLAW_ROOT/openclaw.mjs" message send --channel "$CHANNEL" --target "$TARGET" --message "$MESSAGE")
  if [[ "$DRY_RUN" == "1" ]]; then
    CMD+=(--dry-run)
  fi
  "${CMD[@]}" >> "$LOG_FILE" 2>&1
  log "Morning report delivered to $TARGET"
done
