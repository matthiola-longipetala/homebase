"""turn_scout.py — BLIND turning in place + looking around until a known tag is seen.

Lessons: (1) turning in place needs very little room -> either direction is safe if there
is room; do not agonise, turn. (2) If you see no known tag, you can also orient yourself
from the CAMERA IMAGE -> take pictures while sweeping and identify from the view where you are.

This script: does open-loop K-turns in one direction (default: CW/right), and after EVERY
cycle pans around looking for a known tag. As soon as it finds one (margin>=MIN_M), it STOPS
and prints which tag, in which direction (bearing). Meanwhile it saves a forward image every
cycle (/tmp/scout_<cyc>.jpg) for visual tracking. Afterwards run whereami.py for the fix.

args: turn_scout.py [cw|ccw] [target_tag_id ...]
   e.g. turn_scout.py cw 0 1 3 6   -> turns right until it sees one of the hallway tags
   of the invented example flat (ignoring the others). Without a target list: any known tag.
"""
import os
import json, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.capture import capture
from core.tag_range import tag_bearing_deg
from core.localization import load_tag_map
from core.motion import forward_guarded
from core.sweep import sweep_pans
from picarx import Picarx

TILT0 = 5.0
if "--tilt" in sys.argv:
    i = sys.argv.index("--tilt"); TILT0 = float(sys.argv[i+1]); del sys.argv[i:i+2]
CCW = (len(sys.argv) > 1 and sys.argv[1].lower() == "ccw")
TARGETS = set(int(a) for a in sys.argv[2:]) if len(sys.argv) > 2 else None  # None=any known tag
calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, cx = calib["fx"], calib["cx"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
for a in (15, -15, 0): px.set_dir_servo_angle(a); time.sleep(0.2)
px.set_cam_tilt_angle(TILT0); px.set_cam_pan_angle(0); time.sleep(0.3)

_SHUT = [None]
STEER = 25; FWD_DUR = 0.40; REV_DUR = 0.40
MAX_CYC = 9; MIN_M = 15

def sweep_for_known(cyc):
    """pans around; returns (id,bearing,margin) of all known tags; saves 1 forward image."""
    _SHUT[0] = None
    seen = {}
    for pan in sweep_pans(-70, 70, step=45, current=0):
        px.set_cam_pan_angle(pan); time.sleep(0.4)
        _SHUT[0] = capture("/tmp/scout.jpg", w=1024, h=576, shutter_us=_SHUT[0])
        if pan == 0:
            try: Image.open("/tmp/scout.jpg").save(f"/tmp/scout_{cyc}.jpg")
            except Exception: pass
        img = cv2.undistort(np.array(Image.open("/tmp/scout.jpg").convert("L")), K, D)
        for r in det.detect(img, estimate_tag_pose=False):
            tid = int(r.tag_id)
            if tid not in tag_map: continue
            b = tag_bearing_deg(float(r.center[0]), pan, fx, cx)
            m = float(r.decision_margin)
            if tid not in seen or m > seen[tid][1]:
                seen[tid] = (b, m)
    px.set_cam_pan_angle(0)
    return seen

def kturn_cycle(cw):
    fwd_steer = STEER if cw else -STEER
    px.set_dir_servo_angle(fwd_steer); time.sleep(0.12)
    stopped, dd = forward_guarded(px, 30, FWD_DUR)
    if stopped: print(f"    forward blocked ({dd:.0f}cm) - backward arc only")
    rev_steer = -STEER if cw else STEER
    px.set_dir_servo_angle(rev_steer); time.sleep(0.12)
    px.forward(-30); time.sleep(REV_DUR); px.forward(0)
    px.set_dir_servo_angle(0); time.sleep(0.25)

cw = not CCW
print(f"TURNING: {'CW/right' if cw else 'CCW/left'}")
try:
    for cyc in range(MAX_CYC):
        seen = sweep_for_known(cyc)
        if seen:
            items = ", ".join(f"tag{t}(b={seen[t][0]:+.0f},m={seen[t][1]:.0f})" for t in sorted(seen))
            strong = [t for t in seen if seen[t][1] >= MIN_M and (TARGETS is None or t in TARGETS)]
            print(f"  [{cyc}] I SEE: {items}")
            if strong:
                print(f"  >>> target tag(s) strong enough: {sorted(strong)} -> STOPPING, ready for the fix")
                break
        else:
            print(f"  [{cyc}] no known tag -> turning on")
        kturn_cycle(cw)
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done")
