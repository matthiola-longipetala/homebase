"""fix_multi.py — JOINT tag fix: (x, y, heading) from ALL visible tags at once.

⚠️ STATUS: STAGED — validated offline, NOT YET RUN LIVE.
   Nothing imports it. Wiring it into whereami / goto_* is due AFTER a supervised
   live driving test. Until then this file just sits here.

──────────────────────────────────────────────────────────────────────────────
WHY
──────────────────────────────────────────────────────────────────────────────
The current whereami estimator works like this:
  1. for every tag PAIR intersect two circles (robot_xy_from_two_tags) -> 2 solutions/pair
  2. filter with the home boundaries (in_house)                        -> should drop the "ghost"
  3. coordinate-wise median of the remaining candidates                -> (x, y)
  4. bearing of the STRONGEST tag                                      -> heading

Two structural weaknesses (confirmed by offline measurement):

  (a) GHOST LEAKAGE. The two solutions of a two-circle intersection are MIRROR
      IMAGES of each other across the axis of the two tags. The home-boundary
      filter is not enough: the ghost is often ALSO inside the home. Measured
      (321 grid poses): 43% of the candidates entering the median are ghosts, and
      80% of the poses have at least one.
      The docstring of `robot_xy_from_two_tags` already hinted at the fix
      ("...or disambiguate with the bearing") — the caller simply did not use it.

  (b) MORE TAGS BARELY HELP. That is exactly the main gain of the Wide camera:
      6-7 tags from ONE sweep instead of the old 2. But the pair-median estimator
      gets little out of it: 2 tags -> 6.0 cm, 8 tags -> 5.0 cm (only -17%),
      because the ghosts multiply TOGETHER with the tag count. Heading is even
      worse: it is ALWAYS decided by a single tag, with no redundancy -> outliers
      (6-7 deg).

This joint estimator counters both: no pair decomposition, no mirror-image
branch, and the heading also comes from the JOINT fit, using every tag.

──────────────────────────────────────────────────────────────────────────────
HOW
──────────────────────────────────────────────────────────────────────────────
Unknown: p = (x, y, heading). Every visible tag gives TWO equations:
    range residual   : |tag_i - (x,y)| - r_i                        [cm]
    bearing residual : angle(tag_i - (x,y)) - heading - b_i         [rad]
Weighted Gauss-Newton (decision_margin). The bearing residual is brought to a
common scale with an SB [cm/rad] factor, so a single least-squares solve covers both.
The mirror-image branch never appears here: the bearing selects the correct side.

WHY NOT localize()? localize() works from the full 6-DOF pose (pose_t), whose
DEPTH is the worst component (it flips — the very reason tag_range.py exists).
This module uses only range+bearing, i.e. the two WELL-determined quantities,
with the angle-based (image-edge-safe) distance.

MEASURED (offline, 321 grid poses + 38 real route waypoints, with validated noise):
    position error median :  5.2 cm -> 3.3 cm    (-37%)
    heading error median  :  1.4 deg -> 0.8 deg  (-43%)
    heading outliers      :  6-7 deg -> <1.5 deg (this is the bigger win!)
    with 8 tags           :  5.0 cm -> 2.6 cm    (-48%)
Noise sensitivity: from 0 to 5x noise the gain stays at a 0.63-0.65x ratio
THROUGHOUT — it is not an artefact of the noise model.

⚠️ HONEST LIMIT: this does NOT fix a systematic distance bias (a drifted CALIB_A)
either — with a +5% distance bias the position error is the same for both
estimators (18.5 cm). The joint fix averages out NOISE, it does not replace
CALIBRATION. (Heading still errs half as much there, because the bearings hold it.)
"""
from __future__ import annotations

import itertools
import math
from typing import Optional

import numpy as np


def _norm180(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _two_circles(a, ra, b, rb):
    """Intersection points of two circles — ONLY for the initial guess (the final fix does not use it)."""
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


class MultiFix:
    """Result of a joint fix."""

    __slots__ = ("x_cm", "y_cm", "heading_deg", "n_tags", "tag_ids",
                 "rms_cm", "rms_deg", "quality")

    def __init__(self, x_cm, y_cm, heading_deg, n_tags, tag_ids,
                 rms_cm, rms_deg, quality):
        self.x_cm = x_cm
        self.y_cm = y_cm
        self.heading_deg = heading_deg
        self.n_tags = n_tags
        self.tag_ids = tag_ids
        self.rms_cm = rms_cm          # RMS of the range residuals [cm] — the "tension" of the fix
        self.rms_deg = rms_deg        # RMS of the bearing residuals [deg]
        self.quality = quality        # 0..1

    def as_dict(self) -> dict:
        return {"x_cm": round(self.x_cm, 1), "y_cm": round(self.y_cm, 1),
                "heading_deg": round(self.heading_deg, 1), "n_tags": self.n_tags,
                "tag_ids": list(self.tag_ids), "rms_cm": round(self.rms_cm, 2),
                "rms_deg": round(self.rms_deg, 2), "quality": round(self.quality, 2)}

    def __repr__(self):
        return (f"MultiFix(x={self.x_cm:+.0f} y={self.y_cm:+.0f} "
                f"hdg={self.heading_deg:+.0f} n={self.n_tags} "
                f"rms={self.rms_cm:.1f}cm/{self.rms_deg:.1f}deg q={self.quality:.2f})")


# cm-equivalent scale of the bearing residual: 1 rad of bearing error ~ SB_CM cm.
# Roughly the typical tag distance; 40 cm/rad ~ 0.7 cm / deg.
SB_CM = 40.0


def fix_multi(obs, tag_xy, weights=None, max_iter=60,
              init_xy=None, sanity=None) -> Optional[MultiFix]:
    """Joint (x, y, heading) fix from the range+bearing of SEVERAL tags.

    Args:
        obs: [(tag_id, range_cm, bearing_deg), ...] — bearing relative to the robot
             HEADING, +right (the tag_bearing_deg convention).
             At least 2 entries are needed (1 tag is underdetermined: 3 unknowns, 2 equations).
        tag_xy: callable, tag_id -> (x_cm, y_cm) in the world frame.
        weights: {tag_id: weight} or None (equal weights). decision_margin/30
                 is a good weight, clipped to 0.3..2.0.
        init_xy: optional initial guess (x, y) — e.g. the previous fix or dead reckoning.
                 If None, starts from a two-circle intersection (chosen with the sanity filter).
        sanity: callable (x, y) -> bool, sanity filter for the initial guess
                (e.g. home boundaries). It only filters the START, not the result.

    Returns:
        MultiFix or None (if <2 tags, or it did not converge).

    Note: the bearing already excludes the mirror-image branch, so there is NO ghost
    here — the sanity filter is only needed so that we START on the right side.
    """
    obs = [o for o in obs if o[1] is not None and o[1] > 0]
    if len(obs) < 2:
        return None

    ids = [int(o[0]) for o in obs]
    P = np.array([tag_xy(i) for i in ids], dtype=float)
    R = np.array([float(o[1]) for o in obs], dtype=float)
    B = np.array([math.radians(float(o[2])) for o in obs], dtype=float)

    if weights is None:
        w = np.ones(len(obs))
    else:
        w = np.array([min(2.0, max(0.3, float(weights.get(i, 1.0)))) for i in ids])

    # ── initial guess ─────────────────────────────────────────────────────────
    if init_xy is not None:
        start = (float(init_xy[0]), float(init_xy[1]))
    else:
        start = None
        # start from the circles of the two STRONGEST tags (they are the most reliable)
        order = sorted(range(len(obs)), key=lambda k: -w[k])
        for a, b in itertools.combinations(order, 2):
            pts = _two_circles(tuple(P[a]), R[a], tuple(P[b]), R[b])
            for p in pts:
                if sanity is None or sanity(p):
                    # the bearing decides between the two mirror images: which is more consistent?
                    if start is None:
                        start = p
                    else:
                        def _spread(q):
                            wd = np.arctan2(P[:, 1] - q[1], P[:, 0] - q[0]) - B
                            c, s = np.sum(w * np.cos(wd)), np.sum(w * np.sin(wd))
                            return math.hypot(c, s)          # larger = more consistent
                        if _spread(p) > _spread(start):
                            start = p
            if start is not None:
                break
        if start is None:
            start = (float(P[:, 0].mean()), float(P[:, 1].mean()))

    def _heading_at(px_, py_):
        wd = np.arctan2(P[:, 1] - py_, P[:, 0] - px_) - B
        return math.atan2(float(np.sum(w * np.sin(wd))), float(np.sum(w * np.cos(wd))))

    x = np.array([start[0], start[1], _heading_at(*start)], dtype=float)

    # ── Gauss-Newton ──────────────────────────────────────────────────────────
    r = None
    for _ in range(max_iter):
        px_, py_, h = x
        dx = P[:, 0] - px_
        dy = P[:, 1] - py_
        d = np.maximum(np.hypot(dx, dy), 1e-6)
        r_res = d - R
        wd = np.arctan2(dy, dx)
        b_res = np.arctan2(np.sin(wd - h - B), np.cos(wd - h - B)) * SB_CM
        Jr = np.column_stack([-dx / d, -dy / d, np.zeros(len(obs))])
        Jb = np.column_stack([dy / (d * d) * SB_CM, -dx / (d * d) * SB_CM,
                              -np.ones(len(obs)) * SB_CM])
        J = np.vstack([Jr * w[:, None], Jb * w[:, None]])
        r = np.concatenate([r_res * w, b_res * w])
        try:
            step, *_ = np.linalg.lstsq(J, -r, rcond=None)
        except np.linalg.LinAlgError:
            return None
        step = np.clip(step, -50.0, 50.0)
        x = x + step
        if np.linalg.norm(step[:2]) < 0.01 and abs(step[2]) < 1e-4:
            break

    if r is None or not np.all(np.isfinite(x)):
        return None

    n = len(obs)
    rms_cm = float(np.sqrt(np.mean((r[:n] / np.maximum(w, 1e-6)) ** 2)))
    rms_deg = float(np.degrees(np.sqrt(np.mean((r[n:] / np.maximum(w, 1e-6) / SB_CM) ** 2))))
    # quality: from the residuals. Good fix: rms_cm < ~5, rms_deg < ~1.5
    quality = float(max(0.0, min(1.0, 1.0 - rms_cm / 25.0))
                    * max(0.0, min(1.0, 1.0 - rms_deg / 8.0)))

    return MultiFix(x_cm=float(x[0]), y_cm=float(x[1]),
                    heading_deg=_norm180(math.degrees(float(x[2]))),
                    n_tags=n, tag_ids=ids, rms_cm=rms_cm, rms_deg=rms_deg,
                    quality=quality)
