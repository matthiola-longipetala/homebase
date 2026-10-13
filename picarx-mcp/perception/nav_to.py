"""nav_to.py — AUTONOMOUS visual homing toward a target class (venv + system-site-packages).

The robot sees by ITSELF (lakas_v3) and steers toward the target — Claude does NOT analyze each frame.
Goal set by the owner: 'drive to the sink' autonomously. sink/countertop = kitchen_cabinet
(the model's "kitchen cabinet" class).

Loop (max N steps):
  1. SEARCH: camera sweep [0,-30,30,-60,60] for the target. If it is nowhere → BODY TURN
     (~50° with a K-turn) and search again (max MAX_TURNS body turns), because the camera FOV
     is only ~±70° and the target may be behind.
  2. If found: bearing → steer toward it → short FORWARD step (with SONAR BRAKE).
  3. Stops: sonar < ARRIVE_CM (arrived) OR the target is large in the frame OR step limit.
  Only drives FORWARD + steers; the reverse arc of the K-turn is short and blind (for open space).

Usage: .venv-perception/bin/python nav_to.py [target_class] [max_steps]
"""
import os
import sys, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from ultralytics import YOLO
from picarx import Picarx
from core.motion import forward_guarded, sonar_cm
from core.capture import capture
from core.perception import bearing_from_px

TARGET = sys.argv[1] if len(sys.argv) > 1 else "kitchen_cabinet"
MAX_STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 10
MODEL = os.path.expanduser("~/picarx-mcp/runs_otthon/lakas_v3/weights/best.pt")
IMG = "/tmp/nav.jpg"
ARRIVE_CM = 26.0
ARRIVE_AREA = 0.50
TILT = 12
MAX_TURNS = 5      # max number of ~50° body turns while searching (~250° total)

m = YOLO(MODEL)
px = Picarx()
for a in (10, -10, 0):
    px.set_dir_servo_angle(a); time.sleep(0.15)
px.set_cam_tilt_angle(TILT); px.set_cam_pan_angle(0); time.sleep(0.3)

def detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.25)
    capture(IMG, w=640, h=480)
    r = m(IMG, imgsz=512, conf=0.30, verbose=False)[0]
    H, W = r.orig_shape
    best = None
    for (x1, y1, x2, y2), c, cf in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
        if m.names[int(c)] != TARGET:
            continue
        area = (x2 - x1) * (y2 - y1) / (W * H)
        bg = bearing_from_px((x1 + x2) / 2, W)
        if best is None or area > best[0]:
            best = (area, float(cf), bg)
    return best

def search():
    """Camera sweep for the target. Returns (area,conf,bearing_TOTAL) or None."""
    for pan in (0, -30, 30, -60, 60):
        t = detect(pan)
        if t is not None:
            px.set_cam_pan_angle(0); time.sleep(0.15)
            return (t[0], t[1], pan + t[2])
    px.set_cam_pan_angle(0); time.sleep(0.15)
    return None

def kturn(deg, direction=1):
    """Turn in place with a K-turn. direction +1=right. ~18°/cycle (per the manual)."""
    cycles = max(1, round(abs(deg) / 18.0))
    s = 20 * direction
    for _ in range(cycles):
        px.set_dir_servo_angle(s); time.sleep(0.1)
        forward_guarded(px, 30, 0.40)
        px.set_dir_servo_angle(-s); time.sleep(0.1)
        px.forward(-30); time.sleep(0.40); px.forward(0)
        px.set_dir_servo_angle(0); time.sleep(0.1)

print(f"TARGET: {TARGET} (max {MAX_STEPS} steps, max {MAX_TURNS} body turns)")
arrived = False
turns = 0
step = 0
while step < MAX_STEPS:
    tgt = search()
    if tgt is None:
        if turns >= MAX_TURNS:
            print(f"{TARGET} not found even after {turns} turns -> giving up"); break
        turns += 1
        print(f"[search] {TARGET} not in view -> turning ~50° right ({turns}/{MAX_TURNS})")
        kturn(50, +1)
        continue
    step += 1
    area, cf, bearing = tgt
    d = sonar_cm(px)
    print(f"[{step}] {TARGET} conf={cf:.2f} area={area:.2f} bearing={bearing:+.0f} sonar={d:.0f}cm")
    if (0 < d < ARRIVE_CM) or area >= ARRIVE_AREA:
        print(f"[{step}] >>> ARRIVED at {TARGET} (sonar={d:.0f}, area={area:.2f})")
        arrived = True; break
    steer = max(-20.0, min(20.0, bearing * 0.6))
    px.set_dir_servo_angle(steer); time.sleep(0.15)
    stopped, dd = forward_guarded(px, 30, 0.5)
    px.set_dir_servo_angle(0); time.sleep(0.2)
    if stopped:
        print(f"[{step}] sonar brake {dd:.0f}cm")
        if area >= 0.30:
            print(f"[{step}] >>> in front of the target, ARRIVED"); arrived = True; break
        print(f"[{step}] non-target obstacle -> re-evaluating");

px.forward(0); px.set_cam_pan_angle(0)
print("ARRIVED" if arrived else "DID NOT REACH TARGET")
