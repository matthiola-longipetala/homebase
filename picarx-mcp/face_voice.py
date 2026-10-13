#!/usr/bin/env python3
"""face_voice.py — the robot turns towards the VOICE based on the ReSpeaker DOA direction.

Closed loop: while STANDING it measures the robot-relative bearing of the voice (doa.py +
voice_config.json calibration: 0=front, +=right), then K-turns towards the voice and
re-measures. The measurement is only valid while someone is speaking -> the speaker must talk
CONTINUOUSLY. We do NOT measure while the motor runs (noise).

args: face_voice.py [tol_deg] [max_cycles]   (default: tol=20, max=10)
"""
import os
import sys, time, math, json, collections
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.motion import forward_guarded
from picarx import Picarx
import doa as DOA

TOL = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
MAX_CYC = int(sys.argv[2]) if len(sys.argv) > 2 else 10
STEER = 25
FWD_DUR = 0.40
REV_DUR = 0.40

dev = DOA.get_dev()
cfg = DOA.load_doa_cfg()
px = Picarx()
for a in (15, -15, 0): px.set_dir_servo_angle(a); time.sleep(0.15)
px.set_cam_pan_angle(0); px.set_cam_tilt_angle(0)


def measure_bearing(secs=3.0):
    """Voice bearing measured while STANDING, mode + circular refinement, or None."""
    buckets = collections.Counter()
    samples = []
    t0 = time.time()
    while time.time() - t0 < secs:
        az, sp = DOA.read_doa(dev)
        if sp:
            br = DOA.doa_to_robot(az, cfg)
            buckets[round(br / 10) * 10] += 1
            samples.append(br)
        time.sleep(0.1)
    if not samples:
        return None, 0
    win = buckets.most_common(1)[0][0]
    near = [b for b in samples if abs(((b - win + 180) % 360) - 180) <= 25]
    sx = sum(math.cos(math.radians(b)) for b in near)
    sy = sum(math.sin(math.radians(b)) for b in near)
    mean = math.degrees(math.atan2(sy, sx))
    mean = (mean + 180) % 360 - 180
    return mean, len(samples)


def kturn(cw):
    """one K-turn cycle (cw=True -> right/CW)."""
    fwd_steer = STEER if cw else -STEER
    px.set_dir_servo_angle(fwd_steer); time.sleep(0.12)
    stopped, dd = forward_guarded(px, 30, FWD_DUR)
    if stopped: print(f"    forward blocked ({dd:.0f}cm) - backward arc only", flush=True)
    rev_steer = -STEER if cw else STEER
    px.set_dir_servo_angle(rev_steer); time.sleep(0.12)
    px.forward(-30); time.sleep(REV_DUR); px.forward(0)
    px.set_dir_servo_angle(0); time.sleep(0.3)


try:
    for cyc in range(MAX_CYC):
        br, n = measure_bearing(3.0)
        if br is None:
            print(f"  [{cyc}] I hear no speech -> keep talking! (STOP)", flush=True)
            break
        print(f"  [{cyc}] voice bearing={br:+.0f} deg  (samples={n})", flush=True)
        if abs(br) <= TOL:
            print(f"  >>> FACING the voice! (|{br:+.0f}| <= {TOL:.0f})", flush=True)
            break
        kturn(br > 0)
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done", flush=True)
