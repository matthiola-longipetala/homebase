"""autolabel_dump.py — stock YOLO boxes on EVERY dataset_v2 image (geometric proposals).

Only the box geometry is used (the COCO class name is renamed LATER according to the
ground truth). Output: dataset_v2/stock_detections.json:
  { "pos1/img_p+00_t12.jpg": {"wh":[640,480], "dets":[{"coco":"bed","conf":..,"box":[x1,y1,x2,y2]}, ...]}, ... }
"""
import os, glob, json, sys
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from ultralytics import YOLO

ROOT = os.path.expanduser("~/picarx-mcp/dataset_v2")
m = YOLO("yolo11n.pt"); names = m.names
out = {}
imgs = sorted(glob.glob(os.path.join(ROOT, "*", "*.jpg")))
for f in imgs:
    rel = os.path.relpath(f, ROOT).replace("\\", "/")
    r = m(f, imgsz=640, conf=0.25, verbose=False)[0]
    H, W = r.orig_shape
    b = r.boxes
    dets = []
    for (x1, y1, x2, y2), c, cf in zip(b.xyxy.tolist(), b.cls.tolist(), b.conf.tolist()):
        dets.append({"coco": names[int(c)], "conf": round(float(cf), 3),
                     "box": [round(x1), round(y1), round(x2), round(y2)]})
    out[rel] = {"wh": [W, H], "dets": dets}
with open(os.path.join(ROOT, "stock_detections.json"), "w") as fp:
    json.dump(out, fp, indent=0)
# short summary
from collections import Counter
cnt = Counter(d["coco"] for v in out.values() for d in v["dets"])
empty = sum(1 for v in out.values() if not v["dets"])
print(f"images: {len(out)}, empty: {empty}")
print("COCO class occurrences:", dict(cnt.most_common()))
