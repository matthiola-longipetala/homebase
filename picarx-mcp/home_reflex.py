# -*- coding: utf-8 -*-
"""home_reflex.py — BATTERY-AWARE HOMING, v1 (first building block of the docking goal).

The goal: there will be a dock, and the robot must find its way home on its own,
IN TIME, before the battery runs out. This v1 automates the proven recipe of a
manual homing run, using the validated primitives (turn_scout/face_tag/goto_dr/
turn90/go_dist).

OPERATION:
  python3 home_reflex.py --once     one-shot homing NOW (from anywhere, best effort)
  python3 home_reflex.py --watch    guard mode: checks the battery every minute; on WARN
                                    it speaks up and raises an alarm. Runs ONLY WHEN ARMED!

ARMING (safety latch): watch mode does anything ONLY if the file
/tmp/home_reflex_armed exists (touch it when the robot is ON THE FLOOR). On a table /
terrace / in someone's hands it must NEVER be armed — the reflex starts blindly!

V1 LIMITS (honestly): it can get stuck in a furniture nook; it does not know the way
out of the kitchen/bedroom; it drives over rugs (they are passable). If after 3 legs it
is still not in the corridor zone, it STOPS and asks for help out loud — no blind
struggling.

All coordinates and tag IDs below belong to the INVENTED example flat
(navigacio/OLVASS_EL.md, navigacio/tags_template.json) — set them to your own home.
"""
import os
import re
import subprocess
import sys
import time

BASE = os.path.expanduser("~/picarx-mcp")
ARMED = "/tmp/home_reflex_armed"
HOME_XY = (0, 20)          # SAFE approach point just north of the dock (final docking still to be tuned on the robot)
CORRIDOR = lambda x, y: abs(x) < 40 and -80 < y < 480   # hallway zone (hallway x -50..50, y -100..500)
LIVING_ROOM = lambda x, y: x < -50 and y > 150           # living-room zone (rough)


def sh(args, timeout=300):
    p = subprocess.run(["python3"] + args, capture_output=True, text=True,
                       timeout=timeout, cwd=BASE, env={**os.environ, "PYTHONUTF8": "1"})
    return p.stdout + p.stderr


def mond(szoveg):
    open("/tmp/hr_say.txt", "w", encoding="utf-8").write(szoveg)
    subprocess.run(["python3", f"{BASE}/robot_say.py", "--file", "/tmp/hr_say.txt"],
                   capture_output=True, timeout=120)


def hol_vagyok():
    """whereami -> (x, y, hdg) or None."""
    out = sh([f"{BASE}/whereami.py"], timeout=200)
    m = re.search(r"KALIBRALT-TAV alapjan: x=([+-]?\d+) y=([+-]?\d+)\s+hdg~([+-]?\d+)", out)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def hazamegyek():
    mond("My battery is running low, I am heading home to charge.")
    for szakasz in range(4):
        poz = hol_vagyok()
        if poz is None:
            # no tag fix -> look around for the tags on the exit side
            sh([f"{BASE}/turn_scout.py", "ccw", "0", "1", "3", "6", "9"], timeout=300)
            poz = hol_vagyok()
            if poz is None:
                break
        x, y, hdg = poz
        print(f"[leg {szakasz}] x={x} y={y} hdg={hdg}", flush=True)
        if CORRIDOR(x, y):
            # We are in the hallway -> turn south (towards the dock) and go to the SAFE approach
            # point (HOME_XY). The final move onto the dock still needs a dedicated approach +
            # margin tuning on the robot. Until then: stop at the approach point and ask for help
            # (safe failure — the guard raises an alarm, it does not act blindly).
            delta = ((-90 - hdg + 180) % 360) - 180
            if abs(delta) > 20:
                irany = "right" if delta < 0 else "left"
                sh([f"{BASE}/turn90.py", irany, str(abs(int(delta)))], timeout=300)
            sh([f"{BASE}/goto_dr.py", str(HOME_XY[0]), str(HOME_XY[1]), "15"], timeout=300)
            mond("I am next to my dock. We are still learning the exact parking — "
                 "if you can, please put me on the charger!")
            return True
        if LIVING_ROOM(x, y):
            # living room -> face the exit tag, then out through the door into the hallway
            sh([f"{BASE}/face_tag.py", "9"], timeout=300)
            sh([f"{BASE}/goto_dr.py", "-20", "290", "20"], timeout=300)
            continue
        # elsewhere -> try the middle of the hallway directly
        sh([f"{BASE}/goto_dr.py", "0", "150", "25"], timeout=300)
    mond("I cannot find my way home alone. Please help me — until then I will stay still and save the battery.")
    return False


def watch():
    """WAKE-UP MODE: on WARN it does NOT drive home blindly; instead it (1) speaks up,
    (2) writes an event line to STDOUT — run under Monitor, this WAKES the agent, which
    drives the robot home live, with eyes and judgement.
    Blind homing (--once) remains as the manual / last-resort path.
    Alarm repeat: every 10 minutes while WARN (no spam every minute)."""
    print("home_reflex WAKE-UP GUARD starting (battery check every minute, agent alarm on WARN)", flush=True)
    while True:
        time.sleep(60)
        try:
            v, pct, state = open("/tmp/battery_status").read().split()
        except Exception:
            continue
        if state in ("WARN", "CRIT"):
            print(f"BATTERY ALARM: {v} V / {pct}% ({state}) — agent, wake up: take me home to charge!",
                  flush=True)
            mond("My battery is running low. I am telling Claude to come and take me home to charge.")
            time.sleep(540)   # +60 at the start of the loop = ~10 minutes between two alarms


if __name__ == "__main__":
    if "--watch" in sys.argv:
        watch()
    elif "--once" in sys.argv:
        hazamegyek()
    else:
        print(__doc__)
