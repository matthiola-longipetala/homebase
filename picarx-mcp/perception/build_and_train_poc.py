"""build_and_train_poc.py — bedroom fine-tuning POC (standalone).

Goal: PROVE the pipeline — capture -> (auto)label + REMAP to our own
class names -> fine-tune -> the new model outputs OUR labels.

Steps:
1) Use the stock YOLO @640 boxes as geometric proposals and REMAP the COCO name
   to our class (bench/bed->agy [bed], oven/refrigerator->ejjeliszekreny [nightstand]).
2) Write the images + YOLO labels into train/val folders.
3) Fine-tune yolo11n.pt with a FROZEN backbone (freeze=10), CPU.
4) Test on the val images: does it output OUR names?

⚠️ POC: few images (~9), from ONE position -> proves the plumbing + the remapping,
not a generalizable model. The proper multi-position set comes with the supervised round.
"""
import os, glob, shutil, sys
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from ultralytics import YOLO

ROOT = os.path.expanduser("~/picarx-mcp/dataset_haloszoba")
IMG_SRC = os.path.join(ROOT, "images")
# COCO name -> our class id
REMAP = {"bench": 0, "bed": 0, "couch": 0, "chair": 0,
         "oven": 1, "refrigerator": 1, "microwave": 1}
CLASSES = {0: "agy", 1: "ejjeliszekreny"}

def yolo_line(cls, x1, y1, x2, y2, W, H):
    cx = (x1 + x2) / 2 / W; cy = (y1 + y2) / 2 / H
    w = (x2 - x1) / W; h = (y2 - y1) / H
    return f"{cls} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"

print("=== 1) AUTO-LABEL + REMAP ===")
det = YOLO("yolo11n.pt"); names = det.names
labeled = {}   # img_path -> [label_lines]
for f in sorted(glob.glob(os.path.join(IMG_SRC, "*.jpg"))):
    r = det(f, imgsz=640, conf=0.30, verbose=False)[0]
    b = r.boxes
    H, W = r.orig_shape
    lines, kinds = [], set()
    for (x1, y1, x2, y2), c, cf in zip(b.xyxy.tolist(), b.cls.tolist(), b.conf.tolist()):
        nm = names[int(c)]
        if nm in REMAP:
            cls = REMAP[nm]
            lines.append(yolo_line(cls, x1, y1, x2, y2, W, H))
            kinds.add(CLASSES[cls])
    if lines:
        labeled[f] = lines
        print(f"  {os.path.basename(f)} -> {sorted(kinds)}")
print(f"  labeled images: {len(labeled)}")

# train/val split: 2 of the bed-only images go to val, the rest to train
print("=== 2) TRAIN/VAL BUILD ===")
for sub in ("train/images", "train/labels", "val/images", "val/labels"):
    p = os.path.join(ROOT, sub)
    shutil.rmtree(p, ignore_errors=True); os.makedirs(p, exist_ok=True)

agy_only = [f for f, ls in labeled.items() if all(l[0] == "0" for l in ls)]
val_set = set(sorted(agy_only)[:2])
n_tr = n_va = 0
for f, lines in labeled.items():
    split = "val" if f in val_set else "train"
    base = os.path.splitext(os.path.basename(f))[0]
    shutil.copy(f, os.path.join(ROOT, split, "images", base + ".jpg"))
    with open(os.path.join(ROOT, split, "labels", base + ".txt"), "w") as out:
        out.write("\n".join(lines))
    if split == "val": n_va += 1
    else: n_tr += 1
print(f"  train={n_tr}  val={n_va}")

print("=== 3) data.yaml ===")
yaml_path = os.path.join(ROOT, "data.yaml")
with open(yaml_path, "w") as y:
    y.write(f"path: {ROOT}\ntrain: train/images\nval: val/images\n")
    y.write("names:\n  0: agy\n  1: ejjeliszekreny\n")
print("  ", yaml_path)

print("=== 4) FINE-TUNE (freeze=10 backbone, CPU) ===")
m = YOLO("yolo11n.pt")
m.train(data=yaml_path, epochs=80, imgsz=640, freeze=10, batch=4,
        device="cpu", workers=2, project=os.path.expanduser("~/picarx-mcp/runs_otthon"),
        name="halo_poc", exist_ok=True, verbose=False, plots=False)

print("=== 5) TEST on the val images with the NEW model ===")
best = os.path.expanduser("~/picarx-mcp/runs_otthon/halo_poc/weights/best.pt")
tuned = YOLO(best)
for f in sorted(glob.glob(os.path.join(ROOT, "val/images/*.jpg"))):
    r = tuned(f, imgsz=640, conf=0.30, verbose=False)[0]
    b = r.boxes
    out = [f"{tuned.names[int(c)]}:{cf:.2f}" for c, cf in zip(b.cls.tolist(), b.conf.tolist())]
    print(f"  {os.path.basename(f)} -> {', '.join(out) if out else '(nothing)'}")
print(f"\nDONE. New weights: {best}")
