#!/usr/bin/env python3
"""perem_demo.py — table-edge demo: VL53L1X guard + IMMEDIATE local voice (espeak).

First live cliff-guard trial (on a table, a human pushes the robot towards
the edge — the wheels do NOT drive). The alarm sounds locally (espeak, no cloud
latency); the stdout event goes to the cloud brain.

Usage: python3 -u perem_demo.py [baseline_mm]
  (without a baseline: the median of the initial readings becomes the table baseline)
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
import cliff

alap = float(sys.argv[1]) if len(sys.argv) > 1 else None
if alap is None:
    vals = [cliff.read_mm(samples=3) for _ in range(3)]
    vals = [v for v in vals if v]
    if not vals:
        print("no ToF data — cable/orientation?", flush=True)
        sys.exit(1)
    alap = sorted(vals)[len(vals) // 2]

kuszob = max(alap * 1.5, alap + 80)
print(f"EDGE GUARD ACTIVE: baseline {alap:.0f} mm, alarm above {kuszob:.0f} mm", flush=True)

riadt = 0
try:
    while True:
        d = cliff.read_mm(samples=2, timeout_s=1.5)
        if d and d > kuszob:
            riadt += 1
            print(f"CLIFF! {d:.0f} mm (baseline {alap:.0f})", flush=True)
            subprocess.run(["espeak", "-v", os.environ.get("ROBOT_ESPEAK_VOICE", "en"), "-a", "180", "Cliff, stop!"],
                           capture_output=True)
        elif d:
            print(f"ok {d:.0f} mm", flush=True)
        if riadt >= 15:
            print("(15 alarms reached — the demo guard stops)", flush=True)
            break
        time.sleep(0.1)
except KeyboardInterrupt:
    print("done", flush=True)
