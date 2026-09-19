#!/bin/bash
# Install (or remove) the weekday schedule for bin/daily.sh.
#
#     bin/install-schedule.sh            # install, weekdays at 23:00 local
#     bin/install-schedule.sh --at 18:30 # a different time
#     bin/install-schedule.sh --remove
#
# launchd rather than cron: it catches up a run missed because the Mac was
# asleep, which cron does not, and a missed day cannot be recovered.
set -euo pipefail

LABEL="com.aleksbal.asset-take.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AT="23:00"

while [ $# -gt 0 ]; do
    case "$1" in
        --remove) launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
                  rm -f "$PLIST"; echo "removed $LABEL"; exit 0 ;;
        --at)     AT="$2"; shift 2 ;;
        *)        echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

HOUR="${AT%%:*}"; MINUTE="${AT##*:}"

# The effective data directory, asked of paths.py rather than assumed, so an
# ASSET_TAKE_DATA override reaches the scheduled run. launchd does not inherit
# the installing shell's environment: without this the agent would fall back
# to $ROOT/data and quietly value a different portfolio than manual commands,
# or fail outright because positions.csv is elsewhere.
DATA="$("$ROOT/.venv/bin/python" -c 'import paths; print(paths.DATA)')"
LOGS="$DATA/logs"
mkdir -p "$HOME/Library/LaunchAgents" "$LOGS"

# & and < are legal in a macOS directory name and would produce a plist that
# launchctl cannot parse. Escaped rather than trusted: the failure would be a
# schedule that silently never ran.
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

# 23:00 by default: after the US close, so both the European and the US
# listings have settled. The snapshot is a point-in-time valuation and any
# consistent hour would do, but a settled close is what the price series can
# actually record.
{
    printf '<?xml version="1.0" encoding="UTF-8"?>\n'
    printf '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
    printf '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    printf '<plist version="1.0"><dict>\n'
    printf '  <key>Label</key><string>%s</string>\n' "$(xml "$LABEL")"
    printf '  <key>ProgramArguments</key><array>\n'
    printf '    <string>/bin/bash</string><string>%s/bin/daily.sh</string>\n' "$(xml "$ROOT")"
    printf '  </array>\n'
    printf '  <key>EnvironmentVariables</key><dict>\n'
    printf '    <key>ASSET_TAKE_DATA</key><string>%s</string>\n' "$(xml "$DATA")"
    printf '    <key>ASSET_TAKE_SCHEDULED_AT</key><string>%s</string>\n' "$(xml "$AT")"
    printf '  </dict>\n'
    printf '  <key>StartCalendarInterval</key><array>\n'
    for d in 1 2 3 4 5; do
        printf '    <dict><key>Weekday</key><integer>%d</integer>' "$d"
        printf '<key>Hour</key><integer>%d</integer>' "$((10#$HOUR))"
        printf '<key>Minute</key><integer>%d</integer></dict>\n' "$((10#$MINUTE))"
    done
    printf '  </array>\n'
    printf '  <key>StandardOutPath</key><string>%s/daily.log</string>\n' "$(xml "$LOGS")"
    printf '  <key>StandardErrorPath</key><string>%s/daily.log</string>\n' "$(xml "$LOGS")"
    printf '  <key>RunAtLoad</key><false/>\n'
    printf '</dict></plist>\n'
} > "$PLIST"

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"

echo "installed $LABEL - weekdays at $AT"
echo "  data:    $DATA"
echo "  log:     $LOGS/daily.log"
# Not `launchctl kickstart`: that starts the agent with the schedule variable
# set, so the catch-up guard would skip it at any other time of day - an
# advertised command that silently does nothing.
echo "  run now: $ROOT/bin/daily.sh"
echo "  remove:  $ROOT/bin/install-schedule.sh --remove"
