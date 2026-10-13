#!/usr/bin/env python3
"""cliff.py — DOWNWARD-facing VL53L1X ToF cliff sensor (terrace edge!).

Wiring: QWIIC chain from the HAT's white SH1.0 socket (hardware i2c-1, address 0x29).
The sonar is blind downwards — THIS sensor sees the edge. For a smoke test it is fine held
in the hand pointing down; for real use it needs an overhanging mount (open item).
Lib: pip3 install adafruit-circuitpython-vl53l1x

Logic: baseline height (distance to the floor) + if the measured distance is SIGNIFICANTLY
larger (threshold: baseline*1.5 or +80 mm), it is a CLIFF -> STOP flag for the reflex layer.

Usage:
  python3 cliff.py                # one-shot distance (mm)
  python3 cliff.py loop           # continuous
  python3 cliff.py guard <base>   # guard mode: baseline height in mm; shouts CLIFF! on a jump
"""
import sys
import time

import board
import adafruit_vl53l1x

i2c = board.I2C()  # hardware i2c-1
tof = adafruit_vl53l1x.VL53L1X(i2c)  # address: 0x29
tof.distance_mode = 2  # long
tof.timing_budget = 100
tof.start_ranging()


def read_mm(samples=5, timeout_s=3.0):
    """Median distance in mm. On the bus slowed to 10 kHz (because of the BNO055) a single
    measurement takes much longer -> we wait patiently for data_ready."""
    vals = []
    t0 = time.time()
    while len(vals) < samples and (time.time() - t0) < timeout_s:
        if tof.data_ready:
            d = tof.distance  # cm float or None
            if d is not None:
                vals.append(d * 10.0)
            tof.clear_interrupt()
        time.sleep(0.03)
    if not vals:
        return None
    vals.sort()
    return round(vals[len(vals) // 2])


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        try:
            while True:
                print(f"ToF downward: {read_mm() or 'no data'} mm")
                time.sleep(0.2)
        except KeyboardInterrupt:
            print("done")
    elif mode == "guard":
        alap = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0
        kuszob = max(alap * 1.5, alap + 80)
        print(f"Guard mode: baseline={alap} mm, alarm > {kuszob} mm")
        try:
            while True:
                d = read_mm(samples=3)
                if d and d > kuszob:
                    print(f"⚠️ CLIFF! ({d} mm)")
                else:
                    print(f"ok ({d} mm)")
                time.sleep(0.15)
        except KeyboardInterrupt:
            print("done")
    else:
        print(f"{read_mm()} mm")
