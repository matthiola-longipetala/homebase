#!/bin/bash
# (Re)start the speaker-identification daemon. Keep this script in speaker_id/ (a persistent
# location) — a copy under /tmp does not survive a reboot.
# Call it so that the daemon's name/path does NOT appear in the calling ssh command line
# (otherwise pkill -f would also kill the calling shell):
#   ssh <user>@<robot-host> 'bash ~/picarx-mcp/speaker_id/restart_spk.sh'
cd "$HOME/picarx-mcp"
# The pattern is split ("spea""ker") so this script's own command line never matches it.
pkill -f "spk-venv/bin/python .*spea""ker_daemon" 2>/dev/null; sleep 1
PYTHONUTF8=1 nohup "$HOME/picarx-mcp/speaker_id/.spk-venv/bin/python" "$HOME/picarx-mcp/speaker_id/speaker_daemon.py" > /tmp/speaker_daemon.log 2>&1 &
echo "started $!"
