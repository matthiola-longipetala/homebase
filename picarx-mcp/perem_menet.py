#!/usr/bin/env python3
"""perem_menet.py — FIRST LIVE CLIFF-BRAKE TRIAL: micro-step progress with a ToF depth
reading BETWEEN steps; at an edge an IMMEDIATE stop + a local shout.

Safety plan (table trial, with a human ready to catch the robot):
- step = 0.12 s @ speed 30 (~3-4 cm), FOLLOWED BY a mandatory depth reading
- None reading (blind sensor) = IMMEDIATE STOP (fail-safe, we do not drive blind)
- alarm threshold: baseline*1.5 or baseline+80 mm (same as cliff.py)
- max step count limit — it stops even without an edge
- the shout is LOCAL espeak (no cloud latency)

Usage: python3 -u perem_menet.py [baseline_mm] [max_steps]
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
import cliff  # ToF init (i2c, patient reader on the slow bus)

from picarx import Picarx

SPD = 30
LEPES_S = 0.12
alap = float(sys.argv[1]) if len(sys.argv) > 1 else None
max_lepes = int(sys.argv[2]) if len(sys.argv) > 2 else 14

if alap is None:
    vals = [cliff.read_mm(samples=3) for _ in range(3)]
    vals = [v for v in vals if v]
    if not vals:
        print("no ToF data while stationary — NOT starting!", flush=True)
        sys.exit(1)
    alap = sorted(vals)[len(vals) // 2]
kuszob = max(alap * 1.5, alap + 80)
print(f"EDGE RUN: baseline {alap:.0f} mm, threshold {kuszob:.0f} mm, "
      f"max {max_lepes} steps ({LEPES_S}s @ {SPD})", flush=True)

px = Picarx()
px.set_dir_servo_angle(0)
time.sleep(0.2)
subprocess.run(["espeak", "-v", os.environ.get("ROBOT_ESPEAK_VOICE", "en"), "-a", "170", "Starting."], capture_output=True)

ok_db = 0
try:
    for i in range(1, max_lepes + 1):
        d = cliff.read_mm(samples=2, timeout_s=1.2)
        if d is None:
            print(f"  [{i}] ToF BLIND — safety STOP!", flush=True)
            subprocess.run(["espeak", "-v", os.environ.get("ROBOT_ESPEAK_VOICE", "en"), "-a", "180", "I cannot see, stopping!"],
                           capture_output=True)
            break
        if d > kuszob:
            print(f"CLIFF! {d:.0f} mm (baseline {alap:.0f}) — STOP before step {i}", flush=True)
            subprocess.run(["espeak", "-v", os.environ.get("ROBOT_ESPEAK_VOICE", "en"), "-a", "190", "Cliff, stop!"],
                           capture_output=True)
            break
        print(f"  [{i}] ok {d:.0f} mm — stepping", flush=True)
        ok_db += 1
        px.forward(SPD); time.sleep(LEPES_S); px.forward(0)
        time.sleep(0.05)
    else:
        print(f"({max_lepes} steps done, no edge — stopping)", flush=True)
finally:
    px.forward(0)
print(f"END: {ok_db} steps completed", flush=True)
