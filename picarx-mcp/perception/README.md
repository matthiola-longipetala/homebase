# perception/ — the local vision sub-agent (YOLO), migrated to the laptop

The robot's "eye": a YOLO11n fine-tuned on our own floor-level images, which recognises the home's furniture
BY NAME. Moved here from the Pi (faster training on the laptop + it takes no space on the Pi). (The detailed
planning notes are not part of the public repository.)

## Contents
- `models/runs_otthon/lakas_v3/weights/best.pt` — **the trained HOME model** (`otthon` = "home", `lakas` =
  "flat"; 22 classes, val mAP50 0.84). This is the main result.
- `models/yolo11n.pt` — stock base model; `models/yolo11n_ncnn_model/` — NCNN export (not used for now because
  it crashed on the Pi).
- `dataset_v2/` — 112 labelled images (8 positions) + montages + `CIMKE_JEGYZET.md` ("label notes"; the
  owner's ground-truth notes) + `v3b_detekciok/` ("v3b detections"; annotated evidence).
- `perception_core.py` — the telemetry core (bearing, proximity, reflex danger; formerly `core/perception.py`).
- `build_train_v3.py` — the FULL training (the manual + sub-agent labels are embedded in the RAW dict).
  `build_train_v2.py`, `build_and_train_poc.py` — earlier rounds.
- `nav_to.py`, `look_home.py`, `detect_on_frames.py`, `capture_dataset.py`, `capture_perception_test.py`,
  `autolabel_dump.py`, `perception_loop.py`, `test_perception.py` — helper / live scripts.

## ⚠️ Laptop setup TODO (if you train/run here)
1. New venv (x86!): `python -m venv .venv` → `pip install ultralytics` (CPU torch, for x86). The Pi venv was
   not carried over (aarch64).
2. Path fix: the scripts use `~/picarx-mcp` (the robot user's home) — point them to the local `perception/`
   folder.
3. Import fix: wherever there is `from core.perception import ...` → `from perception_core import ...`.
4. Training is much faster on the laptop (no GPU, but a strong CPU).

## THE KEY LESSON (from a live test) — the next direction
Perception (WHAT is there) is NOT enough for navigation ON ITS OWN: the robot does not know WHERE it is.
Pure vision-based homing is flaky (blind body turns are unreliable). **The right architecture:**
> **AprilTag localisation + grid map = WHERE (metric pose + route)  ⊕  this model = WHAT (names + sonar blind spot).**

Perception has to be wired into the EXISTING tag navigation as a semantic + obstacle layer, NOT as a
stand-alone navigator. Next: an exact tag map (precise x, y, yaw) + a grid map (JSON/array, NOT an image) +
the fusion of the two.
