"""bemer_tag.py — surveying an UNKNOWN tag via co-visibility, with a scan.

From one spot: (1) turning to known tag(s), the robot measures its OWN pose (scan_fix),
(2) turning to the unknown tag and centring it, it records that tag's detection,
(3) tag_world_from_robot -> the unknown tag's WORLD coordinates (can be written to tags.json).

The robot stands still; only the camera sweeps. Both the known and the unknown tag must be
reachable by sweeping within ±70°.

args: unknown_tag_id [repeats] [size_cm] [ref_tag_id]
   ref_tag_id (optional): use EXACTLY this tag as the reference (for measuring its own pose),
   not the first one it finds. Use it when you want a CLOSE/reliable reference (e.g. tag14),
   because a distant reference gives an inaccurate pose -> an inaccurate new-tag measurement.
   (Lesson learned: the survey once picked tag10 as reference from ~2.5 m although tag14 was
   much closer/more reliable.)
"""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import (localize, load_tag_map, load_tag_sizes,
                               camera_extrinsic, tag_world_from_robot)
from core.capture import capture
from core.sweep import sweep_pans
from picarx import Picarx

_SHUT = [None]   # exposure is measured once and reused within the sweep

UID = int(sys.argv[1])
REP = int(sys.argv[2]) if len(sys.argv) > 2 else 3
TARGET_SIZE = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0   # edge of the NEW tag's black square in cm
REF = int(sys.argv[4]) if len(sys.argv) > 4 else None            # explicit reference tag (optional)

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))   # {id: size_cm}, default 6.0
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0); px.set_cam_pan_angle(0)
time.sleep(0.3)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    p = "/tmp/bt.jpg"
    _SHUT[0] = capture(p, w=1024, h=576, shutter_us=_SHUT[0])
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    # tag_size=1.0 -> pose_t in tag-size units; LATER multiplied by the tag's own size
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=1.0)

def center_on(pred):
    """sweeps, finds the first tag for which pred(r) is true, turns it to the centre.
    Returns: (detected_r, pan) or (None,None)."""
    found = None
    for pan in sweep_pans(-80, 80, step=40, current=0):   # wider: so it reaches a reference at ~90 degrees (e.g. tag7)
        if any(pred(r) for r in capture_detect(pan)):
            found = pan; break
    if found is None:
        px.set_cam_pan_angle(0); return None, None
    pan = found; r = None
    for _ in range(4):
        cand = [r for r in capture_detect(pan) if pred(r)]
        if not cand:
            pan -= math.copysign(8, pan or 1); continue
        r = max(cand, key=lambda r: r.decision_margin)
        off = float(r.center[0]) - cx
        if abs(off) < 20: break
        pan = max(-85, min(85, pan + math.degrees(math.atan2(off, fx))))
    return r, pan

def _is_ref(r):
    """suitable reference tag? (never UID; if REF is given, only that one.)"""
    tid = int(r.tag_id)
    if tid == UID or tid not in tag_map: return False
    return (tid == REF) if REF is not None else True

def robot_pose():
    # IMPORTANT: the target tag (UID) can NOT be a reference (circular if the target is stale)!
    # If REF is given: it looks for / centres EXACTLY that tag (close, reliable).
    r, pan = center_on(_is_ref)
    if r is None: return None
    dets = [{"tag_id":int(rr.tag_id),"pose_R":np.asarray(rr.pose_R),
             "pose_t":np.asarray(rr.pose_t).ravel()*SIZES.get(int(rr.tag_id),6.0),
             "decision_margin":float(rr.decision_margin)}
            for rr in capture_detect(pan) if _is_ref(rr)]
    if not dets: return None
    return localize(dets, tag_map, camera_extrinsic(pan_deg=pan, tilt_deg=TILT))

def measure_unknown(pose):
    r, pan = center_on(lambda r: int(r.tag_id) == UID)
    if r is None: return None
    ext = camera_extrinsic(pan_deg=pan, tilt_deg=TILT)
    # the UNKNOWN tag's pose_t is scaled by its OWN size (TARGET_SIZE)
    return tag_world_from_robot(pose.x_cm, pose.y_cm, pose.heading_deg,
                                np.asarray(r.pose_R), np.asarray(r.pose_t).ravel()*TARGET_SIZE, ext)

print(f"SURVEY: tag {UID}  size={TARGET_SIZE:.0f}cm  (average of {REP} repeats)")
results = []
try:
    for i in range(REP):
        pose = robot_pose()
        if pose is None:
            print(f"  [{i}] I see no KNOWN tag (own pose) -> skip"); continue
        w = measure_unknown(pose)
        if w is None:
            print(f"  [{i}] robot ({pose.x_cm:.0f},{pose.y_cm:.0f},{pose.heading_deg:.0f}) "
                  f"but tag {UID} is not visible when sweeping -> skip"); continue
        # sanity: flat bounds (invented example flat x[-400,500], y[-100,500]) + ~40 cm margin
        oob_r = not (-440 <= pose.x_cm <= 540 and -140 <= pose.y_cm <= 540)
        oob_t = not (-440 <= w['x_cm'] <= 540 and -140 <= w['y_cm'] <= 540)
        flag = "  <<< SUSPICIOUS (outside the walls, ambiguity?)" if (oob_r or oob_t) else ""
        print(f"  [{i}] robot ({pose.x_cm:.0f},{pose.y_cm:.0f},{pose.heading_deg:.0f}) "
              f"-> tag{UID}: x={w['x_cm']:.0f} y={w['y_cm']:.0f} z={w['z_cm']:.0f} yaw={w['yaw_deg']:.0f}{flag}")
        if oob_r or oob_t:
            continue   # measurement outside the walls dropped
        results.append(w)
    if results:
        ax = np.median([r["x_cm"] for r in results]); ay = np.median([r["y_cm"] for r in results])
        az = np.median([r["z_cm"] for r in results]); ayaw = np.median([r["yaw_deg"] for r in results])
        print(f"  >>> tag {UID} SURVEYED (median): x={ax:.0f} y={ay:.0f} z={az:.0f} yaw={ayaw:.0f}")
        print(f"      tags_coords.json line: \"{UID}\": {{\"x_cm\": {ax:.0f}, \"y_cm\": {ay:.0f}, \"z_cm\": {az:.0f}, \"yaw_deg\": {ayaw:.0f}}}")
    else:
        print("  NO usable measurement.")
finally:
    px.set_cam_pan_angle(0)
print("done")
