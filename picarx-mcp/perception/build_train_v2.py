"""build_train_v2.py — home-specific fine-tune (from hand-drawn labels).

The agent looked at the images and drew the boxes (based on the owner's ground truth).
Here: train/val build -> data.yaml -> yolo11n fine-tune (freeze=10) -> test.

⚠️ Few examples per class (1-7) -> this is a "round 2" proof: wider vocabulary + real names,
but confidence will still be modest; the main path is MORE labeled images.

The class names are the model's label strings (nav_to.py / look_home.py match on them).
LABELS below shows the format with a few example images from a test flat: replace it with
your own images and boxes (one sub-folder per robot position).
"""
import os, glob, shutil, sys
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from ultralytics import YOLO

ROOT = os.path.expanduser("~/picarx-mcp/dataset_v2")

CLASSES = ["bed", "nightstand", "wardrobe", "desk", "chair", "laundry_basket",
           "sofa", "door", "kitchen_cabinet", "oven", "fridge", "air_conditioner",
           "cat_tree", "patio_door", "coffee_table", "dining_table", "rug", "wall_picture",
           "person"]

# image relpath -> [(class_id, cx, cy, w, h) normalized]
LABELS = {
 "pos1/img_p+00_t12.jpg": [(0,0.371,0.344,0.742,0.646),(1,0.863,0.417,0.273,0.833)],
 "pos1/img_p+40_t12.jpg": [(1,0.379,0.427,0.586,0.854)],
 "pos3/img_p+00_t12.jpg": [(5,0.75,0.448,0.5,0.896),(2,0.273,0.438,0.547,0.875)],
 "pos3/img_p-40_t12.jpg": [(2,0.547,0.5,0.906,0.99)],
 "pos2/img_p+00_t12.jpg": [(3,0.590,0.406,0.523,0.813),(4,0.164,0.438,0.328,0.875),(18,0.359,0.625,0.25,0.458)],
 "pos4/img_p+00_t12.jpg": [(14,0.543,0.563,0.617,0.5),(6,0.602,0.531,0.797,0.438)],
 "pos4/img_p-60_t12.jpg": [(9,0.293,0.474,0.320,0.760),(8,0.652,0.531,0.398,0.604),(8,0.066,0.563,0.133,0.625)],
 "pos5/img_p+20_t12.jpg": [(12,0.270,0.453,0.305,0.719),(13,0.313,0.292,0.625,0.583),(15,0.695,0.490,0.609,0.521),(4,0.488,0.510,0.180,0.479)],
 "pos6/img_p+00_t12.jpg": [(9,0.469,0.505,0.234,0.552),(8,0.176,0.536,0.352,0.531),(8,0.742,0.516,0.313,0.573)],
 "pos6/img_p-20_t12.jpg": [(10,0.219,0.438,0.219,0.750),(8,0.492,0.557,0.328,0.490),(8,0.516,0.125,0.313,0.25),(9,0.805,0.5,0.297,0.542)],
 "pos7/img_p-20_t12.jpg": [(11,0.305,0.125,0.172,0.083),(17,0.320,0.25,0.0625,0.104),(8,0.566,0.620,0.211,0.302),(8,0.563,0.198,0.203,0.271),(15,0.141,0.542,0.281,0.542),(16,0.258,0.917,0.516,0.167),(7,0.047,0.208,0.094,0.417)],
}

VAL = {"pos6/img_p+00_t12.jpg", "pos3/img_p-40_t12.jpg"}

print("=== TRAIN/VAL BUILD ===")
for sub in ("train/images","train/labels","val/images","val/labels"):
    p = os.path.join(ROOT, sub); shutil.rmtree(p, ignore_errors=True); os.makedirs(p, exist_ok=True)
ntr=nva=0
for rel, boxes in LABELS.items():
    split = "val" if rel in VAL else "train"
    flat = rel.replace("/", "__")
    src = os.path.join(ROOT, rel)
    shutil.copy(src, os.path.join(ROOT, split, "images", flat))
    with open(os.path.join(ROOT, split, "labels", flat.replace(".jpg",".txt")), "w") as f:
        f.write("\n".join(f"{c} {cx} {cy} {w} {h}" for (c,cx,cy,w,h) in boxes))
    if split=="val": nva+=1
    else: ntr+=1
print(f"  train={ntr} val={nva}")

yaml_path = os.path.join(ROOT, "data_v2.yaml")
with open(yaml_path,"w") as y:
    y.write(f"path: {ROOT}\ntrain: train/images\nval: val/images\nnames:\n")
    for i,n in enumerate(CLASSES): y.write(f"  {i}: {n}\n")
print("  data:", yaml_path)

print("=== FINE-TUNE (freeze=10, CPU) ===")
m = YOLO("yolo11n.pt")
m.train(data=yaml_path, epochs=80, imgsz=640, freeze=10, batch=4, device="cpu",
        workers=2, project=os.path.expanduser("~/picarx-mcp/runs_otthon"), name="lakas_v2",
        exist_ok=True, verbose=False, plots=False)

print("=== TEST on the val images ===")
best = os.path.expanduser("~/picarx-mcp/runs_otthon/lakas_v2/weights/best.pt")
t = YOLO(best)
for f in sorted(glob.glob(os.path.join(ROOT,"val/images/*.jpg"))):
    r = t(f, imgsz=640, conf=0.10, verbose=False)[0]; b=r.boxes
    out=[f"{t.names[int(c)]}:{cf:.2f}" for c,cf in zip(b.cls.tolist(),b.conf.tolist())]
    print(f"  {os.path.basename(f)} -> "+(", ".join(out) if out else "(nothing)"))
print(f"DONE. Weights: {best}")
