"""detect_on_frames.py — YOLO11n detection on existing images + telemetry (venv Python).

Runs detection on the /tmp/ptest_*.jpg images collected by capture_perception_test.py,
prints the telemetry (label/conf/bearing/danger), and saves an annotated image.
Does not touch the camera (only reads files) → no movement.

Run (from the venv): .venv-perception/bin/python detect_on_frames.py [imgsz]
"""
import sys, os, glob, json, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.perception import build_telemetry
from ultralytics import YOLO
import cv2

imgsz = int(sys.argv[1]) if len(sys.argv) > 1 else 256
m = YOLO(os.path.expanduser("~/picarx-mcp/yolo11n.pt"))
names = m.names

frames = sorted(glob.glob("/tmp/ptest_*.jpg"))
if not frames:
    print("NO /tmp/ptest_*.jpg — run capture_perception_test.py first.")
    sys.exit(1)

for p in frames:
    if "_annot" in p:
        continue
    img = cv2.imread(p)
    if img is None:
        print(f"[error] not readable: {p}")
        continue
    h, w = img.shape[:2]
    res = m(img, imgsz=imgsz, conf=0.35, verbose=False)[0]
    boxes = []
    b = res.boxes
    for (x1, y1, x2, y2), c, cf in zip(b.xyxy.tolist(), b.cls.tolist(), b.conf.tolist()):
        boxes.append({"label": names[int(c)], "conf": float(cf),
                      "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    tel = build_telemetry(boxes, w, h, ts=time.time())
    print(f"\n== {os.path.basename(p)} ({w}x{h}, imgsz={imgsz}) ==")
    if tel["detections"]:
        for d in tel["detections"]:
            flag = " [DANGER]" if d["danger"] else ""
            print(f"   {d['label']:12s} conf={d['conf']:.2f}  bearing={d['bearing_deg']:+5.0f}deg  "
                  f"area={d['area_frac']:.3f}{flag}")
    else:
        print("   (no detections)")
    print(f"   -> stop={tel['stop']}  danger_side={tel['danger_side']}")
    out = p.replace(".jpg", "_annot.jpg")
    cv2.imwrite(out, res.plot())
print("\nDONE. Annotated images: /tmp/ptest_*_annot.jpg")
