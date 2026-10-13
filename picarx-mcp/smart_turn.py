#!/usr/bin/env python3
"""smart_turn.py — HEADING-based CLOSED-LOOP turning with the BNO055.

On clear ground the blind turn90 turns ~111-120° instead of "90" (measured over
4 controlled runs by a human operator). This tool MEASURES after every cycle:
1 K-cycle -> heading read -> decide. With a large remainder it does a full
K-cycle (~25-30°), with a small one a fine pulse (~5°); tolerance ±6°.

GPIO rule (verified on the robot): the heading reader and the motor controller
run in ONE process (import imu + Picarx() here) — NOT from a subprocess!
Importing imu also restores the calibration offsets (instantiating the BNO055
does a soft reset).

Metal guard: while standing still, a 5-sample median + spread check; if the
heading jumps around (>6° spread) it warns (oven/metal nearby). Near metal the
measured heading can be distorted: then the blind turn90 + camera check may be
more reliable, and the script says so.

Usage:
    python3 smart_turn.py right 90      # 90° right (CW), closed loop
    python3 smart_turn.py left 45       # 45° left (CCW)
    python3 smart_turn.py to 319        # absolute heading target (e.g. the flat's "west")
    python3 smart_turn.py status        # measure only: median heading + calib

WARNING: do not turn NEXT TO an obstacle/wall (asymmetric cycle -> drift); back
away from it first. The forward arc is sonar-braked, the backward arc is blind
(same as turn90).
"""
import sys, os
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))

import imu  # module init: board.I2C() + BNO055 + offset restore — BEFORE Picarx!

TOL_DEG = 6.0        # target tolerance (a fine pulse is ~5°, finer is not realistic)
CYCLE_MIN_DEG = 20.0  # above this remainder a full K-cycle, below it a fine pulse
MAX_STEPS = 16        # runaway guard (90° is typically 3-5 steps)
SPREAD_WARN = 6.0     # stationary heading spread above this = suspected metal/motion

# K-cycle mechanics (calibration from turn90.py)
STEER = 25
SPD = 30
LEG_S = 0.40
# fine-pulse mechanics (calibration from fine_turn.py, ~5°/pulse)
F_STEER = 30
F_SPD = 35
F_LEG_S = 0.13


def ang_diff(target, cur):
    """Shortest signed difference in degrees; positive = need to turn CW (right).
    (The BNO055 heading uses compass convention: it INCREASES when rotating CW.)"""
    return ((target - cur + 540.0) % 360.0) - 180.0


def heading_med(n=5, gap=0.08):
    """Stationary median heading (circular, safe across the 0/360 wrap)
    + sample spread. (None, None) if the IMU returns no data."""
    vals = []
    for _ in range(n):
        try:
            h = imu.sensor.euler[0]
        except Exception:
            h = None
        if h is not None:
            vals.append(h)
        time.sleep(gap)
    if not vals:
        return None, None
    ref = vals[0]
    ds = sorted(((v - ref + 540.0) % 360.0) - 180.0 for v in vals)
    med = ds[len(ds) // 2]
    return (ref + med) % 360.0, ds[-1] - ds[0]


def k_cycle(px, cw, forward_guarded):
    """One full K-cycle (turn90 mechanics): forward arc + counter-steered backward arc."""
    fwd_steer = STEER if cw else -STEER
    px.set_dir_servo_angle(fwd_steer); time.sleep(0.12)
    stopped, dd = forward_guarded(px, SPD, LEG_S)
    if stopped:
        print(f"    (forward arc blocked at {dd:.0f} cm — backward arc only, half cycle)")
    px.set_dir_servo_angle(-fwd_steer); time.sleep(0.12)
    px.forward(-SPD); time.sleep(LEG_S); px.forward(0)
    px.set_dir_servo_angle(0); time.sleep(0.2)


def fine_pulse(px, cw, sonar_cm):
    """One ~5° mini pulse (fine_turn mechanics)."""
    s = F_STEER if cw else -F_STEER
    d = sonar_cm(px)
    px.set_dir_servo_angle(s); time.sleep(0.2)
    if d < 0 or d > 12:
        px.forward(F_SPD); time.sleep(F_LEG_S); px.stop()
    else:
        print(f"    (sonar {d:.0f} cm -> forward leg skipped, backward only)")
    time.sleep(0.15)
    px.set_dir_servo_angle(-s); time.sleep(0.2)
    px.backward(F_SPD); time.sleep(F_LEG_S); px.stop()
    px.set_dir_servo_angle(0); time.sleep(0.15)


def main():
    if len(sys.argv) < 2:
        print(__doc__.split("Usage:")[1]); sys.exit(1)
    mode = sys.argv[1].lower()

    if mode == "status":
        _b, _h, _c = imu.bealt(max_s=4.0)  # wait for the post-reset jump here too
        if not _b:
            print(f"(IMU did not settle within 4 s [{_c}] — the heading below is suspect)")
        h, spread = heading_med()
        d = imu.read_once()
        if h is None:
            print("IMU: no heading data!"); sys.exit(1)
        print(f"heading={h:.1f}° (spread {spread:.1f}°)  roll={d['roll']}°  "
              f"pitch={d['pitch']}°  [{d['calib']}]"
              + ("  ⚠️ jumping — metal nearby/motion?" if spread > SPREAD_WARN else ""))
        return

    # We wait for the ABSOLUTE JUMP after the soft reset (sys==3 + 1 s stable), otherwise
    # it arrives mid-turn and would be counted as rotation (it once reported "86° done"
    # while the camera showed 0°). If it does not settle within 4 s (disturbed spot:
    # junction/metal/charger), an absolute target ("to") means STOP — we do not start
    # from a half-calibrated anchor.
    # left/right = RELATIVE turn -> RAW GYRO INTEGRAL (imu.GyroIntegrator), with no mode
    # switch and no settle wait (the raw gyro agrees with the camera to 0.98, while the
    # fused euler under-reads depending on the manoeuvre). The fused (absolute) heading is
    # needed ONLY for 'to' mode, which keeps the settle wait; if it does not settle -> STOP,
    # the camera decides.
    relativ_mod = (mode != "to")
    gi = None
    if relativ_mod:
        gi = imu.GyroIntegrator(h0=0.0)
        if not gi.start():
            print("GYRO INTEGRAL: the measuring child process did not start — STOP, check manually."); sys.exit(1)
        print(f"GYRO INTEGRAL mode (relative, separate process): resting gyro-z {gi.bias:+.2f}°/s subtracted; "
              f"chip mode unchanged ({imu.sensor.mode}).")
    hmf = (lambda n=5, gap=0.08: (gi.heading(), 0.0)) if relativ_mod else heading_med
    if not relativ_mod:
        beallt, _h, calib = imu.bealt(max_s=4.0)
        if beallt:
            print(f"IMU settled [{calib}]")
        else:
            print(f"IMU did NOT settle within 4 s [{calib}] — absolute target FORBIDDEN; STOP, the camera decides.")
            sys.exit(1)

    # --- preliminary read (ONLY for the direction decision — may still be a "sleeping" value!) ---
    h_pre, spread = hmf()
    if h_pre is None:
        print("IMU gives no heading — fall back to the blind turn90!"); sys.exit(1)
    if spread is not None and spread > SPREAD_WARN:
        print(f"⚠️ heading jumps while stationary ({spread:.1f}° spread) — metal nearby?"
              " The measurement may be distorted; near metal turn90+camera is more reliable.")

    if mode == "to":
        target_abs = float(sys.argv[2]) % 360.0
        first_cw = ang_diff(target_abs, h_pre) > 0
        deg = None
    elif mode in ("left", "right", "balra", "jobbra"):
        cw = mode in ("right", "jobbra")
        deg = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
        first_cw = cw
        # (an earlier x0.86 IMUPLUS correction factor was removed: it was not a scale error
        # but a fusion artefact — the raw gyro integral does not need it)
    else:
        print("usage: smart_turn.py left|right [deg] | to <heading> | status")
        sys.exit(1)

    # --- motor only NOW (same process, but the IMU init has already happened) ---
    from core.motion import forward_guarded, sonar_cm
    from picarx import Picarx
    px = Picarx()
    # Rear motor speed balancing (the right motor is slower — core/motor_balance.py;
    # measured with motor_kalib.py). No file / 0 value -> no-op.
    try:
        from core.motor_balance import apply as _mb_apply
        _mb_apply(px)
    except Exception as _e:
        print(f"  (motor balancing skipped: {_e})")
    for a in (15, -15, 0):  # steering wiggle (against sticking)
        px.set_dir_servo_angle(a); time.sleep(0.2)

    # WAKE-UP PULSE (lesson from the first live test): the fusion of a stationary BNO055
    # can "stick" (frozen 0.0° -> shifted target -> physical over-rotation). A ~5° fine
    # pulse in the requested direction wakes it up, and we anchor ONLY to the LIVE heading
    # measured AFTER it; in relative mode the pulse's nominal 5° is subtracted.
    fine_pulse(px, first_cw, sonar_cm)
    h0, sp0 = hmf()
    if h0 is None:
        px.forward(0)
        print("IMU went silent after the wake-up pulse — stop, check manually!"); sys.exit(1)
    if abs(ang_diff(h0, h_pre)) < 1.0:
        print(f"⚠️ the heading did NOT move on the wake-up pulse ({h_pre:.1f}°→{h0:.1f}°) — "
              "suspected sleeping/frozen compass, verify the result with the camera!")

    if mode == "to":
        target = target_abs
        print(f"SMART_TURN: live anchor {h0:.1f}° → target {target:.1f}° (absolute)")
    else:
        rem0 = max(0.0, deg - 5.0)  # the pulse's nominal ~5° is already done
        target = (h0 + (rem0 if cw else -rem0)) % 360.0
        print(f"SMART_TURN: {deg:.0f}° {'RIGHT/CW' if cw else 'LEFT/CCW'}: "
              f"live anchor {h0:.1f}° (pulse: {h_pre:.1f}°→{h0:.1f}°) → target {target:.1f}°")

    cur = h0
    steps = 0
    alig = 0  # "barely turned" anomaly counter
    try:
        while steps < MAX_STEPS:
            rem = ang_diff(target, cur)
            if abs(rem) <= TOL_DEG:
                break
            # near 180° the sign is uncertain -> the first step goes in the REQUESTED direction
            cw_now = first_cw if (steps == 0 and abs(rem) > 165.0) else (rem > 0)
            full = abs(rem) >= CYCLE_MIN_DEG
            if full:
                k_cycle(px, cw_now, forward_guarded)
            else:
                fine_pulse(px, cw_now, sonar_cm)
            steps += 1
            new, sp = hmf(n=3, gap=0.06)
            if new is None:
                print("  ! IMU went silent while moving — stop, manual check needed")
                break
            moved = ang_diff(new, cur)  # positive = we turned CW
            print(f"  [{steps}] {'K-cycle' if full else 'fine'} "
                  f"{'CW' if cw_now else 'CCW'}: {cur:.1f}°→{new:.1f}° "
                  f"(Δ{moved:+.1f}°, remaining {ang_diff(target, new):+.1f}°)")
            # Jump guard: a K-cycle is physically ~25-45°; above that the sensor jumped
            # (post-reset settling / metal), not the body -> STOP, the camera decides.
            if abs(moved) > 45.0:
                print(f"  ! MEASUREMENT JUMP ({moved:+.1f}° in one step) — not a turn but a "
                      "compass jump. STOPPING; camera/whereami decides, do not act on it!")
                break
            dirsign = 1.0 if cw_now else -1.0
            if full and (abs(moved) < 4.0 or moved * dirsign < -10.0):
                alig += 1
                if moved * dirsign < -10.0:
                    print("    ⚠️ the measured change is OPPOSITE to the command — compass jump (metal/wake-up)?")
                else:
                    print("    ⚠️ barely turned — stuck/obstacle/rug?")
                if alig >= 2:
                    print("  ! two suspicious readings in a row — STOPPING, check on me (image/manual)")
                    break
            else:
                alig = 0
            cur = new
    finally:
        px.forward(0)
        if gi is not None:
            gi.stop()
            if gi.hiba: print(f"  (gyro read errors in the thread: {gi.hiba}, samples: {gi.n})")
        imu.ndof_vissza()  # in case something left the shared chip in IMUPLUS (no longer us)
        # zeroing with a wiggle, ending FROM THE LEFT (zero_steer policy)
        for a in (12, -12, 0):
            px.set_dir_servo_angle(a); time.sleep(0.15)

    final, _ = hmf()
    if final is not None:
        err = ang_diff(target, final)
        # "Done too early" flag — a K-cycle is <=~35°, so the requested angle cannot be
        # reached in fewer steps; if it still says "done", the sensor jumped -> verify by camera.
        min_steps = max(1, int(round((deg or 0.0) / 35.0))) if deg else 0
        koran = " — ⚠️ DONE TOO EARLY (fewer steps than the angle allows) → verify with the camera" \
            if (deg and steps < min_steps and abs(err) <= TOL_DEG) else ""
        print(f"DONE: {h0:.1f}° → {final:.1f}° (target {target:.1f}°), "
              f"error {err:+.1f}°, {steps} steps"
              + ("" if abs(err) <= TOL_DEG else " — out of tolerance, check (camera/whereami)")
              + koran + (" [GYRO INTEGRAL, relative]" if relativ_mod else " [NDOF, absolute]"))
    else:
        print(f"DONE (but the final IMU read failed), {steps} steps")
    print("done")


if __name__ == "__main__":
    main()
