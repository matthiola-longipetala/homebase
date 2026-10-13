"""dr_check.py — measuring the accuracy of dead reckoning.

fix A (scan) -> the robot drives n steps BLIND according to the motion model (no scan,
propagating the estimate meanwhile) -> fix B (scan, TRUTH). We compare: how much the
model estimate drifted relative to the real fix.

args: steer n_steps dur_each   (e.g. straight: 0 3 0.8 ; turning: 15 3 0.8)
"""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, camera_extrinsic
from picarx import Picarx

STEER = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0
NSTEP = int(sys.argv[2]) if len(sys.argv) > 2 else 3
DUR   = float(sys.argv[3]) if len(sys.argv) > 3 else 0.8

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
# IMX708 Wide: resolution + LOCKED focus from camera_config.json.
# Without the AF lock the lens would move and fx (distance/bearing) would drift!
_cc = json.load(open(os.path.expanduser("~/picarx-mcp/camera_config.json")))
CAM_W, CAM_H = str(_cc["capture_width"]), str(_cc["capture_height"])
LENS_POS = str(_cc["lens_position"])
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0); px.set_cam_pan_angle(0)
time.sleep(0.3)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    p = "/tmp/drc.jpg"
    subprocess.run(["rpicam-still","-o",p,"--width",CAM_W,"--height",CAM_H,"-t","420","-n",
                    "--autofocus-mode","manual","--lens-position",LENS_POS],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=6.0)

def scan_fix():
    found = None
    for pan in [0, -20, 20, -40, 40, -60, 60]:
        if any(int(r.tag_id) in tag_map for r in capture_detect(pan)):
            found = pan; break
    if found is None:
        px.set_cam_pan_angle(0); return None
    pan = found
    for _ in range(4):
        known = [r for r in capture_detect(pan) if int(r.tag_id) in tag_map]
        if not known:
            pan -= math.copysign(8, pan or 1); continue
        r = max(known, key=lambda r: r.decision_margin)
        off = float(r.center[0]) - cx
        if abs(off) < 25: break
        pan = max(-70, min(70, pan + math.degrees(math.atan2(off, fx))))
    dets = [{"tag_id":int(r.tag_id),"pose_R":np.asarray(r.pose_R),
             "pose_t":np.asarray(r.pose_t).ravel(),"decision_margin":float(r.decision_margin)}
            for r in capture_detect(pan) if int(r.tag_id) in tag_map]
    px.set_cam_pan_angle(0)
    if not dets: return None
    return localize(dets, tag_map, camera_extrinsic(pan_deg=pan, tilt_deg=TILT))

def propagate(x, y, h, steer, dur):
    dh = (-4.2 - 1.325*steer) * dur
    d  = (27.0 - 0.175*abs(steer)) * dur
    hmid = math.radians(h + dh/2.0)
    return x + d*math.cos(hmid), y + d*math.sin(hmid), h + dh

print(f"DR-CHECK: steer={STEER} n={NSTEP} dur={DUR}s  (blind driving, then comparison)")
A = scan_fix()
if A is None:
    print("  fix A: no tag -> place me so that I can see one"); sys.exit(1)
ex, ey, eh = A.x_cm, A.y_cm, A.heading_deg
print(f"  fix A:        x={ex:.1f} y={ey:.1f} hdg={eh:.1f}")
try:
    for k in range(NSTEP):
        px.set_dir_servo_angle(STEER); time.sleep(0.15)
        px.forward(30); time.sleep(DUR); px.forward(0)
        px.set_dir_servo_angle(0)
        ex, ey, eh = propagate(ex, ey, eh, STEER, DUR)
        time.sleep(0.25)
    print(f"  DR estimate:  x={ex:.1f} y={ey:.1f} hdg={eh:.1f}  (after {NSTEP} blind steps)")
    B = scan_fix()
    if B is None:
        print("  fix B: the tag was lost after blind driving (went too far?)"); sys.exit(1)
    print(f"  fix B (true): x={B.x_cm:.1f} y={B.y_cm:.1f} hdg={B.heading_deg:.1f}")
    perr = math.hypot(ex - B.x_cm, ey - B.y_cm)
    herr = eh - B.heading_deg
    while herr > 180: herr -= 360
    while herr < -180: herr += 360
    print(f"  >>> DR ERROR: position {perr:.1f} cm,  heading {herr:.1f} deg  ({NSTEP} steps / about {(27-0.175*abs(STEER))*DUR*NSTEP:.0f} cm)")
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done")
