#!/bin/zsh
# 由 launchd 每個平日觸發。
set -u

SKILL="$HOME/.claude/skills/workplan"
LOG="$HOME/Library/Logs/workplan.log"

print "=== $(date '+%Y-%m-%d %H:%M:%S') evening ===" >> "$LOG"
/usr/bin/python3 "$SKILL/workplan.py" evening --notify >> "$LOG" 2>&1
