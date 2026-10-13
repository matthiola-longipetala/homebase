#!/usr/bin/env python3
"""calib_session.py — BNO055 calibration session in a SINGLE instance.

LESSON: every new BNO055_I2C() instance does a SOFT RESET -> the calibration is lost!
So the whole calibration (dance + holding still) must be done in ONE running process.
This script:
  - writes status ONLY ON CHANGE (Monitor-friendly),
  - when all are 3/3/3/3 -> prints the offsets (they can be restored later!) and exits.

Usage: python3 -u calib_session.py [max_seconds, default 300]
"""
import sys
import time

import board
import adafruit_bno055

max_s = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
i2c = board.I2C()
s = adafruit_bno055.BNO055_I2C(i2c)

print("CALIB_START — dance, then hold still; I only report changes.")
last = None
t0 = time.time()
while time.time() - t0 < max_s:
    try:
        cal = s.calibration_status  # (sys, gyro, accel, mag)
    except Exception as e:
        print(f"READ_ERROR: {e}")
        time.sleep(1)
        continue
    if cal != last:
        print(f"CAL sys{cal[0]} gyro{cal[1]} acc{cal[2]} mag{cal[3]}")
        last = cal
    if cal == (3, 3, 3, 3):
        print("DONE! All 3/3/3/3 — offsets to save:")
        print("OFFSET_ACCEL", s.offsets_accelerometer)
        print("OFFSET_GYRO", s.offsets_gyroscope)
        print("OFFSET_MAG", s.offsets_magnetometer)
        print("RADIUS_ACCEL", s.radius_accelerometer)
        print("RADIUS_MAG", s.radius_magnetometer)
        h = s.euler[0]
        print(f"HEADING_NOW: {round(h,1) if h is not None else None}")
        sys.exit(0)
    time.sleep(0.7)
print("TIMEOUT — got this far:", last)
