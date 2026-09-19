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

# launchd runs a missed StartCalendarInterval job when the Mac wakes, and
# snapshot.py dates its output by the clock. A Friday run caught up on
# Saturday morning would therefore file Friday's closes under Saturday - the
# false history point this schedule exists to avoid, in a series that cannot
# be corrected afterwards. A skipped day is honest; a mislabelled one is not.
#
# Set only by the launch agent, so running this by hand is never suppressed.
if [ -n "${ASSET_TAKE_SCHEDULED_AT:-}" ]; then
    # The window alone is not enough. A Friday job caught up when the Mac
    # wakes on Saturday evening lands within 90 minutes of *Saturday's*
    # schedule and would pass - filing Friday's closes under Saturday, which
    # is the whole failure. The agent only ever fires Mon-Fri, so a scheduled
    # run on any other day is by definition a catch-up.
    if [ "$(date +%u)" -gt 5 ]; then
        echo "$(date '+%Y-%m-%d %H:%M %Z'): skipped, not a weekday. The agent"
        echo "  fires Mon-Fri, so this is a missed run caught up after a wake;"
        echo "  it would date the previous session's closes with today."
        exit 0
    fi
    due=$(date -j -f "%Y-%m-%d %H:%M" \
        "$(date +%F) $ASSET_TAKE_SCHEDULED_AT" +%s 2>/dev/null || echo 0)
    now=$(date +%s)
    drift=$(( now > due ? now - due : due - now ))
    if [ "$due" -gt 0 ] && [ "$drift" -gt 5400 ]; then
        echo "$(date '+%Y-%m-%d %H:%M %Z'): skipped, more than 90 minutes from"
        echo "  the $ASSET_TAKE_SCHEDULED_AT schedule. A catch-up run would date"
        echo "  today's snapshot with the previous session's closes."
        exit 0
    fi
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at $PY - see README" >&2; exit 1; }

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
"$PY" snapshot.py
"$PY" dashboard.py
