"""test_perception.py — LOCAL unit test of the perception telemetry core (no model).

Run (on Windows or the Pi): python test_perception.py
It does NOT test the actual YOLO inference (that is perception_loop.py's + the Pi's job),
only the geometry/telemetry/reflex logic, which is the most failure-prone part.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.perception import (
    bearing_from_px, detection_to_telemetry, build_telemetry,
    HFOV_DEG, DANGER_AREA_FRAC, DANGER_BOTTOM_FRAC,
)

W, H = 640, 480
ok = 0
fail = 0

def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  OK   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")

print("== bearing_from_px ==")
check("center -> 0 deg", abs(bearing_from_px(W/2, W)) < 1e-6)
check("right edge -> +HFOV/2", abs(bearing_from_px(W, W) - HFOV_DEG/2) < 1e-6)
check("left edge -> -HFOV/2", abs(bearing_from_px(0, W) + HFOV_DEG/2) < 1e-6)
check("shifted right -> positive (right)", bearing_from_px(W*0.75, W) > 0)
check("shifted left -> negative (left)", bearing_from_px(W*0.25, W) < 0)

print("== detection_to_telemetry ==")
# Large, low, centered couch -> danger (not a living being, but large+low+ahead)
couch = {"label": "couch", "conf": 0.9, "x1": 220, "y1": 240, "x2": 420, "y2": 470}
t = detection_to_telemetry(couch, W, H)
check("centered box bearing ~0", abs(t["bearing_deg"]) < 3)
check("large box area_frac > threshold", t["area_frac"] >= DANGER_AREA_FRAC)
check("low box bottom_frac > threshold", t["bottom_frac"] >= DANGER_BOTTOM_FRAC)
check("large+low+ahead couch -> danger", t["danger"] is True)
check("couch is not a living being", t["living"] is False)

# Distant small chair at the edge -> NOT a danger (small, not ahead)
chair = {"label": "chair", "conf": 0.6, "x1": 560, "y1": 120, "x2": 600, "y2": 180}
t2 = detection_to_telemetry(chair, W, H)
check("small distant chair -> not danger", t2["danger"] is False)
check("chair at the edge has positive bearing (right)", t2["bearing_deg"] > 0)

# Cat in the center but small/distant -> living-being flag kept; no stop (living beings never trigger danger)
cat_far = {"label": "cat", "conf": 0.8, "x1": 300, "y1": 200, "x2": 340, "y2": 250}
t3 = detection_to_telemetry(cat_far, W, H)
check("cat ahead -> living flag is kept", t3["living"] is True)
check("we do NOT stop for living beings (owner's rule) -> danger False", t3["danger"] is False)

# IMPORTANT DESIGN CONSEQUENCE: the HFOV is only ~45.6°, so the whole frame spans only
# ±22.8°. The 22° "ahead" threshold therefore covers almost the ENTIRE image ->
# if a living being is SEEN at all in the (narrow) image, it is essentially in front of me ->
# freeze&wait. This is the SAFE behaviour (don't drive into a cat). The "pass by it on the
# side" case is NOT decided within one frame, but with a pan sweep (the eye refines it).
cat_edge = {"label": "cat", "conf": 0.8, "x1": 30, "y1": 200, "x2": 70, "y2": 250}
t4 = detection_to_telemetry(cat_edge, W, H)
check("living being is never danger (we don't stop for it)", t4["danger"] is False)
check("cat at the image edge still has bearing within ±22.8", abs(t4["bearing_deg"]) < HFOV_DEG/2)

print("== build_telemetry ==")
tel = build_telemetry([couch, chair, cat_far, cat_edge], W, H, ts=123.4)
check("stop is true if there is a danger", tel["stop"] is True)
check("n = 4 detections", tel["n"] == 4)
check("closest_danger is the largest area (couch)", tel["closest_danger"]["label"] == "couch")
check("danger_side 'elol' (ahead) for the centered couch", tel["danger_side"] == "elol")
check("detections sorted by area, descending",
      all(tel["detections"][i]["area_frac"] >= tel["detections"][i+1]["area_frac"]
          for i in range(len(tel["detections"]) - 1)))

# Empty case -> no stop
empty = build_telemetry([], W, H, ts=1.0)
check("empty list -> stop false", empty["stop"] is False)
check("empty list -> closest_danger None", empty["closest_danger"] is None)

print(f"\n=== {ok} OK, {fail} FAIL ===")
sys.exit(0 if fail == 0 else 1)
