"""pilot.py — first-person visual driving: one small step + a fresh image.
The agent looks at the image, decides, steps. Sonar brake forward. args: steer speed dur [camtilt]
  steer: -25..25 (- left, + right),  speed: + forward / - backward / 0 image only,  dur: s
"""
import os
import sys, time, subprocess
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.capture import capture
from core.motion import forward_guarded, sonar_cm, SONAR_STOP_CM
from picarx import Picarx

steer = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
spd   = int(sys.argv[2]) if len(sys.argv) > 2 else 0
dur   = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
camtilt = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0   # + = looks up

px = Picarx()
# Rear motor speed balancing (the right motor is slower — core/motor_balance.py;
# measured with motor_kalib.py). No file / 0 value -> no-op.
try:
    from core.motor_balance import apply as _mb_apply
    _mb_apply(px)
except Exception as _e:
    print(f"  (motor balancing skipped: {_e})")
for a in [10, -10, 0]:                       # steering wiggle (against sticking)
    px.set_dir_servo_angle(a); time.sleep(0.2)
px.set_cam_pan_angle(0); px.set_cam_tilt_angle(camtilt); time.sleep(0.3)

def sonar():
    return sonar_cm(px)

moved = False
d = sonar()
if spd > 0 and 0 < d < SONAR_STOP_CM:
    print(f"OBSTACLE {d:.0f}cm ahead -> NOT driving forward")
elif spd > 0 and dur > 0:
    # FORWARD: the shared sonar brake also stops at an obstacle while driving
    px.set_dir_servo_angle(steer); time.sleep(0.15)
    stopped, dd = forward_guarded(px, spd, dur)
    if stopped:
        print(f"SONAR STOP {dd:.0f}cm")
    px.set_dir_servo_angle(0); moved = True
elif spd < 0 and dur > 0:
    px.set_dir_servo_angle(steer); time.sleep(0.15)
    px.forward(spd); time.sleep(dur); px.forward(0)
    px.set_dir_servo_angle(0); moved = True
time.sleep(0.35)
# TWO images: straight (camtilt) + slightly raised (+25) -> better distance estimation
px.set_cam_tilt_angle(camtilt); time.sleep(0.3)
capture("/tmp/view.jpg", w=640, h=480)             # 640x480 = the perception model's training resolution
px.set_cam_tilt_angle(camtilt + 25); time.sleep(0.3)
capture("/tmp/view_up.jpg", w=640, h=480)
px.set_cam_tilt_angle(0)
print(f"moved={moved} steer={steer} spd={spd} dur={dur} sonar_before={d:.0f} sonar_after={sonar():.0f}cm")
