"""whereami.py — WHERE AM I? Robust self-localisation from the CALIBRATED tag distance + bearing.

Sweeps around (pan -70..+70), measures every KNOWN tag, and prints per tag:
  - the tag's room + world coordinates
  - CALIBRATED distance (tag_range_cm, 1.08*raw+12, flip-independent, from the pixel size)
  - bearing (the tag's direction relative to the robot's heading)
  - decision_margin (reliability)
Then it computes the robot's (x,y) from the intersection of TWO tags' circles
(robot_xy_from_two_tags), filtered to the flat's bounds, as the median over all pairs. The
heading is estimated from the strongest tag's bearing. As a cross-check it also runs the
old localize().

Usage: ssh ... 'cd ~/picarx-mcp && PYTHONUTF8=1 python3 whereami.py'

NOTE: the lines "SZONAR (elore): ...", "LATOTT ismert tagek: [...]" (= seen known tags),
"KALIBRALT-TAV alapjan: x=.. y=..  hdg~.." (= based on calibrated distance) and "vege" are
parsed/mirrored by utvonalak/route_runner.py and home_reflex.py — keep them unchanged.
"""
import os
import sys, json, time, math
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
from core.capture import capture
from core.tag_range import (tag_range_cm, tag_range_angular_cm, tag_pixel_size,
                            tag_bearing_deg, robot_xy_from_two_tags)
from core.localization import localize, load_tag_map, load_tag_sizes, camera_extrinsic
from core.motion import sonar_cm
from core.sweep import sweep_pans
from picarx import Picarx

# usage: whereami.py [--tilt DEG]   (default 10; for high tags e.g. --tilt 30)
BASETILT = 10.0
if "--tilt" in sys.argv:
    BASETILT = float(sys.argv[sys.argv.index("--tilt") + 1])

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
TAGW = json.load(open(os.path.expanduser("~/picarx-mcp/tags_coords.json")))["tags"]
det = Detector(families="tag36h11", nthreads=4)

# flat bounds (from the tag coordinates, with a small allowance) -> filtering intersection candidates.
# Values = the invented example flat (x -400..500, y -100..500) + 40 cm; set them to YOUR flat.
XMIN, XMAX, YMIN, YMAX = -440, 540, -140, 540

px = Picarx()
# steering wiggle (against sticking)
for a in (15, -15, 0): px.set_dir_servo_angle(a); time.sleep(0.2)
px.set_cam_pan_angle(0); time.sleep(0.2)

# sonar (rough forward distance)
son = sorted(s for s in (sonar_cm(px) for _ in range(6)) if s and s > 0)
sonar = son[len(son)//2] if son else None

_SHUT = [None]
acc = {}   # tid -> best detection dict (largest margin)

def do_sweep(tilt):
    """sweeps edge to edge (servo-friendly, core.sweep) at the given tilt."""
    px.set_cam_tilt_angle(tilt); time.sleep(0.35)
    for pan in sweep_pans(-70, 70, step=35, current=0):
        px.set_cam_pan_angle(pan); time.sleep(0.45)
        _SHUT[0] = capture("/tmp/wa.jpg", w=1024, h=576, shutter_us=_SHUT[0])
        img = cv2.undistort(np.array(Image.open("/tmp/wa.jpg").convert("L")), K, D)
        for r in det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=1.0):
            tid = int(r.tag_id)
            if tid not in tag_map: continue
            size = SIZES.get(tid, 6.0)
            # angle-based distance (safe at the image edge) — the pixel-based one under-estimated
            # by sec^2(theta) at the edges (navigacio/edge_check.py: 122 cm -> 9 cm spread)
            dist = tag_range_angular_cm(r.corners, size, fx, fy, cx, cy)
            bearing = tag_bearing_deg(float(r.center[0]), pan, fx, cx)
            margin = float(r.decision_margin)
            cur = acc.get(tid)
            if cur is None or margin > cur["margin"]:
                acc[tid] = {"tid": tid, "dist": dist, "bearing": bearing, "margin": margin,
                            "pan": pan, "px": tag_pixel_size(r.corners),
                            "pose_R": np.asarray(r.pose_R),
                            "pose_t": np.asarray(r.pose_t).ravel()*size}

do_sweep(BASETILT)
if not acc:
    # tags mounted high (e.g. a big tag at the end of a hallway) fall out of the horizontal sweep
    print(f"(nothing at tilt={BASETILT:.0f} -> retrying higher, tilt={BASETILT+20:.0f})")
    do_sweep(BASETILT + 20)
px.set_cam_pan_angle(0); px.set_cam_tilt_angle(0)

print(f"SZONAR (elore): {round(sonar,1) if sonar else 'nincs'} cm")
print(f"LATOTT ismert tagek: {sorted(acc)}  (n={len(acc)})")
if not acc:
    print("no known tag -> I cannot localise myself"); print("vege"); sys.exit()

print("\n--- PER TAG (calibrated distance + direction) ---")
for tid in sorted(acc, key=lambda t: -acc[t]["margin"]):
    a = acc[tid]; w = TAGW[str(tid)]
    room = w.get("room", "?")
    print(f"  tag{tid:>2} [{room[:34]:34}] world=({w['x_cm']:+4},{w['y_cm']:+4}) | "
          f"DIST={a['dist']:5.0f}cm | bearing={a['bearing']:+6.1f} | margin={a['margin']:3.0f} | {a['px']:.0f}px")

# --- robot (x,y) from two-tag circles, as the median over all pairs ---
def in_house(p): return XMIN <= p[0] <= XMAX and YMIN <= p[1] <= YMAX
strong = [acc[t] for t in acc if acc[t]["margin"] >= 12]
use = strong if len(strong) >= 2 else [acc[t] for t in acc]
cands = []
for i in range(len(use)):
    for j in range(i+1, len(use)):
        A, B = use[i], use[j]
        wa = (TAGW[str(A['tid'])]['x_cm'], TAGW[str(A['tid'])]['y_cm'])
        wb = (TAGW[str(B['tid'])]['x_cm'], TAGW[str(B['tid'])]['y_cm'])
        pts = robot_xy_from_two_tags(wa, A['dist'], wb, B['dist'])
        inside = [p for p in pts if in_house(p)]
        for p in inside:
            cands.append((p, A['tid'], B['tid']))

print("\n--- ROBOT POSITION ---")
if cands:
    xs = sorted(c[0][0] for c in cands); ys = sorted(c[0][1] for c in cands)
    mx = xs[len(xs)//2]; my = ys[len(ys)//2]
    # heading from the strongest tag's bearing: world_dir = atan2(ty-ry,tx-rx); heading = world_dir - bearing
    best = max(use, key=lambda d: d["margin"])
    wb = (TAGW[str(best['tid'])]['x_cm'], TAGW[str(best['tid'])]['y_cm'])
    world_dir = math.degrees(math.atan2(wb[1]-my, wb[0]-mx))
    heading = (world_dir - best['bearing'] + 180) % 360 - 180
    print(f"  KALIBRALT-TAV alapjan: x={mx:+.0f} y={my:+.0f}  hdg~{heading:+.0f}  "
          f"(strongest tag{best['tid']}, {len(cands)} pair intersections)")
    for c in cands:
        print(f"      tag{c[1]}+tag{c[2]} -> ({c[0][0]:+.0f},{c[0][1]:+.0f})")
else:
    print("  (no valid two-tag intersection within the flat bounds)")

# --- cross-check: the old localize() from the best-centred frame ---
# every tag was best at its own pan; localize expects one common extrinsic,
# so we use the strongest tag's pan (approximately). Informational only.
best = max(acc.values(), key=lambda d: d["margin"])
ext = camera_extrinsic(pan_deg=best["pan"], tilt_deg=TILT)
dets = [{"tag_id": best["tid"], "pose_R": best["pose_R"], "pose_t": best["pose_t"],
         "decision_margin": best["margin"]}]
pose = localize(dets, tag_map, ext)
if pose is not None:
    print(f"  POSE-localize (tag{best['tid']} only, pan={best['pan']}, informational): "
          f"x={pose.x_cm:+.0f} y={pose.y_cm:+.0f} hdg={pose.heading_deg:+.0f}")
print("vege")
