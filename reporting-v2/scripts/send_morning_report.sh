#!/bin/zsh
set -euo pipefail

ROOT="/Users/rudolfkonfal/.openclaw/workspace/reporting-v2"
LOG_FILE="$ROOT/logs/morning-report.log"
cd "$ROOT"

mkdir -p "$ROOT/logs"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# Keep launchd runs self-contained; user shell init files can emit interactive-only
# completion noise and are not needed here because PATH/environment is set above.

ts() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  echo "$*" | tee -a "$LOG_FILE"
}

STATE_FILE="$ROOT/data/current/morning_report_prepare_status.json"
DELIVERY_STATE_FILE="$ROOT/data/current/morning_report_delivery_status.json"
REPORT_JSON="$ROOT/data/current/morning_report_previous_day.json"

# ── Duplicate-send guard ─────────────────────────────────────────────────────
# If the report was already successfully delivered today, exit silently.
# This prevents duplicate sends caused by launchd re-firing a missed job after
# the Mac wakes from sleep, reboots, or a session restart.
if [[ -f "$DELIVERY_STATE_FILE" ]]; then
  _already_today=$(python3 - <<'PYGUARD'
import json, sys
from datetime import datetime
from zoneinfo import ZoneInfo
import os
PRAGUE_TZ = ZoneInfo('Europe/Prague')
try:
    d = json.loads(open(os.environ['DELIVERY_STATE_FILE']).read())
    if d.get('allDelivered') and d.get('generatedAt'):
        sent = datetime.fromisoformat(d['generatedAt']).astimezone(PRAGUE_TZ).strftime('%Y-%m-%d')
        today = datetime.now(PRAGUE_TZ).strftime('%Y-%m-%d')
        print('yes' if sent == today else 'no')
    else:
        print('no')
except Exception:
    print('no')
PYGUARD
)
  if [[ "$_already_today" == "yes" ]]; then
    log "INFO: Morning report already delivered today – skipping duplicate send."
    exit 0
  fi
fi
# ─────────────────────────────────────────────────────────────────────────────

run_independent_klaviyo_refresh() {
  set +e
  python3 scripts/fetch_ecomail.py > >(tee -a "$LOG_FILE") 2> >(tee -a "$LOG_FILE" >&2)
  KLAVIYO_REFRESH_STATUS=$?
  set -e

  if [[ "$KLAVIYO_REFRESH_STATUS" -eq 0 ]]; then
    KLAVIYO_MESSAGE="Independent Ecomail refresh succeeded."
    log "INFO: $KLAVIYO_MESSAGE"
  else
    KLAVIYO_MESSAGE="Independent Ecomail refresh failed with exit $KLAVIYO_REFRESH_STATUS."
    log "WARN: $KLAVIYO_MESSAGE"
  fi
}

write_prepare_status() {
  python3 scripts/morning_report_helper.py write-prepare-status \
    --path "$STATE_FILE" \
    --refresh-status "$REFRESH_STATUS" \
    --klaviyo-refresh-status "$KLAVIYO_REFRESH_STATUS" \
    --klaviyo-attempted \
    --klaviyo-message "$KLAVIYO_MESSAGE"
}

REFRESH_STATUS=0
KLAVIYO_REFRESH_STATUS=0
KLAVIYO_MESSAGE=""
if [[ "${MORNING_REPORT_SKIP_REFRESH:-0}" != "1" ]]; then
  run_independent_klaviyo_refresh
  set +e
  python3 - <<'PY' 2>&1 | tee -a "$LOG_FILE"
import os
import subprocess
import sys

root = os.environ.get('ROOT_OVERRIDE') or '/Users/rudolfkonfal/.openclaw/workspace/reporting-v2'
timeout = int(os.environ.get('MORNING_REPORT_REFRESH_TIMEOUT_SECONDS', '900'))
cmd = [sys.executable, 'scripts/refresh_data.py']

try:
    completed = subprocess.run(
        cmd,
        cwd=root,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        print(f'WARN: refresh_data.py exited with code {completed.returncode}; will try last successful generated report if valid', flush=True)
    raise SystemExit(completed.returncode)
except subprocess.TimeoutExpired:
    print(f'WARN: refresh_data.py timed out after {timeout} seconds; will try last successful generated report if valid', flush=True)
    raise SystemExit(124)
PY
  REFRESH_STATUS=$?
  set -e
  write_prepare_status

  if [[ "$REFRESH_STATUS" -eq 0 && "${AUTO_PUBLISH_PREVIEW:-0}" == "1" ]]; then
    python3 scripts/publish_preview.py 2>&1 | tee -a "$LOG_FILE"
  elif [[ "$KLAVIYO_REFRESH_STATUS" -eq 0 ]]; then
    log "INFO: Core refresh failed after successful independent Ecomail refresh."
  fi
else
  log "Skipping refresh step, sending pre-generated morning report."
fi

CHANNEL="${MORNING_REPORT_CHANNEL:-telegram}"
TARGETS_RAW="${MORNING_REPORT_TARGET:-}"
DETAIL_URL="${MORNING_REPORT_DETAIL_URL:-}"
MESSAGE_FILE="$ROOT/data/current/morning_report_previous_day_telegram.txt"
PREPARE_REFRESH_STATUS="$REFRESH_STATUS"
PREPARE_KLAVIYO_REFRESH_STATUS="$KLAVIYO_REFRESH_STATUS"

read_prepare_refresh_status() {
  if [[ -f "$STATE_FILE" ]]; then
    python3 scripts/morning_report_helper.py read-prepare-status --path "$STATE_FILE"
  else
    echo "$REFRESH_STATUS"
  fi
}

read_prepare_klaviyo_status() {
  if [[ -f "$STATE_FILE" ]]; then
    python3 scripts/morning_report_helper.py read-klaviyo-status --path "$STATE_FILE"
  else
    echo "$KLAVIYO_REFRESH_STATUS"
  fi
}

validate_report_for_yesterday() {
  python3 scripts/morning_report_helper.py validate-report --path "$REPORT_JSON"
}

attempt_prepare_recovery() {
  log "WARN: Existing morning report is missing or stale, attempting inline prepare recovery."
  set +e
  "$ROOT/scripts/prepare_morning_report.sh" 2>&1 | tee -a "$LOG_FILE"
  local prepare_exit=$?
  set -e
  PREPARE_REFRESH_STATUS="$(read_prepare_refresh_status)"
  PREPARE_KLAVIYO_REFRESH_STATUS="$(read_prepare_klaviyo_status)"
  if [[ "$prepare_exit" -ne 0 ]]; then
    log "ERROR: Inline prepare recovery failed with exit $prepare_exit"
    exit "$prepare_exit"
  fi
}

PREPARE_REFRESH_STATUS="$(read_prepare_refresh_status)"
PREPARE_KLAVIYO_REFRESH_STATUS="$(read_prepare_klaviyo_status)"

if [[ ! -f "$MESSAGE_FILE" ]]; then
  attempt_prepare_recovery
fi

if ! validate_report_for_yesterday; then
  attempt_prepare_recovery
  validate_report_for_yesterday
fi

if [[ ! -f "$MESSAGE_FILE" ]]; then
  log "ERROR: Missing morning report message file: $MESSAGE_FILE"
  exit 1
fi

if [[ "$PREPARE_REFRESH_STATUS" -ne 0 ]]; then
  log "WARN: Using last successful generated morning report because prepare step failed (exit $PREPARE_REFRESH_STATUS)."
fi

MESSAGE_CONTENT="$(cat "$MESSAGE_FILE")"
if [[ -n "$DETAIL_URL" && "$MESSAGE_CONTENT" != *"$DETAIL_URL"* ]]; then
  MESSAGE_CONTENT="$MESSAGE_CONTENT

Detail: $DETAIL_URL"
fi

if [[ -z "$TARGETS_RAW" ]]; then
  log "ERROR: MORNING_REPORT_TARGET is empty"
  exit 1
fi

if [[ "$CHANNEL" != "telegram" ]]; then
  log "ERROR: Unsupported MORNING_REPORT_CHANNEL=$CHANNEL"
  exit 1
fi

if [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  log "ERROR: TELEGRAM_BOT_TOKEN is not available"
  exit 1
fi

export TELEGRAM_BOT_TOKEN
export TARGETS_RAW
export MESSAGE_CONTENT
export MORNING_REPORT_DRY_RUN="${MORNING_REPORT_DRY_RUN:-0}"
export MORNING_REPORT_ALERT_TARGET="${MORNING_REPORT_ALERT_TARGET:-}"
export PREPARE_REFRESH_STATUS
export KLAVIYO_REFRESH_STATUS="$PREPARE_KLAVIYO_REFRESH_STATUS"
export DELIVERY_STATE_FILE
export REPORT_JSON

python3 scripts/morning_report_helper.py deliver 2>&1 | tee -a "$LOG_FILE"

# Update Výsledky eshopu Google Sheet
log "INFO: Updating eshop GSheet..."
set +e
python3 scripts/update_eshop_gsheet.py 2>&1 | tee -a "$LOG_FILE"
GSHEET_STATUS=$?
set -e
if [[ "$GSHEET_STATUS" -ne 0 ]]; then
  log "WARN: GSheet update failed (exit $GSHEET_STATUS) — sheet NOT updated for this run"
else
  log "INFO: GSheet updated OK"
fi
