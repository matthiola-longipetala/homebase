#!/usr/bin/env python3
"""go_heading.py — STRAIGHT driving with an IMU heading lock: the cure for pulling left.

The hardware's pull to the left cannot be calibrated out (tanulsagok/vezetes_alapok.md) —
so far we avoided it with short legs + aiming at the right door jamb. This tool uses
the BNO055 heading for steering correction WHILE DRIVING: at the start it locks the
direction and steers back to it at 4 Hz (P controller, ±12° steering).

The sonar brake is active THE WHOLE TIME (as in go_dist); --force = threshold mode,
brake OFF (ONLY at a visually confirmed free opening — threshold protocol).
Forward ONLY; for reversing use go_dist.

GPIO rule: IMU reader + motor in ONE process (import imu + Picarx here).

Usage:
    python3 go_heading.py 100           # 100 cm forward with heading hold (speed 30)
    python3 go_heading.py 150 55        # with momentum
    python3 go_heading.py 60 55 --force # threshold mode: sonar brake OFF

Tuning (to be refined on the robot): KP=0.9, steering ceiling ±12°.
"""
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))

import imu  # with offset restore; BEFORE Picarx

from smart_turn import ang_diff, heading_med  # reuse the circular angle maths

KP = 1.2            # steering degrees / error degree (0.9->1.2: in the first test it corrected only at the end)
MAX_STEER = 12.0    # correction steering ceiling (it should hold, not "turn")
HEAD_PERIOD = 0.15  # heading read interval while driving (0.25->0.15, same reason)
SONAR_STOP_CM = 22.0
POLL_S = 0.05

V_PONTOK = [(30, 28.6), (55, 30.8), (80, 34.9)]


def v_of(speed):
    pts = sorted(V_PONTOK)
    if speed <= pts[0][0]:
        return pts[0][1] * (speed / pts[0][0])
    for (s1, v1), (s2, v2) in zip(pts, pts[1:]):
        if speed <= s2:
            return v1 + (v2 - v1) * (speed - s1) / (s2 - s1)
    return pts[-1][1]


def main():
    FORCE_FLAGS = ("--force", "--no-guard", "--kuszob", "--threshold")
    raw = sys.argv[1:]
    force = any(a in FORCE_FLAGS for a in raw)
    args = [a for a in raw if a != "--" and a not in FORCE_FLAGS]
    if not args:
        print("usage: go_heading.py <cm> [speed] [--force]   (forward only!)")
        sys.exit(1)
    dist_cm = float(args[0])
    if dist_cm <= 0:
        print("forward ONLY — use go_dist.py for reversing")
        sys.exit(1)
    speed = int(args[1]) if len(args) > 1 else 30

    # Wait for the absolute jump after the soft reset BEFORE anchoring (sys==3 + 1 s
    # stable, max 4 s). If it does not settle: IMUPLUS (relative hold — just enough for
    # straight driving, without magnetometer jumps).
    _beallt, _h, _calib = imu.bealt(max_s=4.0)
    if _beallt:
        print(f"IMU settled [{_calib}]")
    else:
        imu.imuplus_mod()
        print(f"⚠️ IMU did not settle within 4 s [{_calib}] → holding in IMUPLUS (relative).")

    target, spread = heading_med()
    if target is None:
        print("IMU gives no heading — use plain go_dist!")
        sys.exit(1)
    if spread is not None and spread > 6.0:
        print(f"⚠️ heading jumps while stationary ({spread:.1f}°) — metal nearby? The hold may be distorted.")
    # Frozen-anchor suspicion (lesson from smart_turn's first live test): if the 5 stationary
    # samples are IDENTICAL, the fusion may be asleep -> re-anchor on the first LIVE reading
    # while driving. spread==0.0 alone is NOT a sufficient filter — after a fresh boot the
    # BNO055's 5 stationary samples can be 0.0/0.0/0.1 (spread>0 -> frozen=False), and the
    # script steered towards the false 0.0° THE WHOLE WAY: it saw a ~65° phantom error and
    # arced right at MAX_STEER (hence "half the distance" — on an arc the net progress is half
    # the path length). Two safety nets: (1) an anchor near 0.0° is suspect FROM THE START;
    # (2) the FIRST live reading is always checked — on a stationary robot a >15° deviation is
    # physically impossible.
    frozen = (spread == 0.0) or abs(target) < 0.5
    first_live = True

    from core.motion import sonar_cm
    from picarx import Picarx
    px = Picarx()
    # Rear motor speed balancing (the right motor is slower — core/motor_balance.py;
    # measured with motor_kalib.py). No file / 0 value -> no-op.
    try:
        from core.motor_balance import apply as _mb_apply
        _mb_apply(px)
    except Exception as _e:
        print(f"  (motor balancing skipped: {_e})")
    for a in (15, -15, 0):
        px.set_dir_servo_angle(a); time.sleep(0.2)
    px.set_dir_servo_angle(0); time.sleep(0.15)

    v = v_of(speed)
    total_s = dist_cm / v
    print(f"TARGET: {dist_cm:.0f} cm @speed {speed} with heading lock ({target:.1f}°, "
          f"~{total_s:.1f} s)" + ("  [THRESHOLD MODE: sonar brake OFF!]" if force else ""))

    stopped = False
    max_err = 0.0
    last_steer = 0.0
    t0 = time.time()
    last_head = t0
    try:
        px.forward(speed)
        while (time.time() - t0) < total_s:
            time.sleep(POLL_S)
            if not force:
                d = sonar_cm(px)
                if 0 < d < SONAR_STOP_CM:
                    stopped = True
                    print(f"  ! SONAR BRAKE: obstacle at {d:.0f} cm, stopped "
                          f"(threshold/door jamb + free opening → --force)")
                    break
            now = time.time()
            if now - last_head >= HEAD_PERIOD:
                last_head = now
                try:
                    h = imu.sensor.euler[0]
                except Exception:
                    h = None
                if h is not None:
                    if first_live:
                        first_live = False
                        d0 = abs(ang_diff(target, h))
                        if (frozen and d0 > 3.0) or d0 > 15.0:
                            print(f"  (anchor correction: {target:.1f}°→{h:.1f}° — the stationary sample was false)")
                            target = h
                        frozen = False
                    err = ang_diff(target, h)  # + = CW needed = steer right
                    max_err = max(max_err, abs(err))
                    steer = max(-MAX_STEER, min(MAX_STEER, KP * err))
                    # QUANTISATION into >=8° steps. Continuous tiny (KP*err) angle writes hit a
                    # known problem (tanulsagok/vezetes_alapok.md): angles written in small
                    # increments do NOT get through the linkage friction, the servo physically
                    # lags — the controller saw a 20° error but the steering did not follow.
                    # Decisive jumps + write only on change (so the servo really turns).
                    if abs(steer) < 4.0:
                        steer_q = 0.0
                    elif abs(steer) < 10.0:
                        steer_q = 8.0 if steer > 0 else -8.0
                    else:
                        steer_q = MAX_STEER if steer > 0 else -MAX_STEER
                    if steer_q != last_steer:
                        px.set_dir_servo_angle(steer_q)
                        last_steer = steer_q
    finally:
        px.forward(0)
        imu.ndof_vissza()  # after an IMUPLUS fallback, put the shared chip back into NDOF
        for a in (12, -12, 0):
            px.set_dir_servo_angle(a); time.sleep(0.15)

    el = time.time() - t0
    est = min(el, total_s) * v
    final, _ = heading_med(n=3)
    drift = f"{ang_diff(target, final):+.1f}°" if final is not None else "?"
    print(f"DONE: estimated distance {est:.0f} cm ({'shorter because of the brake' if stopped else 'full'}), "
          f"final heading deviation {drift}, max error while driving {max_err:.1f}°; "
          f"sonar now: {sonar_cm(px):.0f} cm")
    print("done")


if __name__ == "__main__":
    main()
