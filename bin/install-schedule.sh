#!/bin/bash
# Install (or remove) the weekday schedule for bin/daily.sh.
#
#     bin/install-schedule.sh            # install, weekdays at 23:00 local
#     bin/install-schedule.sh --at 18:30 # a different time
#     bin/install-schedule.sh --remove
#
# Writes a launchd agent to ~/Library/LaunchAgents.
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

# --at must be HH:MM.
if ! [[ "$AT" =~ ^([0-9]{1,2}):([0-9]{2})$ ]]; then
    echo "--at must be HH:MM (got '$AT')" >&2; exit 2
fi
HOUR=$((10#${BASH_REMATCH[1]})); MINUTE=$((10#${BASH_REMATCH[2]}))
if [ "$HOUR" -gt 23 ] || [ "$MINUTE" -gt 59 ]; then
    echo "--at must be a real time of day (got '$AT')" >&2; exit 2
fi
AT=$(printf '%02d:%02d' "$HOUR" "$MINUTE")   # what the guard will compare against

# The data directory from paths.py (honours ASSET_TAKE_DATA), passed to the
# agent because launchd does not inherit this shell's environment. Run from
# $ROOT so `import paths` resolves.
DATA="$(cd "$ROOT" && ./.venv/bin/python -c 'import paths; print(paths.DATA)')"
LOGS="$DATA/logs"
mkdir -p "$HOME/Library/LaunchAgents" "$LOGS"

# Escape paths for XML.
xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

# 23:00 by default, after the US close.
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
        printf '<key>Hour</key><integer>%d</integer>' "$HOUR"
        printf '<key>Minute</key><integer>%d</integer></dict>\n' "$MINUTE"
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
# Suggest running daily.sh directly: `launchctl kickstart` would set the
# schedule variable, and the guard would skip the run outside its slot.
echo "  run now: $ROOT/bin/daily.sh"
echo "  remove:  $ROOT/bin/install-schedule.sh --remove"
