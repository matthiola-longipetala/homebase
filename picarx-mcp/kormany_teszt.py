#!/usr/bin/env python3
"""kormany_teszt.py — measuring STEERING SYMMETRY (e.g. after a manual screw fix on the steering linkage).

Why: once the robot arced right on every long run, and the legs covered half the requested
distance. The software suspects (frozen IMU anchor, fine-grained servo writes) were real and
got fixed, but the REAL cause was physical: the steering servo had no effect in the LEFT
range (it turned RIGHT even with −25° steering).

This tool measures that objectively, without a human: with a given steering angle it drives
a short straight leg and measures the actual rotation with the IMU — then after every leg it
reverses back so the robot stays roughly in place.

Healthy picture: the +N° and −N° deflections are roughly MIRROR IMAGES (a few degrees of
asymmetry is normal because of the hardware's pull to the left). Unhealthy picture: the same
sign on both sides.

Usage:
    python3 kormany_teszt.py            # ±12 and ±25 degrees, 0.9 s pushes
    python3 kormany_teszt.py 20         # ±20 degrees only
    python3 kormany_teszt.py 12 25 --t 0.7

SAFETY: before every push the robot checks the sonar (skips the leg below the minimum),
and reverses back after the push. Do NOT run it on a table.
"""
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))

import imu
from core.motion import sonar_cm
from picarx import Picarx

PULSE_S = 0.9      # length of one push
SPEED = 45
SONAR_MIN = 25.0   # we do not start below this


def heading():
    try:
        return imu.read_once()["heading"]
    except Exception:
        return None


def delta(a, b):
    return (b - a + 540) % 360 - 180


def main():
    raw = [a for a in sys.argv[1:]]
    pulse = PULSE_S
    if "--t" in raw:
        i = raw.index("--t")
        pulse = float(raw[i + 1])
        raw = raw[:i] + raw[i + 2:]
    szogek = [float(a) for a in raw] or [12.0, 25.0]

    px = Picarx()
    print(f"STEERING SYMMETRY TEST (push {pulse} s @speed {SPEED})")
    eredmeny = {}

    for sz in szogek:
        for ang in (sz, -sz):
            d = sonar_cm(px)
            if 0 < d < SONAR_MIN:
                print(f"  steering {ang:+6.1f}° — SKIPPED (sonar {d:.0f} cm, no room)")
                continue
            # write the angle with a BIG jump (because of linkage friction; see vezetes_alapok.md)
            px.set_dir_servo_angle(0); time.sleep(0.4)
            px.set_dir_servo_angle(ang); time.sleep(0.8)
            h0 = heading(); time.sleep(0.2); h0 = heading()
            if h0 is None:
                print("  IMU gives no heading — stop, check manually!")
                return
            px.forward(SPEED); time.sleep(pulse); px.stop(); time.sleep(0.9)
            h1 = heading()
            dd = delta(h0, h1)
            eredmeny[ang] = dd
            print(f"  steering {ang:+6.1f}° -> actual rotation {dd:+6.1f}°")
            # back to the start (zeroing FROM THE LEFT — zero_steer policy)
            px.set_dir_servo_angle(-25); time.sleep(0.3)
            px.set_dir_servo_angle(0); time.sleep(0.4)
            px.backward(SPEED); time.sleep(pulse + 0.05); px.stop(); time.sleep(0.6)

    print("\n--- VERDICT ---")
    for sz in szogek:
        j, b = eredmeny.get(sz), eredmeny.get(-sz)
        if j is None or b is None:
            continue
        if j > 0 and b > 0:
            print(f"  ±{sz:.0f}°: ⚠️ UNHEALTHY — turns RIGHT in both directions "
                  f"({j:+.1f}° / {b:+.1f}°) → servo horn screw / wandering zero")
        elif j < 0 and b < 0:
            print(f"  ±{sz:.0f}°: ⚠️ UNHEALTHY — turns LEFT in both directions "
                  f"({j:+.1f}° / {b:+.1f}°) → servo horn screw / wandering zero")
        else:
            aszim = abs(j + b)
            allapot = "HEALTHY" if aszim < 0.5 * max(abs(j), abs(b)) else "slightly asymmetric"
            print(f"  ±{sz:.0f}°: ✅ {allapot} ({j:+.1f}° / {b:+.1f}°, asymmetry {aszim:.1f}°)")
    print("done")


if __name__ == "__main__":
    main()
