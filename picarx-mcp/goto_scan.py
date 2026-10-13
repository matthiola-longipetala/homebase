"""goto_scan.py — scan-based pure-pursuit navigation to an (x,y) target.

At every step the robot SWEEPS: it finds the tag and turns it to the CENTRE with an
image-based closed loop, takes a CLEAN fix there (the pan model is accurate in the centre),
then recentres the camera and drives FORWARD a little. This way a tag slipping out to the
side no longer interrupts navigation (goto_xy failed on that).

args: target_x target_y [arrive_radius_cm]
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
from core.motion import forward_guarded, clear_ahead, sonar_cm
from core.nogo import load_zones, path_blocked, point_blocked
from picarx import Picarx

_SHUT = [None]   # exposure is measured once per scan_fix and reused within the sweep

target_x = float(sys.argv[1])
target_y = float(sys.argv[2])
arrive_r = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
# NO-GO zones (wired in following goto_dr; only HARD zones stop it).
try:
    ZONES = load_zones(os.path.expanduser("~/picarx-mcp/nogo_zones.json"))
except Exception as e:
    ZONES = []; print(f"  (no-go zones did not load: {e} -> without the guard)")
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0); px.set_cam_pan_angle(0)
time.sleep(0.3)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    p = "/tmp/gs.jpg"
    _SHUT[0] = capture(p, w=1024, h=576, shutter_us=_SHUT[0])
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=1.0)

def scan_fix():
    """sweep -> centre the tag -> clean fix. Returns: (PoseEstimate, used_pan) or None."""
    _SHUT[0] = None              # new fix -> re-measure exposure for the current lighting
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
        off_px = float(r.center[0]) - cx
        if abs(off_px) < 25:
            break
        pan = max(-70, min(70, pan + math.degrees(math.atan2(off_px, fx))))
    res = capture_detect(pan)
    dets = [{"tag_id":int(r.tag_id),"pose_R":np.asarray(r.pose_R),
             "pose_t":np.asarray(r.pose_t).ravel()*SIZES.get(int(r.tag_id),6.0),
             "decision_margin":float(r.decision_margin)}
            for r in res if int(r.tag_id) in tag_map]
    if not dets:
        return None
    ext = camera_extrinsic(pan_deg=pan, tilt_deg=TILT)
    pose = localize(dets, tag_map, ext)
    try:
        from core.obsdb import record
        if pose is not None: record(pose, dets, ext, source="goto_scan")
    except Exception: pass
    return pose, pan

def propagate(x, y, h, steer, dur):
    # motion model (same as goto_dr) — to estimate the step end point for the no-go guard
    dh = (-4.2 - 1.325*steer) * dur
    d  = (31.4 - 0.175*abs(steer)) * dur
    hmid = math.radians(h + dh/2.0)
    return x + d*math.cos(hmid), y + d*math.sin(hmid), h + dh

def norm180(a):
    while a > 180: a -= 360
    while a < -180: a += 360
    return a

print(f"TARGET: x={target_x:.0f} y={target_y:.0f} (arrival radius {arrive_r:.0f} cm)")
zc = point_blocked(ZONES, target_x, target_y)
if zc is not None:
    print(f"  >>> THE TARGET IS IN A FORBIDDEN ZONE ({zc.name}) -> not starting"); sys.exit(2)
try:
    for step in range(12):
        out = scan_fix()
        px.set_cam_pan_angle(0)              # camera back to centre for driving
        if out is None:
            print(f"  [{step}] no known tag found even by sweeping -> STOP"); break
        p, used_pan = out
        dx = target_x - p.x_cm; dy = target_y - p.y_cm
        dist = math.hypot(dx, dy)
        desired = math.degrees(math.atan2(dy, dx))
        herr = norm180(desired - p.heading_deg)
        print(f"  [{step}] pos x={p.x_cm:.0f} y={p.y_cm:.0f} hdg={p.heading_deg:.0f} "
              f"(fix pan={used_pan:.0f})  dist={dist:.0f} target_dir={desired:.0f} error={herr:.0f}")
        if dist < arrive_r:
            print("  >>> TARGET REACHED!"); break
        if not clear_ahead(px):
            print(f"  obstacle {sonar_cm(px):.0f} cm -> STOP"); break
        steer = max(-25, min(25, -0.7 * herr))
        dur = min(dist, 22) / 22.0
        nx, ny, _ = propagate(p.x_cm, p.y_cm, p.heading_deg, steer, dur)
        zb = path_blocked(ZONES, p.x_cm, p.y_cm, nx, ny)
        if zb is not None:
            print(f"  >>> NO-GO zone AHEAD ({zb.name}) -> STOP (sonar-blind hazard)"); break
        px.set_dir_servo_angle(steer)
        time.sleep(0.2)
        stopped, dd = forward_guarded(px, 30, dur)   # sonar brake while driving too
        if stopped:
            print(f"  sonar brake {dd:.0f} cm while driving -> STOP (for re-planning)"); break
        time.sleep(0.3)
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done")
