#!/bin/bash
# One day's valuation: archive the snapshot, then re-render the dashboard.
#
# Scheduled rather than run by hand because the history series cannot be
# backfilled - a day nobody ran this is gone for good, and day-over-day and
# drift analysis are the only reasons the series exists.
#
# Weekdays only (see the launchd plist): a weekend run would store Friday's
# closes under Saturday's date and add a flat point to the value chart. The
# per-listing price series is already immune - it records a close under the
# session it settled in - but the snapshot series dates itself by the run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Run only when this is the scheduled slot itself, never a catch-up.
#
# launchd starts a missed StartCalendarInterval job when the Mac wakes, and
# snapshot.py dates its output by the clock - so a Friday run caught up later
# files Friday's closes under whatever day it woke on. The history series
# cannot be corrected afterwards, so a skipped day is the better outcome.
#
# The slot is: a weekday, at or after today's due time, within 90 minutes of
# it. All three are needed. A catch-up can land on a weekend (Friday's job
# waking Saturday evening sits an hour from Saturday's due time), and it can
# land early on a later weekday (Monday's job waking Tuesday at 22:00 sits an
# hour *before* Tuesday's). A calendar job cannot legitimately fire before its
# own due time, so anything earlier than that is a catch-up by definition.
#
# ASSET_TAKE_SCHEDULED_AT is set only by the launch agent, so running this
# script by hand is never suppressed.
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
