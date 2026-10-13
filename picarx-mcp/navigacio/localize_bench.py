"""localize_bench.py — OFFLINE LOCALIZATION TEST BENCH (WITHOUT the robot).

⚠️ This does NOT run on the robot. It runs in a sandbox/on a laptop, with numpy, without camera or Pi.
   Goal: make a navigation-math change DECIDABLE offline, at any time,
   instead of using up the humans' evening robot time. Only a CONFIRMED
   fix goes onto the robot — dead ends die here, for free.

   This continues the core principle of `core/localization.py`: "the math is fully
   testable in the sandbox, WITHOUT the robot". There, the transform chain was made
   testable this way; here, it is the ESTIMATOR QUALITY.

──────────────────────────────────────────────────────────────────────────────
WHAT IT DOES
──────────────────────────────────────────────────────────────────────────────
1. Takes a TRUE robot pose (from a route waypoint or a grid).
2. From tags_coords.json, computes which tags WOULD BE VISIBLE from there (pan limit,
   tag face direction, max distance) and what their TRUE range+bearing would be.
3. Adds the measured noise model (from the tape-measure validation).
4. Runs the estimator(s), and measures the error against the TRUE pose.
5. Compares the estimators, broken down by tag count.

Why it is trustworthy: without step 3 (noise-free) EVERY estimator must give 0.0 cm
error. If it is not 0, the bug is in the bench or in the estimator. (This self-test
is runnable too: `--selftest`.)

WHAT IT DOES NOT DO: it does not model the camera, the detector, servo inaccuracy,
or slipping. The noise model is EMPIRICAL (from the validated tape measurements), not
computed from first principles. So: good for RANKING (is estimator A better than B),
for ABSOLUTE error prediction only as an order of magnitude.

USAGE
    python3 navigacio/localize_bench.py --selftest
    python3 navigacio/localize_bench.py --routes        # on the real route waypoints
    python3 navigacio/localize_bench.py --grid          # on the home grid, by tag count
    python3 navigacio/localize_bench.py --noise         # noise sensitivity + CALIB bias
"""
from __future__ import annotations

import glob
import itertools
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TAGS = json.load(open(os.path.join(ROOT, "tags_coords.json"), encoding="utf-8"))["tags"]

# ── world model ───────────────────────────────────────────────────────────────
XMIN, XMAX, YMIN, YMAX = -440, 540, -140, 540     # whereami home bounds (invented example flat + 40 cm)
PAN_MAX = 70.0          # sweep edge to edge
MAX_RANGE = 550.0       # beyond this a 6cm tag is no longer detected reliably
FACE_CONE = 65.0        # readable from within this half-cone of the tag face

# ── noise model (EMPIRICAL — tape-measure validation + edge_check) ───────────
# angle-based distance: near tags +-1.5 cm, at 2 m +-3 cm -> ~1.2% relative + 1.5 cm absolute
# bearing: rotate-to-center locks below 25 px, fx~1036 -> ~1.4 deg; from a sweep ~0.6 deg
NOISE_REL = 0.012
NOISE_ABS = 1.5
NOISE_BRG = 0.6


def norm180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def tag_xy(tid):
    v = TAGS[str(int(tid))]
    return float(v["x_cm"]), float(v["y_cm"])


def in_house(p):
    return XMIN <= p[0] <= XMAX and YMIN <= p[1] <= YMAX


def tags_visible(rx, ry, heading):
    """Which tags are visible from (rx, ry, heading)? -> [(tid, range_cm, bearing_deg)]"""
    out = []
    for k, v in TAGS.items():
        if v.get("x_cm") is None or v.get("yaw_deg") is None or v.get("size_cm") is None:
            continue
        tx, ty, yaw = float(v["x_cm"]), float(v["y_cm"]), float(v["yaw_deg"])
        dx, dy = tx - rx, ty - ry
        rng = math.hypot(dx, dy)
        if rng < 20 or rng > MAX_RANGE:
            continue
        bearing = norm180(math.degrees(math.atan2(dy, dx)) - heading)
        if abs(bearing) > PAN_MAX:
            continue
        n = np.array([math.cos(math.radians(yaw)), math.sin(math.radians(yaw))])
        to_robot = np.array([rx - tx, ry - ty], dtype=float)
        to_robot /= max(np.linalg.norm(to_robot), 1e-9)
        if math.degrees(math.acos(max(-1.0, min(1.0, float(n @ to_robot))))) > FACE_CONE:
            continue
        out.append((int(k), rng, bearing))
    return out


def margins_for(obs):
    """Estimate decision_margin: larger/closer tag -> stronger."""
    m = {}
    for tid, rng, _ in obs:
        size = float(TAGS[str(tid)]["size_cm"])
        m[tid] = max(5.0, 60.0 * (size / 6.0) * (150.0 / max(rng, 30.0)))
    return m


def add_noise(obs, rel=NOISE_REL, absol=NOISE_ABS, brg=NOISE_BRG,
              rbias=0.0, bbias=0.0, rng=None):
    R = rng or np.random
    return [(t, r * (1 + R.normal(0, rel) + rbias) + R.normal(0, absol),
             b + R.normal(0, brg) + bbias) for t, r, b in obs]


# ── The estimator: the CURRENT whereami (pair intersection + per-coordinate median) ──
def _two_circles(a, ra, b, rb):
    x0, y0 = a
    x1, y1 = b
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    if d == 0 or d > ra + rb or d < abs(ra - rb):
        return []
    t = (ra * ra - rb * rb + d * d) / (2 * d)
    h2 = ra * ra - t * t
    if h2 < 0:
        return []
    h = math.sqrt(h2)
    xm, ym = x0 + t * dx / d, y0 + t * dy / d
    ox, oy = -dy / d * h, dx / d * h
    return [(xm + ox, ym + oy), (xm - ox, ym - oy)]


def est_whereami(obs, margins):
    """The whereami.py estimator 1:1 (as of when this bench was written) — the reference."""
    strong = [o for o in obs if margins.get(o[0], 30) >= 12]
    use = strong if len(strong) >= 2 else obs
    cands = []
    for A, B in itertools.combinations(use, 2):
        for p in _two_circles(tag_xy(A[0]), A[1], tag_xy(B[0]), B[1]):
            if in_house(p):
                cands.append(p)
    if not cands:
        return None
    xs = sorted(c[0] for c in cands)
    ys = sorted(c[1] for c in cands)
    mx, my = xs[len(xs) // 2], ys[len(ys) // 2]
    best = max(use, key=lambda o: margins.get(o[0], 30))
    bx, by = tag_xy(best[0])
    hdg = norm180(math.degrees(math.atan2(by - my, bx - mx)) - best[2])
    return mx, my, hdg


def est_multi(obs, margins):
    """The joint estimator (core/fix_multi.py)."""
    from core.fix_multi import fix_multi
    w = {t: margins.get(t, 30) / 30.0 for t, _, _ in obs}
    f = fix_multi(obs, tag_xy, weights=w, sanity=in_house)
    return None if f is None else (f.x_cm, f.y_cm, f.heading_deg)


ESTIMATORS = [("A whereami(pair-median)", est_whereami),
              ("B joint(fix_multi)", est_multi)]


# ── test poses ────────────────────────────────────────────────────────────────
def route_poses():
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "utvonalak", "routes", "*.json"))):
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        for wp in d.get("waypoints", []):
            c = wp.get("coord") or {}
            if c.get("x_cm") is None or c.get("heading_deg") is None:
                continue
            out.append((os.path.basename(path)[:-5], wp.get("i"),
                        float(c["x_cm"]), float(c["y_cm"]), float(c["heading_deg"])))
    return out


def grid_poses(step=70):
    return [(gx, gy, gh)
            for gx in range(XMIN + 40, XMAX - 10, step)
            for gy in range(YMIN + 30, YMAX - 10, step)
            for gh in (-90, 0, 90, 180)]


# ── runners ───────────────────────────────────────────────────────────────────
def selftest():
    """WITHOUT NOISE every estimator must be exact. This is the bench's self-test."""
    print("=== SELF-TEST: without noise every estimator must give ~0.0 cm ===")
    ok = True
    for tx, ty, th in grid_poses(90):
        vis = tags_visible(tx, ty, th)
        if len(vis) < 2:
            continue
        mg = margins_for(vis)
        for name, fn in ESTIMATORS:
            e = fn(vis, mg)
            if e is None:
                continue
            d = math.hypot(e[0] - tx, e[1] - ty)
            dh = abs(norm180(e[2] - th))
            if d > 1.0 or dh > 1.0:
                print(f"  ✗ {name}: ({tx},{ty},{th}) -> error {d:.1f} cm / {dh:.1f} deg")
                ok = False
    print("  ✓ every estimator is exact without noise" if ok else "  ✗ SELF-TEST FAILED")
    return ok


def _compare(poses, label, n_runs=40, seed=7, **noise):
    rng = np.random.default_rng(seed)
    res = {name: {"pos": [], "hdg": []} for name, _ in ESTIMATORS}
    for _, _, tx, ty, th in poses:
        vis = tags_visible(tx, ty, th)
        if len(vis) < 2:
            continue
        mg = margins_for(vis)
        for _ in range(n_runs):
            o = add_noise(vis, rng=rng, **noise)
            for name, fn in ESTIMATORS:
                e = fn(o, mg)
                if e is None:
                    continue
                res[name]["pos"].append(math.hypot(e[0] - tx, e[1] - ty))
                res[name]["hdg"].append(abs(norm180(e[2] - th)))
    print(f"\n=== {label} ===")
    print(f"{'estimator':26} | {'pos med':>8} {'pos 90p':>8} | {'hdg med':>8} {'hdg 90p':>8}")
    print("-" * 68)
    for name, _ in ESTIMATORS:
        p = np.array(res[name]["pos"])
        h = np.array(res[name]["hdg"])
        if not len(p):
            continue
        print(f"{name:26} | {np.median(p):7.1f}c {np.percentile(p,90):7.1f}c | "
              f"{np.median(h):7.1f}f {np.percentile(h,90):7.1f}f")
    return res


def run_routes():
    poses = route_poses()
    print(f"route waypoints (real, recorded poses): {len(poses)}")
    _compare(poses, "ON REAL ROUTE WAYPOINTS")


def run_grid():
    poses = [(None, None, x, y, h) for x, y, h in grid_poses()]
    _compare(poses, "ON THE HOME GRID")
    print("\n=== DO MORE TAGS HELP? (Wide camera: 6-7 tags/sweep) ===")
    rng = np.random.default_rng(3)
    buckets = {}
    for _, _, tx, ty, th in poses:
        vis = tags_visible(tx, ty, th)
        if len(vis) < 2:
            continue
        mg = margins_for(vis)
        for _ in range(25):
            o = add_noise(vis, rng=rng)
            row = []
            for _, fn in ESTIMATORS:
                e = fn(o, mg)
                row.append((math.hypot(e[0] - tx, e[1] - ty),
                            abs(norm180(e[2] - th))) if e else (np.nan, np.nan))
            buckets.setdefault(min(len(vis), 8), []).append(row)
    print(f"{'tags seen':>10} {'samples':>6} | {'A pos':>7} {'B pos':>7} | {'A hdg':>6} {'B hdg':>6}")
    for k in sorted(buckets):
        a = np.array(buckets[k], dtype=float)
        print(f"{k:>10} {len(a):>6} | {np.nanmedian(a[:,0,0]):7.1f} {np.nanmedian(a[:,1,0]):7.1f} | "
              f"{np.nanmedian(a[:,0,1]):6.1f} {np.nanmedian(a[:,1,1]):6.1f}")


def run_noise():
    poses = [(None, None, x, y, h) for x, y, h in grid_poses()]
    print("\n=== NOISE SENSITIVITY (does the conclusion change with the noise model?) ===")
    for lbl, kw in [
        ("noise-free", dict(rel=0, absol=0, brg=0)),
        ("baseline (validated)", dict()),
        ("2.5x noise", dict(rel=0.03, absol=3.0, brg=1.5)),
        ("5x noise (pessimistic)", dict(rel=0.06, absol=6.0, brg=3.0)),
        ("SYST. +5% distance (bad CALIB_A)", dict(rbias=+0.05)),
        ("SYST. +2 deg bearing", dict(bbias=+2.0)),
    ]:
        _compare(poses, lbl, n_runs=12, **kw)


if __name__ == "__main__":
    args = sys.argv[1:] or ["--selftest", "--routes"]
    if "--selftest" in args:
        selftest()
    if "--routes" in args:
        run_routes()
    if "--grid" in args:
        run_grid()
    if "--noise" in args:
        run_noise()
