"""
test_nogo.py — OFFLINE verification of nogo.py (no robot, only math/stdlib).

Run:  python3 core/test_nogo.py   (from the picarx-mcp root)
Modelled on the localization test: every case is an assertion, with a summary at the end.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.nogo import (
    Zone, point_seg_dist, segs_intersect, seg_seg_dist,
    point_blocked, path_blocked, check_route, suggest_detour, load_zones,
)

_passed = 0
_failed = 0


def ok(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ✅ {label}")
    else:
        _failed += 1
        print(f"  ❌ {label}")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[1] Geometry primitives")
# point-segment distance
ok(abs(point_seg_dist(0, 5, -10, 0, 10, 0) - 5.0) < 1e-9, "point above the segment: dist=5")
ok(abs(point_seg_dist(20, 0, -10, 0, 10, 0) - 10.0) < 1e-9, "point beyond the segment: endpoint dist=10")
ok(abs(point_seg_dist(5, 0, -10, 0, 10, 0)) < 1e-9, "point on the segment: dist=0")
ok(abs(point_seg_dist(3, 4, 0, 0, 0, 0) - 5.0) < 1e-9, "degenerate segment (A==B): point distance")

# segment intersection
ok(segs_intersect(0, 0, 10, 10, 0, 10, 10, 0), "X intersection → True")
ok(not segs_intersect(0, 0, 1, 0, 0, 5, 1, 5), "parallel, non-intersecting → False")
ok(segs_intersect(0, 0, 10, 0, 5, 0, 5, 5), "T touch (endpoint on the segment) → True")
ok(not segs_intersect(0, 0, 4, 0, 5, 0, 9, 0), "collinear, NOT overlapping → False")
ok(segs_intersect(0, 0, 6, 0, 4, 0, 9, 0), "collinear, overlapping → True")

# segment-segment distance
ok(abs(seg_seg_dist(0, 0, 10, 0, 0, 3, 10, 3) - 3.0) < 1e-9, "parallel segments: dist=3")
ok(abs(seg_seg_dist(0, 0, 10, 10, 0, 10, 10, 0)) < 1e-9, "intersecting segments: dist=0")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[2] RECT zone — point containment with margin")
r = Zone(name="r", kind="rect", x_min=0, x_max=10, y_min=0, y_max=10)
ok(r.contains_point(5, 5, 0), "centre inside (margin 0)")
ok(not r.contains_point(15, 5, 0), "outside (margin 0)")
ok(r.contains_point(12, 5, 3), "margin 3 inflates it: x=12 inside")
ok(not r.contains_point(14, 5, 3), "margin 3: x=14 still outside")
ok(r.contains_point(0, 0, 0), "corner on the boundary → inside")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[3] RECT zone — segment crossing")
ok(r.hits_segment(-5, 5, 15, 5, 0), "pierces through the middle → True")
ok(not r.hits_segment(-5, 20, 15, 20, 0), "passes above → False")
ok(r.hits_segment(5, 5, 8, 8, 0), "segment entirely inside → True (endpoint inside)")
ok(not r.hits_segment(-5, -5, -1, -1, 0), "entirely outside → False")
ok(r.hits_segment(-5, 5, 5, 5, 0), "endpoint just inside → True")
ok(not r.hits_segment(-5, 5, -5, 50, 0), "vertical, does not even touch → False")
ok(r.hits_segment(-5, 13, 15, 13, 3.5), "margin 3.5: line y=13 crosses (inflated up to 13.5)")
ok(not r.hits_segment(-5, 13, 15, 13, 2.5), "margin 2.5: y=13 does NOT cross (inflated only to 12.5)")
ok(not r.hits_segment(-5, 13, 15, 13, 0), "margin 0: the same line does NOT cross")
ok(r.hits_segment(-5, 11, 15, 11, 0) is False, "y=11 margin 0: just above → False")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[4] CIRCLE zone")
c = Zone(name="c", kind="circle", cx=0, cy=0, r=5)
ok(c.contains_point(3, 0, 0), "inside the circle")
ok(not c.contains_point(6, 0, 0), "outside the circle")
ok(c.contains_point(6, 0, 2), "margin 2: r=5+2 → x=6 inside")
ok(c.hits_segment(-10, 0, 10, 0, 0), "segment through the diameter → True")
ok(not c.hits_segment(-10, 8, 10, 8, 0), "line y=8 (>r) → False")
ok(c.hits_segment(-10, 6, 10, 6, 2), "line y=6, margin 2 → True (5+2>6)")
ok(not c.hits_segment(-10, 6, 10, 6, 0.5), "line y=6, margin 0.5 → False")
ok(c.hits_segment(0, 10, 0, 6, 0) is False, "ends outside, does not reach → False")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[5] POLYGON zone (triangle)")
p = Zone(name="p", kind="polygon", points=[[0, 0], [10, 0], [5, 10]])
ok(p.contains_point(5, 3, 0), "inside the triangle")
ok(not p.contains_point(0, 9, 0), "outside, near a vertex")
ok(p.contains_point(5, 0, 0), "on the bottom edge → inside")
ok(p.hits_segment(-5, 3, 15, 3, 0), "horizontal piercing → True")
ok(not p.hits_segment(-5, 15, 15, 15, 0), "above → False")
ok(p.hits_segment(-3, 0, -0.5, 0, 1.0), "margin 1: reaches the (0,0) vertex from just outside → True")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[6] High-level API: point_blocked / path_blocked / severity")
zones = [
    # zones of the INVENTED example flat (see navigacio/OLVASS_EL.md)
    Zone(name="bathroom", kind="rect", x_min=50, x_max=500, y_min=-100, y_max=150,
         severity="hard", margin_cm=15),
    Zone(name="rug", kind="rect", x_min=-300, x_max=-200, y_min=300, y_max=400,
         severity="hard"),
    Zone(name="soft_zone", kind="circle", cx=-250, cy=0, r=20, severity="soft"),
]
ok(point_blocked(zones, 275, 25).name == "bathroom", "middle of the bathroom → blocked (bathroom)")
ok(point_blocked(zones, 0, 200) is None, "middle of the hallway → free")
# severity filter: the soft zone does NOT block by default
ok(point_blocked(zones, -250, 0) is None, "soft zone by default (hard only) → None")
ok(point_blocked(zones, -250, 0, severities=("hard", "soft")).name == "soft_zone",
   "soft zone explicitly requested → blocked")
# path: straight line from the hallway into the bathroom
ok(path_blocked(zones, 0, 25, 150, 25).name == "bathroom", "hallway→bathroom segment → bathroom")
ok(path_blocked(zones, -30, 200, -30, 450) is None, "along the hallway's west side → free")
# margin override: with a 15 cm margin the bathroom catches earlier (instead of the default 12)
ok(path_blocked([zones[0]], 0, 50, 37, 50).name == "bathroom",
   "bathroom 15cm margin: reaching x=37 (x_min50-15=35; default 12 would give 38) → blocked")
ok(path_blocked([zones[0]], 0, 50, 33, 50) is None,
   "line to x=33: does not reach 35 → free")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[7] check_route — full waypoint sequence")
# dock -> up the hallway -> living-room door -> living room, but the last segment cuts across the rug
wps_bad = [(0, 20), (0, 290), (-120, 290), (-250, 350)]
hits = check_route(zones, wps_bad)
ok(len(hits) >= 1 and any(h["zone"] == "rug" for h in hits),
   "route cutting across the rug → at least 1 collision (rug)")
# clear route along the hallway
wps_ok = [(0, 20), (0, 100), (0, 200)]
ok(check_route(zones, wps_ok) == [], "hallway route → empty collision list")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[8] suggest_detour — single-corner detour (corner-clip case)")
# Box x[0,40] y[0,40]; target ABOVE the box, start to the RIGHT → the direct line
# clips the top-right corner, a single corner waypoint solves it.
zbox = Zone(name="box", kind="rect", x_min=0, x_max=40, y_min=0, y_max=40)
p0 = (70, 20); p1 = (20, 70)
ok(path_blocked([zbox], *p0, *p1) is not None, "the direct line really clips the corner")
d = suggest_detour(zbox, *p0, *p1)
ok(d is not None, "gives a detour suggestion")
ok(point_blocked([zbox], d[0], d[1]) is None, "the suggested waypoint is not in the forbidden zone")
clear = (path_blocked([zbox], p0[0], p0[1], d[0], d[1]) is None and
         path_blocked([zbox], d[0], d[1], p1[0], p1[1]) is None)
ok(clear, "both segments of the detour are now clear (neither crosses)")
# Pass-through case: the target is straight across the box → one corner is not enough → None
ok(suggest_detour(zbox, -30, 20, 70, 20) is None,
   "line through the box: 1 corner does not solve it → None (caller uses 2 waypoints or asks a human)")


# ──────────────────────────────────────────────────────────────────────────────
print("\n[9] Loading the live zone file (nogo_zones.json), if present")
zp = Path(__file__).resolve().parent.parent / "nogo_zones.json"
if zp.exists():
    zlist = load_zones(str(zp))
    ok(len(zlist) >= 1, f"loaded {len(zlist)} zones from the JSON")
    names = {z.name for z in zlist}
    ok("bathroom_threshold" in names or "bathroom" in names, "contains the bathroom-threshold zone")
    smoke_ok = True
    for z in zlist:
        try:
            z.contains_point(0, 0, 12); z.hits_segment(0, 0, 100, 100, 12)
        except Exception as e:
            smoke_ok = False
            print("      error", z.name, ":", e)
    ok(smoke_ok, "contains_point + hits_segment run on every zone")
else:
    print("  (no nogo_zones.json - skipped)")


print("\n" + "=" * 50)
print("TOTAL:", _passed, "passed,", _failed, "failed")
print("=" * 50)
sys.exit(1 if _failed else 0)
