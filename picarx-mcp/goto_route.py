"""goto_route.py — navigation between rooms along a waypoint list (through doors).

The robot targets the waypoints one by one with the goto_dr engine (scan fix + dead
reckoning + sonar brake + overshoot protection). Intermediate points have a looser arrival
radius, the final one a tight one. The pose estimate is continuous between legs.

args: x1 y1 x2 y2 ...   (waypoints in pairs, in world cm)
  e.g. dock -> living room (invented example flat, see navigacio/OLVASS_EL.md):
       python3 goto_route.py 0 290 -100 290 -150 250
"""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, load_tag_sizes, camera_extrinsic
from core.motion import forward_guarded
from core.capture import capture
from core.sweep import sweep_pans
from core.nogo import load_zones, check_route, path_blocked, point_blocked
from picarx import Picarx

_SHUT = [None]   # adaptive exposure cache (for evening light)

vals = [float(a) for a in sys.argv[1:]]
WPS = list(zip(vals[0::2], vals[1::2]))
if not WPS:
    print("give waypoints: x1 y1 x2 y2 ..."); sys.exit(1)

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
# NO-GO zones (wired in following goto_dr). Only HARD zones stop it (a 'soft' rug is
# passable). See core/nogo.py.
try:
    ZONES = load_zones(os.path.expanduser("~/picarx-mcp/nogo_zones.json"))
except Exception as e:
    ZONES = []; print(f"  (no-go zones did not load: {e} -> without the guard)")
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
# physical steering wiggle (against sticking) -> straight
for _a in [15, -15, 0]:
    px.set_dir_servo_angle(_a); time.sleep(0.3)
px.set_cam_tilt_angle(0); px.set_cam_pan_angle(0)
time.sleep(0.3)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    p = "/tmp/gr.jpg"
    _SHUT[0] = capture(p, w=1024, h=576, shutter_us=_SHUT[0])
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=1.0)

def scan_fix():
    _SHUT[0] = None       # new fix -> re-measure exposure for the current light
    found = None
    for pan in sweep_pans(-60, 60, step=40, current=0):
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
             "pose_t":np.asarray(r.pose_t).ravel()*SIZES.get(int(r.tag_id),6.0),
             "decision_margin":float(r.decision_margin)}
            for r in capture_detect(pan) if int(r.tag_id) in tag_map]
    px.set_cam_pan_angle(0)
    if not dets: return None
    ext = camera_extrinsic(pan_deg=pan, tilt_deg=TILT)
    pose = localize(dets, tag_map, ext)
    try:
        from core.obsdb import record
        if pose is not None: record(pose, dets, ext, source="goto_route")
    except Exception: pass
    return pose

def propagate(x, y, h, steer, dur):
    dh = (-4.2 - 1.325*steer) * dur
    d  = (31.4 - 0.175*abs(steer)) * dur
    hmid = math.radians(h + dh/2.0)
    return x + d*math.cos(hmid), y + d*math.sin(hmid), h + dh

def norm180(a):
    while a > 180: a -= 360
    while a < -180: a += 360
    return a

print(f"ROUTE: {WPS}")
hard_hits = check_route(ZONES, WPS)
if hard_hits:
    for h in hard_hits:
        print(f"  >>> leg {h['leg']} crosses a HARD no-go zone: {h['zone']} ({h['note'][:50]})")
    print("  >>> The route crosses a forbidden zone -> NOT starting (the BRAIN decides / a detour is needed)."); raise SystemExit
for h in check_route(ZONES, WPS, severities=("soft",)):
    print(f"  (note: leg {h['leg']} touches a SOFT zone: {h['zone']} — passable)")
ex = ey = eh = None
blind = 0
try:
    for wi, (tx, ty) in enumerate(WPS):
        last = (wi == len(WPS)-1)
        arrive = 12.0 if last else 20.0
        print(f"--- waypoint {wi+1}/{len(WPS)}: ({tx:.0f},{ty:.0f}) arrive={arrive:.0f} ---")
        zc = point_blocked(ZONES, tx, ty)
        if zc is not None:
            print(f"  >>> waypoint in a HARD zone ({zc.name}) -> skipping it"); continue
        for step in range(16):
            fix = scan_fix()
            if fix is not None:
                ex, ey, eh = fix.x_cm, fix.y_cm, fix.heading_deg; blind = 0; src = "FIX"
            elif ex is None:
                print("  no tag AT START -> STOP"); raise SystemExit
            else:
                blind += 1; src = f"DR({blind})"
            dx, dy = tx-ex, ty-ey; dist = math.hypot(dx, dy)
            desired = math.degrees(math.atan2(dy, dx)); herr = norm180(desired-eh)
            print(f"  [{step}] {src:>6} x={ex:.0f} y={ey:.0f} hdg={eh:.0f}  dist={dist:.0f} error={herr:.0f}")
            if dist < arrive:
                print(f"  >> waypoint {wi+1} reached"); break
            if abs(herr) > 100 and dist < 60:
                print(f"  target behind me (overshoot) -> next waypoint"); break
            if blind > 5:
                print("  no fix for 5 steps -> STOP"); raise SystemExit
            try: d = px.ultrasonic.read()
            except Exception: d = -1
            if 0 < d < 22:
                d2 = px.ultrasonic.read(); d3 = px.ultrasonic.read()
                if (0<d2<22) and (0<d3<22):
                    print(f"  obstacle {d:.0f}cm (persistent) -> STOP"); raise SystemExit
            steer = max(-25, min(25, -0.7*herr))
            dur = min(dist, 20)/26.0
            nx, ny, _ = propagate(ex, ey, eh, steer, dur)
            zb = path_blocked(ZONES, ex, ey, nx, ny)
            if zb is not None:
                print(f"  >>> NO-GO zone AHEAD ({zb.name}) -> STOP (sonar-blind hazard)"); raise SystemExit
            px.set_dir_servo_angle(steer); time.sleep(0.15)
            stopped, dd = forward_guarded(px, 30, dur)   # sonar brake while driving too
            px.set_dir_servo_angle(0)
            if stopped:
                print(f"  sonar brake {dd:.0f}cm while driving -> STOP"); raise SystemExit
            ex, ey, eh = propagate(ex, ey, eh, steer, dur)
            time.sleep(0.25)
    print(">>> ROUTE DONE")
except SystemExit:
    pass
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done")
