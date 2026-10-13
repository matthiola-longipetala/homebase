"""turn90.py — FIXED-ANGLE turn in place in ONE command, without the camera.

Problem: turn_to sweeps+localises after every K-cycle -> a flood of data in which the
cloud brain loses the thread ("how many degrees have I turned already?"). In a known,
clearly visible situation (e.g. "turn right 90, then go") continuous measurement is pointless.

This tool turns in an OPEN LOOP: it computes the number of cycles from the rule of thumb
(1 in-place K-cycle ~ 25 degrees), runs them, and ONLY AT THE END suggests a check.
Two commands = 180 degrees, without data noise.

Usage:
    python3 turn90.py right          # 90 degrees right (CW)   (also: jobbra)
    python3 turn90.py left           # 90 degrees left (CCW)   (also: balra)
    python3 turn90.py right 45       # 45 degrees right
    python3 turn90.py left 180       # full turnaround to the left

WARNING: do not turn NEXT TO an obstacle/wall (asymmetric cycle -> drift); back away from
it first (go_dist.py -- -30). The forward arc is sonar-braked, the backward arc is blind.

NOTE: the output lines (FORDULAS / elore blokkolva / FIGYELEM ... fek / KESZ / vege) are
parsed or mirrored by utvonalak/route_runner.py — keep their wording unchanged.
FORDULAS = TURNING, elore blokkolva = forward blocked, FIGYELEM = WARNING, fek = brake,
KESZ = DONE, vege = end.
"""
import os
import sys, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.motion import forward_guarded
from picarx import Picarx

CYCLE_DEG = 22.5
STEER = 25
FWD_DUR = 0.40
REV_DUR = 0.40       # FWD==REV -> turns in place (see turn_to.py)

if len(sys.argv) < 2 or sys.argv[1].lower() not in ("left", "right", "balra", "jobbra"):
    print("usage: turn90.py left|right [deg]   (default: 90)"); sys.exit(1)
cw = sys.argv[1].lower() in ("right", "jobbra")
deg = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
cycles = max(1, int(round(deg / CYCLE_DEG)))

px = Picarx()
for a in (15, -15, 0): px.set_dir_servo_angle(a); time.sleep(0.2)  # steering wiggle

# NOTE: the Hungarian (unaccented) output lines below are a parsed protocol: utvonalak/route_runner.py
# looks for "FIGYELEM" + "fek" (braked) and mirrors these lines in its dry run. Keep them byte-identical.
print(f"FORDULAS: {deg:.0f} fok {'JOBBRA/CW' if cw else 'BALRA/CCW'} = {cycles} K-ciklus "
      f"(~{cycles*CYCLE_DEG:.0f} fok), kamera nelkul")
blocked = 0
try:
    for c in range(cycles):
        fwd_steer = STEER if cw else -STEER
        px.set_dir_servo_angle(fwd_steer); time.sleep(0.12)
        stopped, dd = forward_guarded(px, 30, FWD_DUR)
        if stopped:
            blocked += 1
            print(f"  [{c+1}/{cycles}] elore blokkolva ({dd:.0f}cm) - csak hatra-iv (a ciklus ~felet fordul!)")
        else:
            print(f"  [{c+1}/{cycles}] ok")
        rev_steer = -STEER if cw else STEER
        px.set_dir_servo_angle(rev_steer); time.sleep(0.12)
        px.forward(-30); time.sleep(REV_DUR); px.forward(0)
        px.set_dir_servo_angle(0); time.sleep(0.2)
finally:
    px.forward(0)
    # zeroing with a wiggle: after a plain 0 command the servo can PHYSICALLY stick in a turned position
    for a in (-12, 12, 0): px.set_dir_servo_angle(a); time.sleep(0.15)

if blocked:
    print(f"FIGYELEM: {blocked} ciklusban fek volt -> a tenyleges szog ~{(cycles-blocked*0.5)*CYCLE_DEG:.0f} fok lehet; ellenorizz (whereami / 1 kep)")
else:
    print(f"KESZ: ~{cycles*CYCLE_DEG:.0f} fok lefordulva. Ha bizonytalan, 1 gyors ellenorzes eleg (NE folyamatos meres).")
print("vege")
