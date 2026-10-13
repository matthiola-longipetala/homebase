#!/usr/bin/env python3
"""leallit.py — GENTLE SHUTDOWN: safely shutting the Pi down before the power switch.

Written after an SD card died (the file system had accumulated dozens of fsck scars).

WHY IT IS NEEDED: cutting the power with the switch can hit the card mid-write — every
such occasion wounds the file system. This script first puts everything to sleep nicely,
syncs the disk, and shuts the system down in software.

USAGE (the agent calls it over ssh at the end of a session, or a human runs it by hand):
    python3 leallit.py            # with a spoken espeak goodbye
    python3 leallit.py --nema     # silent (no voice)

⚠️ ONE-TIME PREREQUISITE (run by the owner, needs sudo — recipe in eszkoztar.md):
    echo 'pi ALL=(ALL) NOPASSWD: /usr/sbin/shutdown' | sudo tee /etc/sudoers.d/010_pi-shutdown
    sudo chmod 440 /etc/sudoers.d/010_pi-shutdown

Press the power switch ONLY when the green ACT LED has gone dark FOR GOOD
(at the end of the shutdown it blinks ~10 times as a goodbye — that is the signal).
"""
import os
import subprocess
import sys
import time

NEMA = "--nema" in sys.argv

# What to put to sleep (battery_guard is DELIBERATELY not here — it is a root systemd
# service, and the shutdown closes it cleanly anyway):
ALTATANDO = [
    "robot_listen_loop",
    "orszem.py",
    "speaker_daemon",
    "server.py",          # picarx-mcp MCP server
    "go2rtc",             # live video stream, if running
    "claude_reply_watcher",
]


def fut(cmd, **kw):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15, **kw)
    except Exception:
        return None


def main():
    print("=== GENTLE SHUTDOWN ===")

    # 0) lamp off, if it was left on
    fut("cd ~/picarx-mcp && python3 lampa.py off 2>/dev/null")

    # 1) goodbye (local espeak — does not depend on the network; robot_say is deliberately
    #    not used: it is a cloud TTS + speaker chain, slow and fragile at shutdown)
    if not NEMA:
        fut(f'espeak -v {os.environ.get("ROBOT_ESPEAK_VOICE", "en")} "Going to sleep. Good night!" 2>/dev/null')

    # 2) gently stop the known processes
    for minta in ALTATANDO:
        r = fut(f"pgrep -f '[{minta[0]}]{minta[1:]}'")
        if r and r.stdout.strip():
            print(f"  stopping: {minta}")
            fut(f"pkill -TERM -f '[{minta[0]}]{minta[1:]}'")
    time.sleep(2)
    # whatever did not listen to TERM:
    for minta in ALTATANDO:
        r = fut(f"pgrep -f '[{minta[0]}]{minta[1:]}'")
        if r and r.stdout.strip():
            print(f"  stubborn, harder: {minta}")
            fut(f"pkill -KILL -f '[{minta[0]}]{minta[1:]}'")

    # 3) disk sync (twice, as the old-timers taught)
    print("  disk sync...")
    fut("sync")
    time.sleep(1)
    fut("sync")

    # 4) system shutdown
    print("  system shutdown — press the switch ONLY after the green LED has gone dark for good!")
    r = fut("sudo -n /usr/sbin/shutdown -h now")
    if r is not None and r.returncode != 0:
        print("")
        print("⚠️  The sudo permission is missing! One-time setup (with sudo):")
        print("  echo 'pi ALL=(ALL) NOPASSWD: /usr/sbin/shutdown' | sudo tee /etc/sudoers.d/010_pi-shutdown")
        print("  sudo chmod 440 /etc/sudoers.d/010_pi-shutdown")
        sys.exit(1)


if __name__ == "__main__":
    main()
