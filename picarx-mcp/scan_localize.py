"""scan_localize.py — search sweep: the camera finds the tag, turns it to the CENTRE
(image-based closed loop), and takes a CLEAN fix there.

The pan model is accurate near the image centre (see pan_sweep), so the fix is always taken
with the tag in the centre of the image -> the narrow field-of-view limit is bypassed.
"""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, load_tag_sizes, camera_extrinsic
from core.capture import capture
from core.sweep import sweep_pans
from picarx import Picarx

_SHUT = [None]   # exposure is measured once per scan and reused within the sweep

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.5)
    p = "/tmp/scan.jpg"
    _SHUT[0] = capture(p, w=1024, h=576, shutter_us=_SHUT[0])
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    res = det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=1.0)
    return res

def scan_and_fix():
    _SHUT[0] = None              # new fix -> re-measure exposure for the current lighting
    # 1) coarse sweep: look for any known tag
    found_pan = None
    for pan in sweep_pans(-60, 60, step=40, current=0):
        res = capture_detect(pan)
        known = [r for r in res if int(r.tag_id) in tag_map]
        if known:
            found_pan = pan
            print(f"  tag(s) {[int(r.tag_id) for r in known]} found at pan={pan}")
            break
    if found_pan is None:
        print("  found NO known tag at all in the sweep"); return None
    # 2) fine centring on the strongest tag (image-based closed loop)
    pan = found_pan
    for it in range(4):
        res = capture_detect(pan)
        known = [r for r in res if int(r.tag_id) in tag_map]
        if not known:
            print(f"  lost at pan={pan} -> stepping back"); pan -= math.copysign(8, pan); continue
        r = max(known, key=lambda r: r.decision_margin)
        u = float(r.center[0])
        off_px = u - cx
        off_deg = math.degrees(math.atan2(off_px, fx))
        print(f"  [it{it}] pan={pan:.1f} tag{int(r.tag_id)} u={u:.0f} (cx={cx:.0f}) "
              f"offset={off_px:+.0f}px = {off_deg:+.1f}deg margin={r.decision_margin:.0f}")
        if abs(off_px) < 25:        # ~1.2 degrees -> centred enough
            break
        pan += off_deg               # +pan=right; u>cx (to the right) -> pan increases
        pan = max(-70, min(70, pan))
    # 3) clean fix with the centred pan
    res = capture_detect(pan)
    dets = [{"tag_id":int(r.tag_id),"pose_R":np.asarray(r.pose_R),
             "pose_t":np.asarray(r.pose_t).ravel()*SIZES.get(int(r.tag_id),6.0),
             "decision_margin":float(r.decision_margin)}
            for r in res if int(r.tag_id) in tag_map]
    if not dets:
        print("  the tag was lost at the final fix"); return None
    ext = camera_extrinsic(pan_deg=pan, tilt_deg=TILT)
    pose = localize(dets, tag_map, ext)
    try:
        from core.obsdb import record
        if pose is not None: record(pose, dets, ext, source="scan_localize")
    except Exception: pass
    print(f"  >>> FIX at pan={pan:.1f}: x={pose.x_cm:.1f} y={pose.y_cm:.1f} hdg={pose.heading_deg:.1f} "
          f"(tag {[d['tag_id'] for d in dets]})")
    return pose, pan

try:
    out = scan_and_fix()
finally:
    px.set_cam_pan_angle(0); time.sleep(0.3)
print("done")
