"""goto_xy.py — simple single-frame pure-pursuit to an (x,y) target (the predecessor of goto_scan)."""
import os
import json, subprocess, sys, time, math
import numpy as np, cv2
from PIL import Image
from pupil_apriltags import Detector
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.localization import localize, load_tag_map, camera_extrinsic
from picarx import Picarx

# args: target_x target_y  [arrive_radius_cm]
target_x = float(sys.argv[1])
target_y = float(sys.argv[2])
arrive_r = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
# IMX708 Wide: resolution + LOCKED focus from camera_config.json.
# Without the AF lock the lens would move and fx (distance/bearing) would drift!
_cc = json.load(open(os.path.expanduser("~/picarx-mcp/camera_config.json")))
CAM_W, CAM_H = str(_cc["capture_width"]), str(_cc["capture_height"])
LENS_POS = str(_cc["lens_position"])
fx, fy, cx, cy = calib["fx"], calib["fy"], calib["cx"], calib["cy"]
K = np.array(calib["camera_matrix"]); D = np.array(calib["dist_coeffs"])
ext = camera_extrinsic(pan_deg=0, tilt_deg=calib.get("tilt_offset_deg", 0.0))
tag_map = load_tag_map(os.path.expanduser("~/picarx-mcp/tags_coords.json"))
det = Detector(families="tag36h11", nthreads=4)
px = Picarx()
px.set_cam_pan_angle(0); px.set_cam_tilt_angle(0); px.set_dir_servo_angle(0)
time.sleep(0.4)

def localize_now():
    p = "/tmp/g.jpg"
    subprocess.run(["rpicam-still","-o",p,"--width",CAM_W,"--height",CAM_H,"-t","400","-n",
                    "--autofocus-mode","manual","--lens-position",LENS_POS],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    img = cv2.undistort(np.array(Image.open(p).convert("L")), K, D)
    res = det.detect(img, estimate_tag_pose=True, camera_params=[fx,fy,cx,cy], tag_size=6.0)
    dets = [{"tag_id":int(r.tag_id),"pose_R":np.asarray(r.pose_R),
             "pose_t":np.asarray(r.pose_t).ravel(),"decision_margin":float(r.decision_margin)} for r in res]
    return localize(dets, tag_map, ext)

def norm180(a):
    while a > 180: a -= 360
    while a < -180: a += 360
    return a

print(f"TARGET: x={target_x:.0f} y={target_y:.0f} cm (arrival radius {arrive_r:.0f} cm)")
try:
    for step in range(16):
        p = localize_now()
        if p is None:
            print("  I see no known tag -> STOP (the tag slipped out of view)"); break
        dx = target_x - p.x_cm
        dy = target_y - p.y_cm
        dist = math.hypot(dx, dy)
        desired_hdg = math.degrees(math.atan2(dy, dx))   # CCW from +X
        hdg_err = norm180(desired_hdg - p.heading_deg)
        print(f"  [{step}] pos x={p.x_cm:.0f} y={p.y_cm:.0f} hdg={p.heading_deg:.0f}  "
              f"dist={dist:.0f} target_dir={desired_hdg:.0f} error={hdg_err:.0f}")
        if dist < arrive_r:
            print("  >>> TARGET REACHED!"); break
        # if the target is at too large an angle (>FOV), the tag would slip out -> careful, only correct
        # sonar brake forward
        try: d = px.ultrasonic.read()
        except Exception: d = -1
        if 0 < d < 22:
            print(f"  obstacle {d:.0f} cm -> STOP"); break
        # pure-pursuit steering: from the heading error, a bit stronger than goto_y
        steer = max(-25, min(25, -0.7 * hdg_err))
        px.set_dir_servo_angle(steer)
        spd = 30
        dur = min(dist, 22) / 22.0      # max ~22 cm step
        px.forward(spd); time.sleep(dur); px.forward(0)
        time.sleep(0.35)
finally:
    px.forward(0); px.set_dir_servo_angle(0)
print("done")
