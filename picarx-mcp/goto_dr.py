"""goto_dr.py — navigation to an (x,y) target FUSING dead reckoning + tag fixes.

Every step:
  1) scan_fix: if it sees a known tag -> the position estimate SNAPS to the fix (reset).
     if not -> the dead-reckoned estimate (from the motion model) stays.
  2) pure-pursuit steering from the estimate.
  3) known command (speed30, steer, dur) -> the MOTION MODEL propagates the estimate.

Motion model (calibrated at speed 30):
  forward: d   = (27.0 - 0.175*|steer|) * dur   [cm]
  turn:    dh  = (-4.2 - 1.325*steer)   * dur   [deg]  (+steer=right=heading decreases)

Safety: sonar brake; STOP after MAX_BLIND consecutive steps without a tag.
args: target_x target_y [arrive_radius_cm]
"""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, load_tag_sizes, camera_extrinsic
from core.motion import forward_guarded
from core.nogo import load_zones, path_blocked, point_blocked
from core.sweep import sweep_pans
from picarx import Picarx

target_x = float(sys.argv[1]); target_y = float(sys.argv[2])
arrive_r = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
MAX_BLIND = 5

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
# IMX708 Wide: resolution + LOCKED focus from camera_config.json.
# Without the AF lock the lens would move and fx (distance/bearing) would drift!
_cc = json.load(open(os.path.expanduser("~/picarx-mcp/camera_config.json")))
CAM_W, CAM_H = str(_cc["capture_width"]), str(_cc["capture_height"])
LENS_POS = str(_cc["lens_position"])
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
TILT = calib.get("tilt_offset_deg", 0.0)
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
SIZES = load_tag_sizes(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
# NO-GO zones (added after the robot once slid over a step threshold): a map-based guard
# for the sonar-BLIND hazards (threshold drop, rug, under the bed). See core/nogo.py.
try:
    ZONES = load_zones(os.path.expanduser("~/picarx-mcp/nogo_zones.json"))
except Exception as e:
    ZONES = []
    print(f"  (no-go zones did not load: {e} -> driving without the guard)")
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0); px.set_cam_pan_angle(0)
time.sleep(0.3)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    p = "/tmp/dr.jpg"
    subprocess.run(["rpicam-still","-o",p,"--width",CAM_W,"--height",CAM_H,"-t","420","-n",
                    "--autofocus-mode","manual","--lens-position",LENS_POS],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=1.0)

def scan_fix():
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
        if pose is not None: record(pose, dets, ext, source="goto_dr")
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

print(f"TARGET: x={target_x:.0f} y={target_y:.0f} (arrive {arrive_r:.0f}cm)  model: dead-reckoning+fix")
zc = point_blocked(ZONES, target_x, target_y)
if zc is not None:
    print(f"  >>> THE TARGET IS IN A FORBIDDEN ZONE ({zc.name})! Not starting. <<<"); sys.exit(2)
init = scan_fix()
if init is None:
    print("  I see no tag AT START -> place me so that I can see one"); sys.exit(1)
ex, ey, eh = init.x_cm, init.y_cm, init.heading_deg
blind = 0
try:
    for step in range(20):
        fix = scan_fix()
        if fix is not None:
            ex, ey, eh = fix.x_cm, fix.y_cm, fix.heading_deg
            blind = 0; src = "FIX"
        else:
            blind += 1; src = f"DR({blind})"
        dx, dy = target_x - ex, target_y - ey
        dist = math.hypot(dx, dy)
        desired = math.degrees(math.atan2(dy, dx))
        herr = norm180(desired - eh)
        print(f"  [{step}] {src:>6} pos x={ex:.0f} y={ey:.0f} hdg={eh:.0f}  dist={dist:.0f} error={herr:.0f}")
        if dist < arrive_r:
            print("  >>> TARGET REACHED!"); break
        if abs(herr) > 100 and dist < 60:
            # the target is now BEHIND the robot (overshoot) - forward-only control cannot reach it
            print(f"  target behind me (overshoot, dist={dist:.0f} error={herr:.0f}) -> STOP"); break
        if blind > MAX_BLIND:
            print(f"  no tag fix for {MAX_BLIND} steps -> STOP (safety)"); break
        try: d = px.ultrasonic.read()
        except Exception: d = -1
        if 0 < d < 22:
            # noise filtering: two more readings
            d2 = px.ultrasonic.read(); d3 = px.ultrasonic.read()
            if (0 < d2 < 22) and (0 < d3 < 22):
                print(f"  obstacle {d:.0f} cm (persistent) -> STOP"); break
        steer = max(-25, min(25, -0.7 * herr))
        dur = min(dist, 20) / 26.0
        # NO-GO STEP GUARD: where would THIS step take me according to the estimate? If the
        # path touches a forbidden zone -> STOP, the BRAIN decides (the guard alarms, the brain
        # drives). In that threshold incident the DR estimate was already inside the
        # zone -> this guard would have caught it BEFORE sliding over the threshold.
        nx, ny, _ = propagate(ex, ey, eh, steer, dur)
        zb = path_blocked(ZONES, ex, ey, nx, ny)
        if zb is not None:
            print(f"  >>> NO-GO zone AHEAD ({zb.name}) -> STOP (sonar-blind hazard) <<<")
            print(f"      estimated pos ({ex:.0f},{ey:.0f}) -> end of step ({nx:.0f},{ny:.0f})")
            break
        px.set_dir_servo_angle(steer); time.sleep(0.15)
        stopped, dd = forward_guarded(px, 30, dur)   # sonar brake while driving too
        px.set_dir_servo_angle(0)
        if stopped:
            print(f"  sonar brake {dd:.0f} cm while driving -> STOP"); break
        ex, ey, eh = propagate(ex, ey, eh, steer, dur)   # propagate the estimate
        time.sleep(0.25)
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done")
