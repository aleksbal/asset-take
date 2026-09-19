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

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at $PY - see README" >&2; exit 1; }

echo "=== $(date '+%Y-%m-%d %H:%M:%S %Z') ==="
"$PY" snapshot.py
"$PY" dashboard.py
