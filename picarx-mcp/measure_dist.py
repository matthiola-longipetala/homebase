"""measure_dist.py — DISTANCE MEASUREMENT TEST (with the CORRECTED tag_range, CALIB_K=1.2).
Finds the best-centred tag (tilt 0; if none there, +15), averages several images for a
stable pixel size, and prints:
  CORRECTED pixel distance (main guess) | raw pixel distance | pose distance | sonar | bearing.
Usage: place the robot at a KNOWN distance in front of a tag, run it, compare with a tape measure.
"""
import os
import sys, json, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
from core.capture import capture
from core.tag_range import tag_range_angular_cm, tag_pixel_size, tag_bearing_deg
from core.motion import sonar_cm
from core.localization import load_tag_sizes
from picarx import Picarx

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
det = Detector(families="tag36h11", nthreads=4)
px = Picarx(); px.set_cam_pan_angle(0); _s = [None]

# sonar
px.set_cam_tilt_angle(0); time.sleep(0.3)
son = [sonar_cm(px) for _ in range(6)]; son = sorted(s for s in son if s and s > 0)
sonar = son[len(son) // 2] if son else None

# collect tags at tilt 0 then +15, per image; accumulate the measurements per tag
acc = {}   # tid -> list of (px_size, raw, korr, pose, center_x)
for tilt in (0, 15):
    px.set_cam_tilt_angle(tilt); time.sleep(0.45)
    for _ in range(3):
        _s[0] = capture("/tmp/md.jpg", w=1024, h=576, shutter_us=_s[0])
        img = cv2.undistort(np.array(Image.open("/tmp/md.jpg").convert("L")), K, D)
        for r in det.detect(img, estimate_tag_pose=True, camera_params=[fx, fy, cx, cy], tag_size=1.0):
            tid = int(r.tag_id); size = SIZES.get(tid, 6.0)
            # angle-based distance (safe at the image edge, validated with edge_check)
            raw = tag_range_angular_cm(r.corners, size, fx, fy, cx, cy, corrected=False)
            korr = tag_range_angular_cm(r.corners, size, fx, fy, cx, cy)
            pose = float(np.linalg.norm(np.asarray(r.pose_t).ravel())) * size
            acc.setdefault(tid, []).append((tag_pixel_size(r.corners), raw, korr, pose, float(r.center[0])))
    if acc:   # if there is already a tag at tilt 0, +15 is not needed
        break
px.set_cam_tilt_angle(0)

print(f"SONAR: {round(sonar,1) if sonar else 'none'} cm")
if not acc:
    print("no tag (neither at tilt 0 nor at +15)"); print("done"); sys.exit()
# the best-centred tag (smallest mean |center-cx|)
def centerness(v): return abs(np.mean([m[4] for m in v]) - cx)
for tid in sorted(acc, key=lambda t: centerness(acc[t])):
    v = acc[tid]; size = SIZES.get(tid, 6.0)
    pxs = np.mean([m[0] for m in v]); raw = np.mean([m[1] for m in v])
    korr = np.mean([m[2] for m in v]); pose = np.mean([m[3] for m in v])
    bearing = tag_bearing_deg(float(np.mean([m[4] for m in v])), 0.0, fx, cx)
    star = " <<< best centred" if tid == sorted(acc, key=lambda t: centerness(acc[t]))[0] else ""
    print(f"tag{tid} ({size:.0f}cm, {pxs:.0f}px, n={len(v)}): "
          f"CORRECTED={korr:.0f}cm | raw={raw:.0f} | pose={pose:.0f} | bearing={bearing:+.1f}{star}")
print("done")
