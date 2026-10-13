#!/usr/bin/env python3
"""rear_sonar.py — REAR HC-SR04 parking sensor.

Wiring (as actually connected): Trig -> D1 (GPIO4), Echo -> D0 (GPIO17),
VCC -> 3V3 (a 3.3 V module!), GND -> GND.
The FRONT sonar is px.ultrasonic (library default D2/D3); so the REAR one is a separate
Ultrasonic instance, directly from robot_hat (no Picarx() needed).

Usage:
  python3 rear_sonar.py          # one-shot median distance (cm)
  python3 rear_sonar.py loop     # continuous reading, until Ctrl+C
"""
import sys
import time

from robot_hat import Ultrasonic, Pin

rear = Ultrasonic(Pin("D1"), Pin("D0"))


def read_cm(samples=6):
    """Median distance in cm from the rear sonar; None if there is no valid echo."""
    vals = []
    for _ in range(samples):
        try:
            d = rear.read()
        except Exception:
            d = -1
        if d and d > 0:
            vals.append(d)
        time.sleep(0.02)
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        try:
            while True:
                d = read_cm()
                print(f"REAR sonar: {round(d, 1) if d else 'no echo'} cm")
                time.sleep(0.3)
        except KeyboardInterrupt:
            print("done")
    else:
        d = read_cm()
        print(f"REAR sonar: {round(d, 1) if d else 'no echo'} cm")
