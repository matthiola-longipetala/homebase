"""
nogo.py — forbidden areas ("no-go zones") for navigation.

WHY IT IS NEEDED (lesson from a rug incident): for obstacle avoidance the `goto_*` scripts
rely only on the SONAR (`core/motion.forward_guarded`). The sonar is a FORWARD-facing
sensor that sees VERTICAL obstacles — it physically CANNOT see:
  • floor-level hazards (a rug that the grayscale/line sensors get stuck on),
  • steps DOWNWARD (e.g. a bathroom floor a couple of cm lower → the robot drives down,
    but the rear drive cannot bring it back up → a trap),
  • edges / drop hazards (balcony),
  • driving UNDER furniture (bed legs — the sonar sees the gap underneath as free).

These can only be avoided based on MAP + POSITION (avoiding e.g. a bathroom must be
MAP+POSITION-based, not tag-based). This module provides that
map-based layer ALONGSIDE the sonar brake — not instead of it.

SCOPE (important design principle):
  A no-go zone is ONLY for SONAR-BLIND hazards (floor drop, rug, edge,
  under-furniture). VERTICAL furniture (sofa, cabinet, table leg) does NOT
  go here — the sonar brake handles that live. This keeps the zone list minimal and
  accurate, and it does not duplicate the sonar.

DELIBERATELY pure geometry: only `math` + `json` + `dataclasses`. It does NOT import
numpy/cv2 — runs in a sandbox AND on the Pi, fully testable without the robot
(see `core/test_nogo.py`).

──────────────────────────────────────────────────────────────────────────────
COORDINATE FRAME  (same as the world frame of localization.py and the home map)
──────────────────────────────────────────────────────────────────────────────
  All lengths are in CENTIMETRES. World frame: +X right, +Y forward (hallway), origin
  at the dock / start point (example flat: navigacio/OLVASS_EL.md). The robot's 2D pose
  (x, y) comes from `localize()`.

ZONE TYPES
  • "rect"   : axis-aligned rectangle  {x_min, x_max, y_min, y_max}
  • "circle" : circle  {cx, cy, r}
  • "polygon": arbitrary polygon  {points: [[x,y], ...]}  (≥3 points)

MARGIN (margin_cm)
  Every test works on a zone INFLATED by `margin` cm (robot size + safety
  allowance). The global default is `DEFAULT_MARGIN_CM`, but it can be overridden
  per zone with the JSON `margin_cm` field (e.g. the bathroom drop needs a LARGER margin,
  because the robot's NOSE must not cross it — and the sonar sits ~8 cm behind the nose).
  The margin value should be tuned in live runs.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Half the robot's width + allowance. Tune in live runs (PiCar-X is ~16.5 cm wide
# → half ~8.5 cm; with the mic holder it is taller, not wider). 12 cm is a cautious default.
DEFAULT_MARGIN_CM = 12.0


# ──────────────────────────────────────────────────────────────────────────────
#  Geometry helpers (point–segment, segment–segment)
# ──────────────────────────────────────────────────────────────────────────────
def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def point_seg_dist(px: float, py: float, ax: float, ay: float,
                   bx: float, by: float) -> float:
    """Distance of point (px,py) from segment A–B (not the infinite line)."""
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:                       # A==B, degenerate segment → point distance
        return math.hypot(px - ax, py - ay)
    t = _clamp(((px - ax) * dx + (py - ay) * dy) / seg2, 0.0, 1.0)
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


def _orient(ax, ay, bx, by, cx, cy) -> float:
    """Signed area ×2 of triangle A–B–C (orientation)."""
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_seg(ax, ay, bx, by, cx, cy) -> bool:
    """Whether C lies on segment A–B (assuming collinearity)."""
    return min(ax, bx) <= cx <= max(ax, bx) and min(ay, by) <= cy <= max(ay, by)


def segs_intersect(ax, ay, bx, by, cx, cy, dx, dy) -> bool:
    """Whether segment A–B intersects segment C–D (touching/collinear overlap included)."""
    d1 = _orient(cx, cy, dx, dy, ax, ay)
    d2 = _orient(cx, cy, dx, dy, bx, by)
    d3 = _orient(ax, ay, bx, by, cx, cy)
    d4 = _orient(ax, ay, bx, by, dx, dy)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        # strict intersection (each segment splits the other onto opposite sides)
        if d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0:
            return True
    # collinear / touching cases
    if d1 == 0 and _on_seg(cx, cy, dx, dy, ax, ay): return True
    if d2 == 0 and _on_seg(cx, cy, dx, dy, bx, by): return True
    if d3 == 0 and _on_seg(ax, ay, bx, by, cx, cy): return True
    if d4 == 0 and _on_seg(ax, ay, bx, by, dx, dy): return True
    return False


def seg_seg_dist(ax, ay, bx, by, cx, cy, dx, dy) -> float:
    """Minimum distance between two segments (0 if they intersect)."""
    if segs_intersect(ax, ay, bx, by, cx, cy, dx, dy):
        return 0.0
    return min(
        point_seg_dist(ax, ay, cx, cy, dx, dy),
        point_seg_dist(bx, by, cx, cy, dx, dy),
        point_seg_dist(cx, cy, ax, ay, bx, by),
        point_seg_dist(dx, dy, ax, ay, bx, by),
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Zone data structure
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class Zone:
    name: str
    kind: str                       # "rect" | "circle" | "polygon"
    # rect
    x_min: float = 0.0
    x_max: float = 0.0
    y_min: float = 0.0
    y_max: float = 0.0
    # circle
    cx: float = 0.0
    cy: float = 0.0
    r: float = 0.0
    # polygon
    points: list = field(default_factory=list)   # [[x,y], ...]
    # meta
    margin_cm: Optional[float] = None             # None → global default
    severity: str = "hard"          # "hard" (never) | "soft" (avoid)
    note: str = ""
    confidence: str = "measured"    # "measured" | "estimated" | "placeholder"

    # ── point inside the zone? (inflated by margin) ──────────────────────────
    def contains_point(self, x: float, y: float, margin: float) -> bool:
        if self.kind == "rect":
            return (self.x_min - margin <= x <= self.x_max + margin and
                    self.y_min - margin <= y <= self.y_max + margin)
        if self.kind == "circle":
            return math.hypot(x - self.cx, y - self.cy) <= self.r + margin
        if self.kind == "polygon":
            if _point_in_poly(x, y, self.points):
                return True
            # margin: within margin of any boundary edge → inside
            n = len(self.points)
            for i in range(n):
                ax, ay = self.points[i]
                bx, by = self.points[(i + 1) % n]
                if point_seg_dist(x, y, ax, ay, bx, by) <= margin:
                    return True
            return False
        raise ValueError(f"unknown zone type: {self.kind}")

    # ── does segment (x0,y0)->(x1,y1) cross the zone? ────────────────────────
    def hits_segment(self, x0, y0, x1, y1, margin: float) -> bool:
        # 1) if either endpoint is inside the (inflated) zone → hit
        if self.contains_point(x0, y0, margin) or self.contains_point(x1, y1, margin):
            return True
        if self.kind == "rect":
            # the 4 edges of the inflated rectangle vs. the segment; the "endpoint inside"
            # case was handled above, so edge intersection (passing through) is enough here.
            xa, xb = self.x_min - margin, self.x_max + margin
            ya, yb = self.y_min - margin, self.y_max + margin
            edges = [(xa, ya, xb, ya), (xb, ya, xb, yb),
                     (xb, yb, xa, yb), (xa, yb, xa, ya)]
            return any(segs_intersect(x0, y0, x1, y1, *e) for e in edges)
        if self.kind == "circle":
            return point_seg_dist(self.cx, self.cy, x0, y0, x1, y1) <= self.r + margin
        if self.kind == "polygon":
            n = len(self.points)
            for i in range(n):
                ax, ay = self.points[i]
                bx, by = self.points[(i + 1) % n]
                if seg_seg_dist(x0, y0, x1, y1, ax, ay, bx, by) <= margin:
                    return True
            return False
        raise ValueError(f"unknown zone type: {self.kind}")

    # ── zone corners (rect/poly) with margin — candidates for detour waypoints ──
    def inflated_corners(self, margin: float) -> list:
        if self.kind == "rect":
            xa, xb = self.x_min - margin, self.x_max + margin
            ya, yb = self.y_min - margin, self.y_max + margin
            return [(xa, ya), (xb, ya), (xb, yb), (xa, yb)]
        if self.kind == "polygon":
            # approximation: vertices pushed outward from the centroid
            cx = sum(p[0] for p in self.points) / len(self.points)
            cy = sum(p[1] for p in self.points) / len(self.points)
            out = []
            for x, y in self.points:
                d = math.hypot(x - cx, y - cy) or 1.0
                out.append((x + (x - cx) / d * margin, y + (y - cy) / d * margin))
            return out
        if self.kind == "circle":
            # 8 points around the inflated circle
            rr = self.r + margin
            return [(self.cx + rr * math.cos(a), self.cy + rr * math.sin(a))
                    for a in [i * math.pi / 4 for i in range(8)]]
        return []


def _point_in_poly(x: float, y: float, pts: list) -> bool:
    """Ray-casting point-in-polygon test (on the boundary → counts as inside)."""
    n = len(pts)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        # on the boundary?
        if point_seg_dist(x, y, xi, yi, xj, yj) < 1e-9:
            return True
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


# ──────────────────────────────────────────────────────────────────────────────
#  High-level API (called by the goto_* scripts)
# ──────────────────────────────────────────────────────────────────────────────
def load_zones(path: str) -> list:
    """Reads nogo_zones.json into a list of Zones. Zones with 'disabled': true
    are skipped. Unknown fields are tolerated (forward compatibility)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    zones = []
    for z in data.get("zones", []):
        if z.get("disabled"):
            continue
        zones.append(Zone(
            name=z.get("name", "?"), kind=z["kind"],
            x_min=z.get("x_min", 0.0), x_max=z.get("x_max", 0.0),
            y_min=z.get("y_min", 0.0), y_max=z.get("y_max", 0.0),
            cx=z.get("cx", 0.0), cy=z.get("cy", 0.0), r=z.get("r", 0.0),
            points=z.get("points", []),
            margin_cm=z.get("margin_cm"), severity=z.get("severity", "hard"),
            note=z.get("note", ""), confidence=z.get("confidence", "measured"),
        ))
    return zones


def _eff_margin(zone: Zone, default_margin: float) -> float:
    return zone.margin_cm if zone.margin_cm is not None else default_margin


def point_blocked(zones: list, x: float, y: float,
                  default_margin: float = DEFAULT_MARGIN_CM,
                  severities=("hard",)) -> Optional[Zone]:
    """Is point (x,y) inside a FORBIDDEN zone? Returns the FIRST matching zone
    (or None). `severities`: which severities count as forbidden."""
    for z in zones:
        if z.severity not in severities:
            continue
        if z.contains_point(x, y, _eff_margin(z, default_margin)):
            return z
    return None


def path_blocked(zones: list, x0: float, y0: float, x1: float, y1: float,
                 default_margin: float = DEFAULT_MARGIN_CM,
                 severities=("hard",)) -> Optional[Zone]:
    """Does the STRAIGHT segment (x0,y0)->(x1,y1) cross a forbidden zone?
    Returns the first matching zone (or None)."""
    for z in zones:
        if z.severity not in severities:
            continue
        if z.hits_segment(x0, y0, x1, y1, _eff_margin(z, default_margin)):
            return z
    return None


def check_route(zones: list, waypoints: list,
                default_margin: float = DEFAULT_MARGIN_CM,
                severities=("hard",)) -> list:
    """Checks a full waypoint sequence (the input of goto_route.py).
    waypoints: [(x,y), ...]. Returns: a list of collisions, each
    {"leg": i, "from": (x,y), "to": (x,y), "zone": <name>, "severity": ...}.
    Empty list = the route is clear."""
    hits = []
    for i in range(len(waypoints) - 1):
        x0, y0 = waypoints[i]
        x1, y1 = waypoints[i + 1]
        z = path_blocked(zones, x0, y0, x1, y1, default_margin, severities)
        if z is not None:
            hits.append({"leg": i, "from": (x0, y0), "to": (x1, y1),
                         "zone": z.name, "severity": z.severity, "note": z.note})
    return hits


def suggest_detour(zone: Zone, x0: float, y0: float, x1: float, y1: float,
                   default_margin: float = DEFAULT_MARGIN_CM,
                   clearance: float = 8.0) -> Optional[tuple]:
    """Simple detour suggestion: the zone CORNER (pushed out `clearance` cm BEYOND
    the margin) for which the two sub-segments (p0->corner, corner->p1)
      (a) are BOTH clear (do not cross this zone), AND
      (b) give the smallest detour (minimum of |p0->corner|+|corner->p1|).
    Returns an intermediate (x,y) waypoint, or None.

    IMPORTANT: this only detours around a SINGLE zone, with ONE intermediate point. If the
    target is on the FAR SIDE of the zone such that no single corner solves it (the segment
    'passes through' the box), it returns None → the caller should either detour with two
    waypoints (up–across–down) or ask for a LIVE human decision. Deliberately conservative
    and transparent — NOT a full path planner. `clearance` ensures the suggested point
    falls OUTSIDE the margin boundary (otherwise path_blocked would count the boundary
    point as a hit too)."""
    m = _eff_margin(zone, default_margin)
    best = None
    best_cost = float("inf")
    for (cxp, cyp) in zone.inflated_corners(m + clearance):
        # the corner must be outside the zone with margin
        if zone.contains_point(cxp, cyp, m):
            continue
        # BOTH sub-segments must be clear with respect to this zone
        leg1 = zone.hits_segment(x0, y0, cxp, cyp, m)
        leg2 = zone.hits_segment(cxp, cyp, x1, y1, m)
        if leg1 or leg2:
            continue
        cost = math.hypot(cxp - x0, cyp - y0) + math.hypot(x1 - cxp, y1 - cyp)
        if cost < best_cost:
            best_cost = cost
            best = (round(cxp, 1), round(cyp, 1))
    return best


# ──────────────────────────────────────────────────────────────────────────────
#  CLI: quick route check against the map zones
#     python3 -m core.nogo  x0 y0 x1 y1 [x2 y2 ...]
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    here = Path(__file__).resolve().parent.parent
    zpath = here / "nogo_zones.json"
    if not zpath.exists():
        print("no zone file:", zpath); sys.exit(1)
    zones = load_zones(str(zpath))
    print("loaded", len(zones), "zones (" + zpath.name + "):")
    for z in zones:
        mlabel = z.margin_cm if z.margin_cm is not None else "default"
        print("  -", z.name, "[" + z.kind + ",", z.severity + ",",
              z.confidence + ", margin=" + str(mlabel) + "]")
    vals = [float(a) for a in sys.argv[1:]]
    if len(vals) >= 4:
        wps = list(zip(vals[0::2], vals[1::2]))
        print("\nroute:", wps)
        hits = check_route(zones, wps)
        if not hits:
            print("  CLEAR - no segment crosses a forbidden zone.")
        for h in hits:
            print("  FORBIDDEN", h["leg"], "segment", h["from"], "->", h["to"],
                  "crosses:", h["zone"], "(" + h["severity"] + ") -", h["note"])
            z = next(z for z in zones if z.name == h["zone"])
            dd = suggest_detour(z, h["from"][0], h["from"][1], h["to"][0], h["to"][1])
            print("      detour suggestion (intermediate waypoint):", dd)
