"""go_dist.py — drive a GIVEN DISTANCE straight, in ONE command.

Problem: goto-style navigation stops every 20-30 cm, looks around, thinks -> a flood of
data in which the cloud brain sometimes gets lost ("after 40 cm I think I am at the
target"). On a clearly visible, unambiguous leg step-by-step localisation is pointless.

This tool computes the time from the CALIBRATED motion model (speed 30: ~27 cm/s) and
drives with the sonar brake running THE WHOLE TIME — but it does NOT stop to think.
At the end it prints the estimated distance travelled so dead reckoning can be updated.

Usage:
    python3 go_dist.py 100          # 100 cm forward (speed 30, with sonar brake)
    python3 go_dist.py 100 55       # 100 cm forward at speed 55 (e.g. momentum)
    python3 go_dist.py -- -40       # 40 cm BACKWARD, with the REAR sonar brake
    python3 go_dist.py -- -40 --force # 40 cm BACKWARD blind (rear brake OFF)
    python3 go_dist.py 60 55 --force  # THRESHOLD MODE: sonar brake OFF, across with momentum

THRESHOLD MODE (--force / --no-guard / --kuszob / --threshold): the sonar brake (22 cm)
sees a threshold/door jamb at the edge of its cone as an "obstacle" and stops, although
the robot would fit through. PROTOCOL: if the brake stops at a door -> TAKE A PICTURE
(tag-based position relative to the gap), and if the OPENING IS PROVEN FREE, then
go_dist <cm> <speed~55> --force -> the brake is off and it crosses with momentum.
Use ONLY at a visually confirmed free opening!

Model: at speed 30 v = 27.0 cm/s (cal_motion). For other speeds we scale
(v ~ 0.9*speed) -- to be refined by a stopwatch measurement.

NOTE: the "CEL: ..." and "KESZ: becsult megtett tav ..." output lines are parsed by
utvonalak/route_runner.py (and mirrored by its simulator) — keep their wording unchanged.
"""
import os
import sys, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.motion import forward_guarded, sonar_cm, back_guarded, rear_cm
from picarx import Picarx

#   speed 30 -> 31.4 cm/s (116cm/3.7s);  speed 55 -> 37.4 cm/s (65cm/1.74s);
#   speed 80 -> 43.0 cm/s (115cm/2.67s)
# Measured speed points. speed->velocity is NOT proportional -- above 30 the increment is
# ~+0.23 cm/s per speed unit. In between: interpolation; above the highest measured point
# we treat it as saturated.
V_PONTOK = [(30, 28.6), (55, 30.8), (80, 34.9)]
def v_of(speed):
    pts = sorted(V_PONTOK)
    if speed <= pts[0][0]:
        return pts[0][1] * (speed / pts[0][0])      # scale down proportionally (0 at 0)
    for (s1, v1), (s2, v2) in zip(pts, pts[1:]):
        if speed <= s2:
            return v1 + (v2 - v1) * (speed - s1) / (s2 - s1)
    return pts[-1][1]                                # above the measured range: saturated
CHUNK_S = 1.5        # max leg length in seconds (the sonar brake still runs every 50 ms)
REV_CHUNK_S = 1.0    # reverse leg (blind -> shorter pieces)

FORCE_FLAGS = ("--force", "--no-guard", "--kuszob", "--threshold")
raw = sys.argv[1:]
FORCE = any(a in FORCE_FLAGS for a in raw)
args = [a for a in raw if a != "--" and a not in FORCE_FLAGS]
if not args:
    print("usage: go_dist.py <cm> [speed] [--force]   (negative cm = backward; --force = sonar brake OFF)"); sys.exit(1)
dist_cm = float(args[0])
speed = int(args[1]) if len(args) > 1 else 30

v = v_of(speed)   # velocity interpolated from the measured points
total_s = abs(dist_cm) / v

px = Picarx()
# Rear motor speed balancing (the right motor is slower — core/motor_balance.py;
# measured with motor_kalib.py). No file / 0 value -> no-op.
try:
    from core.motor_balance import apply as _mb_apply
    _mb_apply(px)
except Exception as _e:
    print(f"  (motor balancing skipped: {_e})")
for a in (15, -15, 0): px.set_dir_servo_angle(a); time.sleep(0.2)  # steering wiggle
px.set_dir_servo_angle(0); time.sleep(0.15)

print(f"CEL: {dist_cm:+.0f} cm @speed {speed}  (~{total_s:.1f} mp, modell v={v:.1f} cm/s)")
done_s = 0.0
stopped = False
try:
    if dist_cm >= 0 and FORCE:
        # THRESHOLD MODE: sonar brake OFF -> across the threshold/door with momentum.
        print("  THRESHOLD MODE: sonar brake OFF (a visually confirmed free opening is required!)")
        while done_s < total_s:
            seg = min(CHUNK_S, total_s - done_s)
            px.forward(speed); time.sleep(seg); px.forward(0)
            done_s += seg
    elif dist_cm >= 0:
        while done_s < total_s and not stopped:
            seg = min(CHUNK_S, total_s - done_s)
            stopped, last = forward_guarded(px, speed, seg)
            done_s += seg if not stopped else seg * 0.5   # when braked, estimate ~half the leg
            if stopped:
                print(f"  ! SONAR BRAKE: obstacle at {last:.0f} cm, stopped (if it is only a threshold/door jamb and the opening is free -> --force)")
    elif dist_cm < 0 and FORCE:
        # BACKWARD, --force: rear sonar brake OFF (blind reversing, the old behaviour)
        print("  (BACKWARD, --force: rear sonar brake OFF, blind, in short pieces)")
        while done_s < total_s:
            seg = min(REV_CHUNK_S, total_s - done_s)
            px.forward(-speed); time.sleep(seg); px.forward(0)
            done_s += seg
            time.sleep(0.1)
    else:
        # BACKWARD: with the REAR sonar brake (rear_sonar, D1/D0)
        print("  (BACKWARD: driving with the rear sonar brake; in short pieces)")
        while done_s < total_s and not stopped:
            seg = min(REV_CHUNK_S, total_s - done_s)
            stopped, last = back_guarded(px, speed, seg)
            done_s += seg if not stopped else seg * 0.5   # when braked, estimate ~half the leg
            if stopped:
                print(f"  ! REAR SONAR BRAKE: obstacle {last:.0f} cm behind me, stopped")
            time.sleep(0.05)
finally:
    px.forward(0); px.set_dir_servo_angle(0)

est = done_s * v * (1 if dist_cm >= 0 else -1)
_szonar = f"hatso-szonar most: {rear_cm():.0f} cm" if dist_cm < 0 else f"elore-szonar most: {sonar_cm(px):.0f} cm"
print(f"KESZ: becsult megtett tav {est:+.0f} cm "
      f"({'fek miatt rovidebb' if stopped else 'teljes szakasz'}); {_szonar}")
print("vege")  # end marker (mirrored by route_runner.py's simulator)
