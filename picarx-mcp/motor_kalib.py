#!/usr/bin/env python3
"""motor_kalib.py — MEASURING and setting the balance of the two rear motors.

Background and mechanism: see the header of `core/motor_balance.py`. In short: the right rear
motor spins slower (spotted by holding the robot with wheels in the air, also on charger) -> the
robot arcs to the right regardless of steering, and covers a shorter distance. It is an
INTERMITTENT phenomenon (it has come and gone before) — so we measure rather than hard-code.

## What it does
With straight steering (0°) it drives short legs and measures the DRIFT RATE with the IMU
(deg/s). It tries several correction values, then LINEARLY interpolates between the two best
points the value where the drift is zero — finally it saves it (`motor_balance.json`).

Sign convention: + drift = turning RIGHT = the LEFT motor is faster -> the right one must be
sped up, i.e. the `jobb` (right) cali is NEGATIVE (picarx SUBTRACTS the cali from the PWM).

## Usage
    python3 motor_kalib.py               # full measurement (0, -4, -8, -12), then save
    python3 motor_kalib.py --no-save     # measure only, do not save
    python3 motor_kalib.py --t 1.6       # longer pushes (more accurate, needs more room)

SAFETY: the robot must be ON THE FLOOR with ~1.5 m of free space in front. Before every push
it checks the sonar (skips below the minimum), and after every push it reverses back so it
stays in place. NEVER run it on a table or in someone's hands.
"""
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))

import imu
from core import motor_balance
from core.motion import sonar_cm
from picarx import Picarx

SPEED = 45
PULSE_S = 1.4
SONAR_MIN = 30.0
PROBA_ERTEKEK = [0, -4, -8, -12]   # speed-up of the RIGHT motor (negative = faster)


def heading():
    try:
        return imu.read_once()["heading"]
    except Exception:
        return None


def delta(a, b):
    return (b - a + 540) % 360 - 180


def egy_meres(px, jobb_corr, pulse):
    """One push with the given correction; returns: drift rate in deg/s (None if skipped)."""
    d = sonar_cm(px)
    if 0 < d < SONAR_MIN:
        print(f"  right {jobb_corr:+3d}: SKIPPED (sonar {d:.0f} cm — no room)")
        return None
    px.cali_speed_value = [0, jobb_corr]
    px.set_dir_servo_angle(-25); time.sleep(0.3)
    px.set_dir_servo_angle(0); time.sleep(0.5)
    h0 = heading(); time.sleep(0.2); h0 = heading()
    if h0 is None:
        print("  IMU gives no heading — stop!")
        return None
    px.forward(SPEED); time.sleep(pulse); px.stop(); time.sleep(0.9)
    h1 = heading()
    rate = delta(h0, h1) / pulse
    print(f"  right {jobb_corr:+3d}: drift {delta(h0, h1):+6.1f}° / {pulse} s "
          f"= {rate:+5.1f} deg/s")
    # back to the start (with the same correction, so reversing is straight too)
    px.backward(SPEED); time.sleep(pulse + 0.05); px.stop(); time.sleep(0.7)
    return rate


def main():
    raw = sys.argv[1:]
    save = "--no-save" not in raw
    pulse = PULSE_S
    if "--t" in raw:
        pulse = float(raw[raw.index("--t") + 1])

    px = Picarx()
    print(f"MOTOR BALANCE MEASUREMENT (push {pulse} s @speed {SPEED})")
    print("  (+ rate = drifts right = the left motor is faster)")

    meresek = []
    for corr in PROBA_ERTEKEK:
        r = egy_meres(px, corr, pulse)
        if r is not None:
            meresek.append((corr, r))
            # once the sign has flipped, no need to waste more room/battery
            if len(meresek) >= 2 and meresek[-1][1] * meresek[0][1] < 0:
                break

    px.cali_speed_value = [0, 0]
    if len(meresek) < 2:
        print("\nTOO FEW MEASUREMENTS — cannot make a recommendation (room/IMU?).")
        return

    print("\n--- EVALUATION ---")
    (c1, r1), (c2, r2) = meresek[0], meresek[-1]
    if r1 == r2:
        print("  the correction did not change the drift → NOT a motor asymmetry; "
              "check the steering/traction (vezetes_alapok.md)")
        return
    # linear interpolation to zero drift
    nulla = c1 + (0.0 - r1) * (c2 - c1) / (r2 - r1)
    javasolt = int(round(nulla))
    javasolt = max(-motor_balance.MAX_CORR, min(motor_balance.MAX_CORR, javasolt))
    print(f"  measurements: {meresek}")
    print(f"  → estimated zero-drift point: right = {nulla:.1f}  → recommended: {javasolt:+d}")

    if abs(javasolt) >= motor_balance.MAX_CORR:
        print(f"  ⚠️ the recommendation hits the limit ({motor_balance.MAX_CORR}) — this is more likely a "
              f"MECHANICAL problem (wheel/axle/motor), have it checked by hand, do not just mask it!")

    if save:
        d = motor_balance.save(0, javasolt, merve=time.strftime("%Y-%m-%d %H:%M"),
                               megjegyzes=f"measurements: {meresek}")
        print(f"  SAVED: {d['bal']:+d} / {d['jobb']:+d} → {motor_balance.BALANCE_PATH}")
    else:
        print("  (--no-save: not saved)")
    print("done")


if __name__ == "__main__":
    main()
