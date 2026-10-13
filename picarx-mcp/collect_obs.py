"""collect_obs.py — collecting cross-observations for refining the tag map.

From one robot position: it sweeps, determines the robot's pose from the NEAREST known tag
(close = accurate, no ambiguity), then logs the implied world pose of EVERY OTHER visible
known tag from that pose -> NON-circular cross-observations.

Run from several different places, the data accumulates; refine_tags.py later computes a
weighted median from it. args: none (collects from the current spot).
"""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, load_tag_sizes, camera_extrinsic
from core.obsdb import record
from core.capture import capture
from picarx import Picarx

_SHUT = [None]   # exposure is measured once and reused within the sweep

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0); px.set_cam_pan_angle(0)
time.sleep(0.3)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    p = "/tmp/co.jpg"
    _SHUT[0] = capture(p, w=1024, h=576, shutter_us=_SHUT[0])
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=1.0)

# 1) full sweep: the best (nearest, closest-to-centre) detection of every known tag
seen = {}   # tag_id -> (pan, pose_R, pose_t_cm, margin, dist, off_px)
for pan in [-75,-60,-45,-30,-15,0,15,30,45,60,75]:
    for r in capture_detect(pan):
        tid = int(r.tag_id)
        if tid not in tag_map: continue
        pt = np.asarray(r.pose_t).ravel() * SIZES.get(tid, 6.0)
        dist = float(np.linalg.norm(pt)); off = abs(float(r.center[0]) - cx)
        prev = seen.get(tid)
        # keep the one with the smallest offset from the image centre (most accurate pose for that tag)
        if prev is None or off < prev[5]:
            seen[tid] = (pan, np.asarray(r.pose_R), pt, float(r.decision_margin), dist, off)
px.set_cam_pan_angle(0)

if not seen:
    print("I see no known tag from here"); sys.exit(0)
print("seen tags:", {t:(round(v[4]) ) for t,v in seen.items()}, "(distance cm)")

# 2) anchor = nearest tag -> robot pose
anchor = min(seen, key=lambda t: seen[t][4])
ap = seen[anchor]
ext_a = camera_extrinsic(pan_deg=ap[0], tilt_deg=TILT)
pose = localize([{"tag_id":anchor,"pose_R":ap[1],"pose_t":ap[2],"decision_margin":ap[3]}],
                tag_map, ext_a)
if pose is None:
    print("anchor localisation failed"); sys.exit(0)
print(f"anchor=tag{anchor} (distance {ap[4]:.0f}cm) -> robot pose x={pose.x_cm:.0f} y={pose.y_cm:.0f} hdg={pose.heading_deg:.0f}")

# 3) log the implied pose of every seen tag from this pose (with their own pan)
n = 0
for tid, (pan, pR, pt, margin, dist, off) in seen.items():
    ext = camera_extrinsic(pan_deg=pan, tilt_deg=TILT)
    # record flags circularity based on pose.tag_ids (anchor=circular)
    n += record(pose, [{"tag_id":tid,"pose_R":pR,"pose_t":pt,"decision_margin":margin}],
                ext, source="collect_obs")
print(f">>> {n} observations logged ({len(seen)} tags). Non-circular: {len(seen)-1} (all except the anchor).")
print("done")
