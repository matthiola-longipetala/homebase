# -*- coding: utf-8 -*-
"""jonapot.py — session-start ritual in ONE command.

Runs the preflight, reads the battery, and greets in the robot's VOICE according to
the TIME OF DAY (a fixed "Good morning" was a bit too time-bound :)), then briefly
announces its status. The text goes to the TTS through a file (safe for any characters).

Usage:  PYTHONUTF8=1 python3 jonapot.py
Exit code = the preflight's exit code (0 = all green).
"""
import os
import datetime
import subprocess
import sys

BASE = os.path.expanduser("~/picarx-mcp")


def koszones(ora):
    if 5 <= ora < 9:
        return "Good morning"
    if 9 <= ora < 12:
        return "Have a nice morning"
    if 12 <= ora < 18:
        return "Good afternoon"
    if 18 <= ora < 22:
        return "Good evening"
    return "I could say good night, but since we are both awake: hi"


# 1) preflight
pf = subprocess.run(["python3", f"{BASE}/preflight.py"],
                    capture_output=True, text=True, timeout=120)
print(pf.stdout, end="")
n_ok = pf.stdout.count("[OK]")
n_warn = pf.stdout.count("[WARN]")
n_fail = pf.stdout.count("[FAIL]")

# 2) battery
try:
    v, pct, state = open("/tmp/battery_status").read().split()
    akku = f"my battery is at {pct} percent"
    if state == "WARN":
        akku += ", which is already a warning level — the charger would be nice"
except Exception:
    akku = "I cannot read my battery status right now"

# 3) text according to the time of day
ora = datetime.datetime.now().hour
if n_fail == 0 and n_warn == 0:
    helyzet = f"all my systems are green, all {n_ok} checks passed"
elif n_fail == 0:
    helyzet = f"{n_ok} checks passed, with {n_warn} warnings"
else:
    helyzet = (f"{n_ok} checks passed, but I found {n_fail} errors — "
               f"please look at the preflight output")

# The listener's name comes from ROBOT_OWNER_NAME (optional; without it the greeting has no name).
_nev = os.environ.get("ROBOT_OWNER_NAME", "").strip()
szoveg = f"{koszones(ora)}{', ' + _nev if _nev else ''}! I am awake: {helyzet}, and {akku}. Let's begin!"
open("/tmp/jonapot.txt", "w", encoding="utf-8").write(szoveg)

# 4) speak it in the robot's voice
subprocess.run(["python3", f"{BASE}/robot_say.py", "--file", "/tmp/jonapot.txt"],
               timeout=120)
sys.exit(1 if n_fail else 0)
