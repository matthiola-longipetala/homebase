"""test_fix_multi.py — offline test of core/fix_multi.py (WITHOUT the robot).

Modelled on core/test_localization.py: pure geometry, only numpy is needed.
Run:  cd picarx-mcp && python3 core/test_fix_multi.py
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.fix_multi import fix_multi, _norm180

# synthetic mini-world (not the real tags_coords — the maths is independent of it)
WORLD = {1: (0.0, 0.0), 2: (200.0, 0.0), 3: (200.0, 200.0), 4: (0.0, 200.0),
         5: (100.0, 300.0), 6: (-150.0, 120.0)}


def xy(t):
    return WORLD[int(t)]


def observe(rx, ry, hdg, ids, rel=0.0, absol=0.0, brg=0.0, rng=None):
    """TRUE range+bearing from a known pose (with optional noise)."""
    R = rng or np.random
    out = []
    for t in ids:
        tx, ty = WORLD[t]
        d = math.hypot(tx - rx, ty - ry)
        b = _norm180(math.degrees(math.atan2(ty - ry, tx - rx)) - hdg)
        out.append((t, d * (1 + R.normal(0, rel)) + R.normal(0, absol) if (rel or absol) else d,
                    b + R.normal(0, brg) if brg else b))
    return out


def _check(name, cond):
    print(f"  {'✓' if cond else '✗'} {name}")
    return cond


def main():
    ok = True
    print("=== fix_multi self-test ===")

    # 1) NOISE-FREE: must return the truth exactly
    print("\n1) noise-free recovery (the basic requirement of fix_multi)")
    for truth in [(100.0, 100.0, 0.0), (50.0, 150.0, 90.0), (-40.0, 30.0, -135.0),
                  (170.0, 20.0, 179.0), (10.0, 250.0, 45.0)]:
        obs = observe(*truth, ids=[1, 2, 3, 4])
        f = fix_multi(obs, xy)
        d = math.hypot(f.x_cm - truth[0], f.y_cm - truth[1])
        dh = abs(_norm180(f.heading_deg - truth[2]))
        ok &= _check(f"pose {truth} -> error {d:.3f} cm / {dh:.3f} deg", d < 0.05 and dh < 0.05)

    # 2) TWO tags are enough (3 unknowns, 4 equations)
    print("\n2) minimal case: 2 tags")
    f = fix_multi(observe(100.0, 100.0, 30.0, [1, 3]), xy)
    ok &= _check(f"fix from 2 tags: {f}", f is not None and
                 math.hypot(f.x_cm - 100, f.y_cm - 100) < 0.05)

    # 3) 1 tag / 0 tags -> None (underdetermined, must not fake a fix)
    print("\n3) underdetermined input -> None")
    ok &= _check("1 tag -> None", fix_multi(observe(100.0, 100.0, 0.0, [1]), xy) is None)
    ok &= _check("0 tags -> None", fix_multi([], xy) is None)

    # 4) NO MIRROR-IMAGE BRANCH: the bearing excludes the ghost.
    #    The two solutions of a pair intersection are mirrored across the tag1-tag2 axis (y=0);
    #    the ranges of (100,+100) and (100,-100) are IDENTICAL -> only the bearing decides.
    print("\n4) mirror-image disambiguation (the weak spot of the pair-median estimator)")
    up = fix_multi(observe(100.0, 100.0, 0.0, [1, 2]), xy)
    dn = fix_multi(observe(100.0, -100.0, 0.0, [1, 2]), xy)
    ok &= _check(f"(100,+100) -> y={up.y_cm:+.1f}", abs(up.y_cm - 100) < 0.05)
    ok &= _check(f"(100,-100) -> y={dn.y_cm:+.1f} (identical ranges, only the bearing decides)",
                 abs(dn.y_cm + 100) < 0.05)

    # 5) MORE TAGS = BETTER (with noise). This is the point of the Wide camera.
    print("\n5) more tags -> smaller error (noisy)")
    rng = np.random.default_rng(42)
    prev = None
    for n in (2, 3, 4, 6):
        errs = []
        for _ in range(400):
            obs = observe(100.0, 100.0, 20.0, list(WORLD)[:n],
                          rel=0.012, absol=1.5, brg=0.6, rng=rng)
            f = fix_multi(obs, xy)
            if f:
                errs.append(math.hypot(f.x_cm - 100, f.y_cm - 100))
        m = float(np.median(errs))
        print(f"    {n} tags -> median error {m:.2f} cm")
        if prev is not None:
            ok &= _check(f"{n} tags no worse than {n-1 if n<5 else 4}", m <= prev + 0.4)
        prev = m

    # 6) do rms/quality flag BAD input? (one tag's range is grossly wrong)
    print("\n6) does the residual (rms_cm) flag bad input?")
    good = fix_multi(observe(100.0, 100.0, 0.0, [1, 2, 3, 4]), xy)
    bad_obs = observe(100.0, 100.0, 0.0, [1, 2, 3, 4])
    bad_obs[2] = (bad_obs[2][0], bad_obs[2][1] + 80.0, bad_obs[2][2])   # an 80 cm lie
    bad = fix_multi(bad_obs, xy)
    print(f"    clean: rms={good.rms_cm:.2f} cm q={good.quality:.2f}")
    print(f"    corrupted: rms={bad.rms_cm:.2f} cm q={bad.quality:.2f}")
    ok &= _check("corrupted input gives larger rms and lower quality",
                 bad.rms_cm > good.rms_cm + 5 and bad.quality < good.quality)

    print("\n" + ("=== ALL TESTS PASSED ===" if ok else "=== SOME TESTS FAILED ==="))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
