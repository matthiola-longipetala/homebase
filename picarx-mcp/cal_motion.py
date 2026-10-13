"""cal_motion.py — motion-model calibration for dead reckoning.

fix A (scan+centering) -> known command (speed, steering, time) -> fix B.
From the difference of the two fixes: distance travelled, FORWARD component, sideways
component, heading change. This gives the cm/s and deg/s for the given command.

args: speed steer duration_s
  e.g. straight: python3 cal_motion.py 30 0 1.0
       turning:  python3 cal_motion.py 30 20 1.0
"""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, camera_extrinsic
from picarx import Picarx

SPEED = int(sys.argv[1]) if len(sys.argv) > 1 else 30
STEER = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
DUR   = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0

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
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0); px.set_cam_pan_angle(0)
time.sleep(0.3)

def capture_detect(pan):
    px.set_cam_pan_angle(pan); time.sleep(0.45)
    p = "/tmp/cm.jpg"
    subprocess.run(["rpicam-still","-o",p,"--width",CAM_W,"--height",CAM_H,"-t","420","-n",
                    "--autofocus-mode","manual","--lens-position",LENS_POS],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    return det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=6.0)

def scan_fix():
    found = None
    for pan in [0, -20, 20, -40, 40, -60, 60]:
        if any(int(r.tag_id) in tag_map for r in capture_detect(pan)):
            found = pan; break
    if found is None:
        return None
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
             "pose_t":np.asarray(r.pose_t).ravel(),"decision_margin":float(r.decision_margin)}
            for r in capture_detect(pan) if int(r.tag_id) in tag_map]
    px.set_cam_pan_angle(0)
    if not dets: return None
    return localize(dets, tag_map, camera_extrinsic(pan_deg=pan, tilt_deg=TILT))

print(f"CALIBRATION: speed={SPEED} steer={STEER} dur={DUR}s")
try:
    A = scan_fix()
    if A is None:
        print("  fix A: I see no tag -> place me so that I can see one"); sys.exit(1)
    print(f"  fix A: x={A.x_cm:.1f} y={A.y_cm:.1f} hdg={A.heading_deg:.1f}")
    px.set_dir_servo_angle(STEER); time.sleep(0.2)
    px.forward(SPEED); time.sleep(DUR); px.forward(0)
    px.set_dir_servo_angle(0); time.sleep(0.5)
    B = scan_fix()
    if B is None:
        print("  fix B: the tag was lost after the motion"); sys.exit(1)
    print(f"  fix B: x={B.x_cm:.1f} y={B.y_cm:.1f} hdg={B.heading_deg:.1f}")
    dx, dy = B.x_cm - A.x_cm, B.y_cm - A.y_cm
    dist = math.hypot(dx, dy)
    dh = B.heading_deg - A.heading_deg
    while dh > 180: dh -= 360
    while dh < -180: dh += 360
    # forward/sideways components projected onto A's heading
    hr = math.radians(A.heading_deg)
    fwd = dx*math.cos(hr) + dy*math.sin(hr)
    lat = -dx*math.sin(hr) + dy*math.cos(hr)
    print(f"  -> displacement: dist={dist:.1f}cm  forward={fwd:.1f}cm  sideways={lat:.1f}cm  dheading={dh:.1f}deg")
    print(f"  -> RATES: {dist/DUR:.1f} cm/s (distance)  {fwd/DUR:.1f} cm/s (forward)  {dh/DUR:.1f} deg/s (turning)")
finally:
    px.forward(0); px.set_dir_servo_angle(0); px.set_cam_pan_angle(0)
print("done")
