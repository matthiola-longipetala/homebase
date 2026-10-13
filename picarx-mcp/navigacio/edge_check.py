"""edge_check.py — compare tag distance at the image edge vs. the center (playbook 8.2).

A test of the pinhole+undistort pipeline: the same tag is measured from several pan
positions, so it appears in different parts of the image (center -> edge). If the
undistort is good, the distance estimate stays constant; if it drifts at the edge, there
is still residual distortion there (-> only trust a fix rotated to the center).

Wheels do NOT move — only the pan servo. Run: python3 navigacio/edge_check.py
"""
import os
import json
import time

import cv2
import numpy as np
from PIL import Image
from pupil_apriltags import Detector
from robot_hat import Servo

import sys
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.capture import capture
from core.tag_range import tag_range_cm, tag_range_angular_cm, tag_pixel_size

calib = json.load(open(os.path.expanduser("~/picarx-mcp/camera_calib.json")))
K = np.array(calib["camera_matrix"])
D = np.array(calib["dist_coeffs"])
fx, fy = calib["fx"], calib["fy"]
cx, cy = calib["cx"], calib["cy"]
W = calib["image_size"][0]
coords = json.load(open(os.path.expanduser("~/picarx-mcp/tags_coords.json")))["tags"]

det = Detector(families="tag36h11", nthreads=4)
pan = Servo("P0")
# one look from the current position, then edge to edge in ONE direction.
# A zigzag (0,-20,20,-40...) wears the servo and yanks the ribbon cable.
PANS = [0, -60, -40, -20, 20, 40, 60]

seen = {}   # tag_id -> [(off_px, range_cm, px_size, pan)]
E = None
for p in PANS:
    pan.angle(p)
    time.sleep(0.7)
    E = capture("/tmp/edge.jpg", shutter_us=E)
    img = cv2.undistort(np.array(Image.open("/tmp/edge.jpg").convert("L")), K, D)
    for r in det.detect(img):
        tid = str(r.tag_id)
        if tid not in coords:
            continue
        size = coords[tid].get("size_cm", 6.0)
        rng = tag_range_cm(r.corners, size, fx)
        rng_a = tag_range_angular_cm(r.corners, size, fx, fy, cx, cy)
        off = float(r.center[0]) - cx
        seen.setdefault(r.tag_id, []).append(
            (round(off), round(rng, 1), round(rng_a, 1),
             round(tag_pixel_size(r.corners), 1), p))
pan.angle(0)

print(f"image width={W}, cx={cx:.0f} (|off| ~{W//2 - 100}+ = EDGE)")
print("PX = old pixel-based distance | ANG = new angle-based distance (the good method should have a small spread!)")
for tid, rows in sorted(seen.items()):
    if len(rows) < 2:
        continue
    rows.sort(key=lambda t: abs(t[0]))
    spr_px = max(r[1] for r in rows) - min(r[1] for r in rows)
    spr_a = max(r[2] for r in rows) - min(r[2] for r in rows)
    print(f"tag{tid}: PX spread={spr_px:.0f}cm | ANG spread={spr_a:.0f}cm")
    for o, d, da, s, p in rows:
        print(f"    off{o:+5d}px  PX={d:6.1f}cm  ANG={da:6.1f}cm  ({s}px, pan{p:+d})")
