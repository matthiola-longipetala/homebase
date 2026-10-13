"""core/perception.py — telemetry core of the local vision subagent (PURE, model-independent).

The owner's idea (from a brainstorming session): run a fast, local vision process
on the Pi that recognizes objects from the camera stream and gives EYES to the
robot's REFLEX layer (the sonar only sees straight ahead, and is blind to rugs,
slanted glass, furniture legs, and the sides). Claude stays the strategist; this layer
is the fast, local "eye+hand".

This module DELIBERATELY imports neither ultralytics nor cv2 — it computes telemetry
only from the raw detection boxes (bbox px). That makes it UNIT-TESTABLE with numpy,
without a model (test_perception.py). The actual YOLO inference is done by
perception_loop.py on the Pi, which passes the boxes to this module.

Conventions (consistent with the manual):
- bearing_deg: horizontal direction measured from the image center. +bearing = RIGHT (= +pan),
  -bearing = LEFT. HFOV ~45.6° (the narrow lens from camera_calib).
- From a monocular camera we do NOT reliably estimate METRIC distance to an object of
  unknown size. Instead we use a PROXIMITY PROXY: area_frac (bbox area / frame) and
  bottom_frac (bottom of the box relative to the bottom of the image). Large + low + centered =
  a nearby obstacle ahead.
- danger_side values are "elol" (ahead), "jobb" (right), "bal" (left).
"""
from __future__ import annotations

HFOV_DEG = 45.6   # camera_calib.json: narrow lens, horizontal field of view

# --- Reflex danger thresholds — TO BE TUNED ON HARDWARE! (first reasonable guesses) ---
DANGER_AREA_FRAC = 0.12   # above this frame fraction the object is "large" (near)
DANGER_BOTTOM_FRAC = 0.70 # box bottom in the lowest 30% of the image = close on the floor/to the robot
DANGER_BEARING_DEG = 22.0 # within this it is "ahead" (in the direction of travel)

# Living beings: freeze&wait for these (don't push), no "large/near" condition needed
# if they are centered. (Automates the cat protocol.)
LIVING = {"person", "cat", "dog", "bird"}


def bearing_from_px(cx_px: float, frame_w: int, hfov_deg: float = HFOV_DEG) -> float:
    """Horizontal direction in degrees from the box center. +right, -left.

    Linear FOV approximation: ±hfov/2 at the image edge. This is accurate enough for
    navigation guidance, and does not tie the resolution to fx/cx (the calibration was
    made at a different resolution than the one inference runs at)."""
    if frame_w <= 0:
        return 0.0
    return (cx_px - frame_w / 2.0) / frame_w * hfov_deg


def detection_to_telemetry(box, frame_w: int, frame_h: int) -> dict:
    """One raw detection -> telemetry dict.

    box: dict {label:str, conf:float, x1,y1,x2,y2 (px)}.
    Returns: label, conf, bearing_deg, area_frac, bottom_frac, center_x_frac,
             living(bool), danger(bool).
    """
    x1, y1, x2, y2 = box["x1"], box["y1"], box["x2"], box["y2"]
    w = max(0.0, x2 - x1)
    h = max(0.0, y2 - y1)
    cx = (x1 + x2) / 2.0
    frame_area = float(frame_w * frame_h) if frame_w and frame_h else 1.0
    area_frac = (w * h) / frame_area if frame_area > 0 else 0.0
    bottom_frac = (y2 / frame_h) if frame_h else 0.0
    bearing = bearing_from_px(cx, frame_w)
    label = box.get("label", "?")
    living = label in LIVING

    ahead = abs(bearing) <= DANGER_BEARING_DEG
    big_low = area_frac >= DANGER_AREA_FRAC and bottom_frac >= DANGER_BOTTOM_FRAC
    # Danger = a large, NEARBY OBSTACLE ahead. We do NOT stop for living beings (person/cat).
    # The 'living' label STAYS in the telemetry (awareness), but does not trigger a stop.
    danger = ahead and big_low

    return {
        "label": label,
        "conf": round(float(box.get("conf", 0.0)), 3),
        "bearing_deg": round(bearing, 1),
        "area_frac": round(area_frac, 4),
        "bottom_frac": round(bottom_frac, 3),
        "center_x_frac": round(cx / frame_w, 3) if frame_w else 0.0,
        "living": living,
        "danger": danger,
    }


def build_telemetry(boxes, frame_w: int, frame_h: int, ts: float = 0.0) -> dict:
    """List of detections -> full telemetry dict (this is what /tmp/perception.json holds).

    'stop' is the aggregated signal for the reflex layer: true if ANY detection is
    dangerous. 'detections' is the compact text matrix for the strategist (Claude).
    The danger presumed closest (largest area_frac) is 'closest_danger'.
    """
    dets = [detection_to_telemetry(b, frame_w, frame_h) for b in boxes]
    dangers = [d for d in dets if d["danger"]]
    closest = max(dangers, key=lambda d: d["area_frac"]) if dangers else None
    # guidance for the strategist: which side the danger is on
    side = None
    if closest:
        side = "elol" if abs(closest["bearing_deg"]) <= DANGER_BEARING_DEG else (
            "jobb" if closest["bearing_deg"] > 0 else "bal")
    return {
        "ts": round(ts, 2),
        "frame": [frame_w, frame_h],
        "n": len(dets),
        "stop": bool(dangers),
        "closest_danger": closest,
        "danger_side": side,
        "detections": sorted(dets, key=lambda d: -d["area_frac"]),
    }
