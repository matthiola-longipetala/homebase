"""refine_tags.py — refines the tag poses from the observation database (tag_observations.jsonl).
Weighted median per tag (weight ~ margin / distance), skipping circular observations and
observations outside the walls.

Usage:
  python3 refine_tags.py            # report only (what it would suggest)
  python3 refine_tags.py --write    # also writes it into tags_coords.json (x,y,yaw; updates conf)
"""
import os
import json, sys, math
from pathlib import Path

DB = os.path.expanduser("~/picarx-mcp/tag_observations.jsonl")
MAP = os.path.expanduser("~/picarx-mcp/tags_coords.json")
WRITE = "--write" in sys.argv
MIN_OBS = 3

def wmedian(vals, wts):
    pairs = sorted(zip(vals, wts))
    tot = sum(w for _, w in pairs); acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= tot/2: return v
    return pairs[-1][0]

def circ_wmedian_deg(vals, wts):
    # circular: unwrap +-180 around the heaviest value, then a weighted median
    base = vals[wts.index(max(wts))]
    un = []
    for v in vals:
        d = v - base
        while d > 180: d -= 360
        while d < -180: d += 360
        un.append(base + d)
    m = wmedian(un, wts)
    while m > 180: m -= 360
    while m < -180: m += 360
    return m

# reading
obs = {}
if not Path(DB).exists():
    print("no database yet:", DB); sys.exit(0)
for line in Path(DB).read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line: continue
    try: r = json.loads(line)
    except Exception: continue
    if r.get("circular"): continue
    # outside the walls -> skip (bounds = invented example flat + 40 cm; set them to YOUR flat)
    if not (-440 <= r["x"] <= 540 and -140 <= r["y"] <= 540): continue
    obs.setdefault(int(r["tag"]), []).append(r)

d = json.loads(Path(MAP).read_text(encoding="utf-8"))
print(f"{'tag':>3} {'n':>4} {'x_new':>7} {'y_new':>7} {'yaw':>6} | {'x_old':>7} {'y_old':>7} {'yaw':>6} | {'dx':>5} {'dy':>5} {'spread':>7}")
for tid in sorted(obs):
    rs = obs[tid]
    if len(rs) < MIN_OBS:
        print(f"{tid:>3} {len(rs):>4}  (too few observations, >= {MIN_OBS} needed)"); continue
    wts = [max(0.01, r["margin"]) * (150.0/(r["dist"]+150.0)) for r in rs]
    xs = [r["x"] for r in rs]; ys = [r["y"] for r in rs]; yaws = [r["yaw"] for r in rs]
    nx = wmedian(xs, wts); ny = wmedian(ys, wts); nyaw = circ_wmedian_deg(yaws, wts)
    import statistics
    spread = statistics.pstdev([math.hypot(x-nx, y-ny) for x, y in zip(xs, ys)]) if len(rs) > 1 else 0.0
    cur = d["tags"].get(str(tid), {})
    cx, cy, cyaw = cur.get("x_cm"), cur.get("y_cm"), cur.get("yaw_deg")
    dx = (nx-cx) if cx is not None else 0; dy = (ny-cy) if cy is not None else 0
    print(f"{tid:>3} {len(rs):>4} {nx:>7.0f} {ny:>7.0f} {nyaw:>6.0f} | "
          f"{(cx if cx is not None else 0):>7.0f} {(cy if cy is not None else 0):>7.0f} {(cyaw if cyaw is not None else 0):>6.0f} | "
          f"{dx:>5.0f} {dy:>5.0f} {spread:>7.1f}")
    if WRITE and str(tid) in d["tags"]:
        d["tags"][str(tid)].update({
            "x_cm": round(nx), "y_cm": round(ny), "yaw_deg": round(nyaw),
            "conf_pos": f"REFINED ({len(rs)} observations, spread {spread:.0f}cm)",
        })

if WRITE:
    json.dump(d, open(MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(">>> tags_coords.json UPDATED (with refined values)")
else:
    print("(report only — '--write' writes it)")
