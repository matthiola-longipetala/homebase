"""face_tag.py — TURNING TO FACE a tag based on its BEARING (apparent direction), with K-turns.

turn_to relies on the pose heading from localize(), which FLIPS with a single oblique tag
-> wrong heading -> the robot believes it is facing the tag when it is not. That happened
with a single tag seen obliquely (flipped).

Solution: the tag's BEARING (pan + image-x offset) is FLIP-INDEPENDENT. Panning around we find
the target tag, compute its bearing (direction relative to the body), and K-turn towards
centring it until |bearing| <= TOL. +bearing=right -> we turn right (CW); -bearing=left ->
left (CCW). The bearing is re-measured after EVERY cycle (closed loop), so the flip does
not matter.

args: face_tag.py <tag_id>   (e.g. face_tag.py 9  -> turns to face tag9, the living-room
      exit tag of the invented example flat)

NOTE: the result lines "nem latom a tag<N>-t a panban -> STOP" (= I do not see tag N in the
pan) and ">>> SZEMBE a tag<N>-tel (nyugat)!" (= FACING tag N; the "(nyugat)" = "west"
suffix is a fixed literal, not the real direction) and "vege" are parsed by
utvonalak/route_runner.py — keep them unchanged. face_tag exits with 0 even on STOP.
"""
import os
import json, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.capture import capture
from core.tag_range import tag_bearing_deg
from core.motion import forward_guarded
from core.sweep import sweep_pans
from picarx import Picarx

TILT0 = 0.0
if "--tilt" in sys.argv:
    i = sys.argv.index("--tilt"); TILT0 = float(sys.argv[i+1]); del sys.argv[i:i+2]
TID = int(sys.argv[1])
calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, cx = calib["fx"], calib["cx"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
for a in (15, -15, 0): px.set_dir_servo_angle(a); time.sleep(0.2)
px.set_cam_tilt_angle(TILT0); px.set_cam_pan_angle(0); time.sleep(0.3)

_SHUT = [None]
TOL = 12.0
STEER = 25
FWD_DUR = 0.40
REV_DUR = 0.40
MAX_CYC = 8

def find_bearing():
    """pans around, finds the target tag, returns its bearing (and margin)."""
    _SHUT[0] = None
    best = None
    for pan in sweep_pans(-70, 70, step=40, current=0):
        px.set_cam_pan_angle(pan); time.sleep(0.4)
        _SHUT[0] = capture("/tmp/ft.jpg", w=1024, h=576, shutter_us=_SHUT[0])
        img = cv2.undistort(np.array(Image.open("/tmp/ft.jpg").convert("L")), K, D)
        for r in det.detect(img, estimate_tag_pose=False):
            if int(r.tag_id) != TID: continue
            b = tag_bearing_deg(float(r.center[0]), pan, fx, cx)
            m = float(r.decision_margin)
            if best is None or m > best[1]:
                best = (b, m)
    px.set_cam_pan_angle(0)
    return best

try:
    for cyc in range(MAX_CYC):
        bm = find_bearing()
        if bm is None:
            print(f"  [{cyc}] nem latom a tag{TID}-t a panban -> STOP"); break
        b, m = bm
        print(f"  [{cyc}] tag{TID} bearing={b:+.1f} margin={m:.0f}")
        if abs(b) <= TOL:
            print(f"  >>> SZEMBE a tag{TID}-tel (nyugat)!"); break
        cw = b > 0                       # tag on the right -> we turn right (CW)
        fwd_steer = STEER if cw else -STEER
        px.set_dir_servo_angle(fwd_steer); time.sleep(0.12)
        stopped, dd = forward_guarded(px, 30, FWD_DUR)
        if stopped: print(f"    forward blocked ({dd:.0f}cm) - backward arc only")
        rev_steer = -STEER if cw else STEER
        px.set_dir_servo_angle(rev_steer); time.sleep(0.12)
        px.forward(-30); time.sleep(REV_DUR); px.forward(0)
        px.set_dir_servo_angle(0)
        time.sleep(0.25)
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("vege")
