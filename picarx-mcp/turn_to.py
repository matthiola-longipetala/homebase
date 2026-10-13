"""turn_to.py — TURNING AROUND / K-turn to a target heading, ~in place.

Problem: the forward-only navigators (goto_scan/goto_dr/goto_route) cannot turn
around — if the target is BEHIND the robot, they would arc forward into the wall.
Solution: a K-turn. Alternating
  - FORWARD + turn  (in the rotation direction; with the SONAR BRAKE)
  - BACKWARD + counter-turn (rotates in the same direction; SHORT, no rear sonar)
arcs rotate it ~in place to the target heading. The key is the same rotation direction:
  CCW (left): (forward, steer<0) AND (backward, steer>0) both rotate CCW;
  the two opposite displacements partly cancel -> small net displacement, large rotation.

TURN IN PLACE: if the FORWARD and BACKWARD arcs are EQUALLY LONG (same |steer|), their
SIDEWAYS displacements cancel -> net sideways drift ~0, only a tiny (~R*φ², a few cm)
BACKWARD remainder. Previously FWD_DUR(0.45) >> REV_DUR(0.30) -> it went forward more,
so it DRIFTED in the direction of rotation (observed in a test run). Now
FWD_DUR == REV_DUR -> it practically turns IN PLACE, so there is no oversized arc when
turning in at a door. (Compromise: the backward arc is a bit longer and blind -> we keep
the arcs short; the rear distance sensor protects this too.)

args:  turn_to.py <target_heading_deg>
   or  turn_to.py --to <X> <Y>   (turns towards the point from the current pose)
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
from core.motion import forward_guarded, sonar_cm
from picarx import Picarx

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

_SHUT = [None]
TOL_DEG = 14.0          # within this we are "facing" the target
MAX_CYCLES = 10
FWD_DUR = 0.40          # forward burst length (with sonar brake)
REV_DUR = 0.40          # backward burst = forward burst -> turns IN PLACE (no sideways drift), tiny backward remainder
STEER = 25

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    _SHUT[0] = capture("/tmp/tt.jpg", w=1024, h=576, shutter_us=_SHUT[0])
    img = cv2.undistort(np.array(Image.open("/tmp/tt.jpg").convert("L")), K, D)
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx, fy, cx, cy], tag_size=1.0)

def scan_fix():
    """sweep -> centre the tag -> clean fix. (PoseEstimate, pan) or None."""
    _SHUT[0] = None
    found = None
    for pan in sweep_pans(-60, 60, step=40, current=0):
        res = capture_detect(pan)
        if any(int(r.tag_id) in tag_map for r in res):
            found = pan; break
    if found is None:
        return None
    pan = found
    for _ in range(4):
        res = capture_detect(pan)
        known = [r for r in res if int(r.tag_id) in tag_map]
        if not known:
            pan -= math.copysign(8, pan or 1); continue
        r = max(known, key=lambda r: r.decision_margin)
        off = float(r.center[0]) - cx
        if abs(off) < 25: break
        pan = max(-70, min(70, pan + math.degrees(math.atan2(off, fx))))
    res = capture_detect(pan)
    dets = [{"tag_id": int(r.tag_id), "pose_R": np.asarray(r.pose_R),
             "pose_t": np.asarray(r.pose_t).ravel() * SIZES.get(int(r.tag_id), 6.0),
             "decision_margin": float(r.decision_margin)}
            for r in res if int(r.tag_id) in tag_map]
    if not dets:
        return None
    ext = camera_extrinsic(pan_deg=pan, tilt_deg=TILT)
    pose = localize(dets, tag_map, ext)
    try:
        from core.obsdb import record
        if pose is not None: record(pose, dets, ext, source="turn_to")
    except Exception: pass
    return (pose, pan) if pose is not None else None

def norm180(a):
    while a > 180: a -= 360
    while a < -180: a += 360
    return a

# --- determine the target heading ---
if sys.argv[1] == "--to":
    tx, ty = float(sys.argv[2]), float(sys.argv[3])
    target = None  # computed from the pose
else:
    target = float(sys.argv[1]); tx = ty = None

print(f"TARGET: {'point '+str((tx,ty)) if target is None else f'heading {target:.0f}'}")
eh = None            # estimated heading (from a fix or dead reckoning)
tgt = None           # target heading (kept if we lose the fix in --to mode)
noflix = 0           # consecutive cycles without a tag
try:
    for cyc in range(MAX_CYCLES):
        out = scan_fix()
        px.set_cam_pan_angle(0)
        if out is not None:
            p, _ = out
            eh = p.heading_deg
            tgt = math.degrees(math.atan2(ty - p.y_cm, tx - p.x_cm)) if target is None else target
            noflix = 0; src = "fix"
        else:
            noflix += 1
            if eh is None or tgt is None:
                print(f"  [{cyc}] no tag fix and no estimate -> STOP"); break
            if noflix > 4:
                print(f"  [{cyc}] no tag fix for {noflix} cycles -> STOP (lost)"); break
            src = f"DR({noflix})"     # dead reckoning: continue with the heading estimated from the model
        err = norm180(tgt - eh)
        print(f"  [{cyc}] hdg~{eh:.0f} target={tgt:.0f} error={err:.0f} [{src}]")
        if abs(err) <= TOL_DEG:
            print("  >>> FACING THE TARGET!"); break
        ccw = err > 0                      # +error -> need to turn CCW (left)
        # FORWARD + turn in the rotation direction (CCW=left=neg steer), with sonar brake
        fwd_steer = -STEER if ccw else STEER
        px.set_dir_servo_angle(fwd_steer); time.sleep(0.12)
        stopped, dd = forward_guarded(px, 30, FWD_DUR)
        dh_fwd = 0.0 if stopped else (-4.2 - 1.325 * fwd_steer) * FWD_DUR
        if stopped:
            print(f"    forward blocked ({dd:.0f}cm) - backward arc only this time")
        # BACKWARD + counter-turn (gives the same rotation direction), SHORT
        rev_steer = STEER if ccw else -STEER
        px.set_dir_servo_angle(rev_steer); time.sleep(0.12)
        px.forward(-30); time.sleep(REV_DUR); px.forward(0)
        px.set_dir_servo_angle(0)
        dh_rev = -(-4.2 - 1.325 * rev_steer) * REV_DUR   # reverse: the opposite of the fwd sign
        eh = norm180(eh + dh_fwd + dh_rev)               # dead-reckoning update
        time.sleep(0.25)
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done")
