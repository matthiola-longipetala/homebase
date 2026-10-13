"""pan_sweep.py — clean diagnostics of the pan model.
The robot stays in place, only the camera sweeps. pan=0 is the TRUTH.
At every pan angle we re-localise with camera_extrinsic(pan_deg=pan)
and check how far it deviates from the pan=0 truth.
If the pan model is correct, every row gives ~the same (dx~dy~dh~0).
"""
import os
import json, subprocess, sys, time
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, camera_extrinsic
from picarx import Picarx

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
px.set_dir_servo_angle(0); px.set_cam_tilt_angle(0)

def loc_at(pan_servo, pan_math):
    px.set_cam_pan_angle(pan_servo); time.sleep(0.6)
    p = "/tmp/sweep.jpg"
    subprocess.run(["rpicam-still","-o",p,"--width",CAM_W,"--height",CAM_H,"-t","500","-n",
                    "--autofocus-mode","manual","--lens-position",LENS_POS],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    res = det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=6.0)
    if not res:
        return None, None
    dets = [{"tag_id":int(r.tag_id),"pose_R":np.asarray(r.pose_R),
             "pose_t":np.asarray(r.pose_t).ravel(),"decision_margin":float(r.decision_margin)} for r in res]
    ext = camera_extrinsic(pan_deg=pan_math, tilt_deg=TILT)
    pose = localize(dets, tag_map, ext)
    # raw: the nearest tag's pose_t (camera frame) and its offset from the image centre
    raw = dets[0]
    return pose, raw

# 1) truth at pan=0
truth, _ = loc_at(0, 0)
if truth is None:
    print("I see no tag at pan=0 -> place the robot so that it can see one"); sys.exit(1)
print(f"TRUTH (pan=0): x={truth.x_cm:.1f} y={truth.y_cm:.1f} hdg={truth.heading_deg:.1f}")
print(f"{'panServo':>8} {'panMath':>7} {'x':>7} {'y':>7} {'hdg':>6} {'dx':>6} {'dy':>6} {'dh':>6} {'tag':>4}")

# 2) sweep: panMath = panServo (the 'correct' assumption). If it is wrong, the trend shows.
for pan in [-30, -20, -10, 0, 10, 20, 30]:
    pose, raw = loc_at(pan, pan)
    if pose is None:
        print(f"{pan:>8} {pan:>7} {'--- no tag in view ---':>40}")
        continue
    dx = pose.x_cm - truth.x_cm
    dy = pose.y_cm - truth.y_cm
    dh = pose.heading_deg - truth.heading_deg
    print(f"{pan:>8} {pan:>7} {pose.x_cm:>7.1f} {pose.y_cm:>7.1f} {pose.heading_deg:>6.1f} "
          f"{dx:>6.1f} {dy:>6.1f} {dh:>6.1f} {raw['tag_id']:>4}")

px.set_cam_pan_angle(0); time.sleep(0.3)
print("done")
