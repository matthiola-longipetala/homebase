"""look_home.py (venv) — the HOME model (lakas_v3) names what is in the image.

Run: .venv-perception/bin/python look_home.py [image_path]   (default: /tmp/view.jpg)
Output: one 'name conf side (bearing)' per line, where side is elol=ahead / jobbra=right /
balra=left. Called at the decision points of live navigation,
and spoken aloud on the VOICE, so the people nearby hear it too.
"""
import os
import sys
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from ultralytics import YOLO
from core.perception import bearing_from_px

img = sys.argv[1] if len(sys.argv) > 1 else "/tmp/view.jpg"
m = YOLO(os.path.expanduser("~/picarx-mcp/runs_otthon/lakas_v3/weights/best.pt"))
r = m(img, imgsz=512, conf=0.35, verbose=False)[0]
H, W = r.orig_shape
dets = []
for (x1, y1, x2, y2), c, cf in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
    cx = (x1 + x2) / 2
    bg = bearing_from_px(cx, W)
    side = "elol" if abs(bg) <= 8 else ("jobbra" if bg > 0 else "balra")
    dets.append((m.names[int(c)], float(cf), side, round(bg)))
dets.sort(key=lambda d: -d[1])
for name, cf, side, bg in dets:
    print(f"{name} {cf:.2f} {side} ({bg:+d})")
if not dets:
    print("(nothing familiar)")
