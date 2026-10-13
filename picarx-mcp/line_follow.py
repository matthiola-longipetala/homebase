"""line_follow.py — following a black tape (line) with the ground sensor.

The grayscale module's 3 values [left, centre, right]: light floor ~2500-3600,
black tape <900. Proportional steering from the weighted centre of 'darkness'.

args:  [run_s]  [speed]   (default: 20 s, speed 18)
option: the threshold can be set with the THRESH environment variable (default 1800)
"""
import os, sys, time
from picarx import Picarx

RUN_S = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
SPEED = int(sys.argv[2]) if len(sys.argv) > 2 else 18
THRESH = float(os.environ.get("THRESH", 1800))
K = 32.0            # steering gain
LOST_STOP = 6       # stops after this many consecutive 'line lost' readings

px = Picarx()
px.set_dir_servo_angle(0)
time.sleep(0.3)

STALE = [2571, 3085, 3599]          # false default value after init

def warmup():
    """Waits for LIVE sensor data (after init it returns a stale value for ~0.75 s)."""
    prev = None
    for _ in range(40):             # max ~4s
        g = px.get_grayscale_data()
        if g != STALE and prev is not None and g != prev:
            return g
        prev = g
        time.sleep(0.1)
    return px.get_grayscale_data()

def darkness(v):
    return max(0.0, THRESH - v)     # light->0, dark->large

print(f"LINE FOLLOWING: {RUN_S:.0f} s, speed {SPEED}, threshold {THRESH:.0f}")
g0 = warmup()
print(f"  live sensor (warmup): {g0}")
lost = 0
near = 0            # counter of consecutive close sonar readings (noise filtering)
t0 = time.time()
try:
    while time.time() - t0 < RUN_S:
        g = px.get_grayscale_data()          # [L, M, R]
        L, M, R = darkness(g[0]), darkness(g[1]), darkness(g[2])
        tot = L + M + R
        # sonar brake
        try: d = px.ultrasonic.read()
        except Exception: d = -1
        near = near + 1 if 0 < d < 18 else 0
        if near >= 3:                         # 3 consecutive close readings = real
            print(f"  obstacle {d:.0f} cm (persistent) -> STOP"); break
        if tot < 60:                          # no line seen anywhere
            lost += 1
            px.forward(0)
            if lost >= LOST_STOP:
                print(f"  line lost ({g}) -> STOP"); break
            time.sleep(0.05); continue
        lost = 0
        pos = (R - L) / tot                   # -1 (line to the left) .. +1 (to the right)
        steer = max(-28, min(28, K * pos))
        px.set_dir_servo_angle(steer)
        px.forward(SPEED)
        time.sleep(0.03)
finally:
    px.forward(0); px.set_dir_servo_angle(0)
print("done")
