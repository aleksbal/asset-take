#!/bin/bash
# Takes the day's snapshot, then re-renders the dashboard. Scheduled on
# weekdays by bin/install-schedule.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# When started by the launch agent (ASSET_TAKE_SCHEDULED_AT set), run only
# on a weekday, at or up to 90 minutes after the scheduled time. This skips
# runs launchd catches up after the Mac wakes. Manual runs are not affected.
if [ -n "${ASSET_TAKE_SCHEDULED_AT:-}" ]; then
    skip=""
    due=$(date -j -f "%Y-%m-%d %H:%M" \
        "$(date +%F) $ASSET_TAKE_SCHEDULED_AT" +%s 2>/dev/null || echo 0)
    since=$(( $(date +%s) - due ))

    if [ "$due" -eq 0 ]; then
        skip="ASSET_TAKE_SCHEDULED_AT is not a readable HH:MM"
    elif [ "$(date +%u)" -gt 5 ]; then
        skip="not a weekday; the agent fires Mon-Fri"
    elif [ "$since" -lt -60 ]; then       # tolerate a little clock skew
        skip="before today's $ASSET_TAKE_SCHEDULED_AT slot"
    elif [ "$since" -gt 5400 ]; then
        skip="more than 90 minutes after today's $ASSET_TAKE_SCHEDULED_AT slot"
    fi

    if [ -n "$skip" ]; then
        echo "$(date '+%Y-%m-%d %H:%M %Z'): skipped - $skip."
        echo "  This is a missed run caught up after a wake. Taking it now"
        echo "  would date the previous session's closes with today."
        exit 0
    fi
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at $PY - see README" >&2; exit 1; }

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
"$PY" snapshot.py
"$PY" dashboard.py
