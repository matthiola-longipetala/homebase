#!/usr/bin/env python3
"""set_cali.py — setting the steering zero point (dir_servo) + straightening immediately.

Written while fixing a steering fault after an SD-card rebuild. Tightening a loose
servo-arm screw made the OLD calibration value (picarx_dir_servo = 2) invalid: the geometry
changed, a new zero has to be found. The search uses human feedback (a human looks at the
wheels) — this is more accurate than the noise of a driving-based measurement.

Usage:
    python3 set_cali.py -6        # set it, save it to the config, then steering to 0 (FROM THE LEFT)
    python3 set_cali.py           # only print the current value, do not touch it

Config location: /opt/picar-x/picar-x.conf (written by the picarx library).
"""
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))

from picarx import Picarx


def main():
    px = Picarx()
    print(f"current dir_cali_val: {px.dir_cali_val}")
    if len(sys.argv) < 2:
        print("(nothing changed — give a value, e.g. set_cali.py -6)")
        return
    uj = float(sys.argv[1])
    px.dir_servo_calibrate(uj)
    time.sleep(0.4)
    # zeroing FROM THE LEFT (zero_steer policy: coming from the right it does not reach zero)
    px.set_dir_servo_angle(-25)
    time.sleep(0.5)
    px.set_dir_servo_angle(0)
    time.sleep(0.5)
    print(f"new dir_cali_val: {px.dir_cali_val} — the steering is now at 0 (zeroed from the left)")
    print("done")


if __name__ == "__main__":
    main()
