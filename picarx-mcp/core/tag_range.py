"""tag_range.py — ROBUST tag distance from PIXEL SIZE (not from the flipping pose).

Problem (lesson learned): the full 6-DOF AprilTag pose (pose_t) FLIPS for distant/oblique/small
tags ("more than one new minima") → wrong depth/distance → jumping x,y, and therefore
badly "guessed" distances. Depth (distance along the optical axis) is the worst-determined
component of the pose.

Solution: compute the distance from the tag's APPARENT PIXEL SIZE. This is INDEPENDENT of the flip:
    dist_cm ≈ tag_size_cm * fx / tag_width_px           (frontal, small-angle approximation)
The pixel width is averaged from the 4 corners (r.corners). In oblique views the visible edge
is foreshortened → slight overestimate; this is mitigated by taking the LONGER pair of opposite
edges (the less foreshortened direction), or the apparent area-based diameter.

The DIRECTION (bearing) is robust too: horizontal offset of the tag's image centre
+ the camera pan angle -> the tag's direction relative to the robot heading.

RANGE + BEARING to a tag with KNOWN world position >> the full pose, because neither input
is the flipping depth. Range+bearing of two tags gives an unambiguous robot position.

NOTE: originally written without hardware access; validate on the robot against the sonar
(the sonar is the ground truth for frontal distance) and against localize().
"""
import math
import numpy as np

#   real ~= CALIB_A * raw_pixel_dist + CALIB_B.   (raw = size_cm*fx/px)
# 5 points (raw->real): 26->40, 35->48, 83->100, 123->150, 204->233.
#   A single multiplier does NOT work (the needed factor is ~1.54 near, ~1.14 far -> distance-dependent).
#   A linear fit brings ALL 5 points within ±5cm:
#     A=1.08 (slight fx error), B=12cm (reference offset: the sensors sit ~12cm behind the
#     tape measure's zero point). Check: 26->40, 83->102, 204->232. EXCELLENT.
# IMPORTANT for accuracy: the tag should be WELL FRAMED (not cropped / at the edge), frontal,
# for a high tag ~+15° camera tilt, average 3-4 frames. (The "164cm" outlier was a different pose.)
#   A=1.08 compensated the OLD ov5647 fx error — with the new, accurate calibration
#   (fx=1036.5, reproj 0.087 px) the raw formula is correct on its own: A=1.0.
#   B=12 (sensor offset to the tape zero point) is PHYSICAL in origin, unchanged.
#   Validation (raw -> tape): 67.6->80 (err -0.4), 65.7->79 (-1.3), 190->205 (-3),
#   320->315 (+17 — 39px distant tag, quantisation noise; TODO: more far points after undistort).
#   ⚠️ For tags at the IMAGE EDGE (bearing offset > ~30°) the distance is NOT reliable without
#   undistort because of distortion (live example: tag7 353 vs 494 real) — use a centred fix!
CALIB_A = 1.0
CALIB_B = 12.0


def _edge_lengths_px(corners):
    """Lengths of the tag's 4 edges in pixels (corners: 4x2, clockwise or counter-clockwise)."""
    c = np.asarray(corners, dtype=float)
    return [float(np.linalg.norm(c[(i + 1) % 4] - c[i])) for i in range(4)]


def tag_pixel_size(corners, mode="maxpair"):
    """Apparent tag size in pixels.
    mode='mean'    : average of all 4 edges (simple).
    mode='maxpair' : average of the LONGER of the two opposite edge pairs
                     (the less foreshortened direction -> better distance in oblique views).
    """
    e = _edge_lengths_px(corners)            # e0=01,e1=12,e2=23,e3=30
    if mode == "mean":
        return sum(e) / 4.0
    pair_a = (e[0] + e[2]) / 2.0             # 01 & 23 (opposite)
    pair_b = (e[1] + e[3]) / 2.0             # 12 & 30 (opposite)
    return max(pair_a, pair_b)


def tag_range_raw_cm(corners, size_cm, fx, mode="maxpair"):
    """RAW distance from pixel size, WITHOUT correction: size_cm * fx / pixel_size."""
    px = tag_pixel_size(corners, mode=mode)
    if px <= 1e-6:
        return None
    return size_cm * fx / px


def tag_range_cm(corners, size_cm, fx, mode="maxpair", corrected=True):
    """ROBUST, CALIBRATED distance to the tag in cm from pixel size. Flip-independent.
    Linear correction: dist = CALIB_A * (size_cm*fx/px) + CALIB_B  (see above, 5-point fit).
    corrected=False -> the raw distance (without correction).
    """
    raw = tag_range_raw_cm(corners, size_cm, fx, mode=mode)
    if raw is None:
        return None
    return (CALIB_A * raw + CALIB_B) if corrected else raw


def tag_range_angular_cm(corners, size_cm, fx, fy, cx, cy, corrected=True):
    """ANGLE-BASED distance — safe at the image edge (lesson from the IMX708 Wide edge_check).

    The size*fx/px formula only holds ON the image AXIS: pinhole projection magnifies
    by sec^2(theta) towards the edges (with a 96 deg FOV that is already ~30% distance error
    halfway out!). Here we compute from the angle between the normalised DIRECTION VECTORS
    of the 4 corners — this is independent of the projection. Expects the corners to come
    from the UNDISTORTED image (all our callers detect that way). maxpair principle: the
    edge pair with the larger angle (the less foreshortened direction). Validated: edge_check,
    tag1 351+-2 cm across the whole field of view (the px-based one scattered 233-351 there)."""
    c = np.asarray(corners, dtype=float)
    rays = []
    for u, v in c:
        r = np.array([(u - cx) / fx, (v - cy) / fy, 1.0])
        rays.append(r / np.linalg.norm(r))

    def _ang(i, j):
        return math.acos(max(-1.0, min(1.0, float(np.dot(rays[i], rays[j])))))

    pair_a = (_ang(0, 1) + _ang(2, 3)) / 2.0
    pair_b = (_ang(1, 2) + _ang(3, 0)) / 2.0
    a = max(pair_a, pair_b)
    if a < 1e-9:
        return None
    raw = size_cm / (2.0 * math.tan(a / 2.0))
    return (CALIB_A * raw + CALIB_B) if corrected else raw


def tag_bearing_deg(center_x, pan_deg, fx, cx):
    """Direction of the tag relative to the robot HEADING, in degrees.
    Sign per project convention: +bearing = tag to the right (consistent with pan).
    bearing = pan + atan2(center_x - cx, fx)
    (a tag right of the image centre gives a positive image offset).
    """
    return pan_deg + math.degrees(math.atan2(center_x - cx, fx))


def robot_xy_from_range_bearing(tag_world, tag_range, bearing_deg, heading_deg):
    """Estimate robot (x,y) from ONE tag's range+bearing + a known robot heading.
    The tag's WORLD direction from the robot = heading + bearing (CCW from +X).
    The robot is in the OPPOSITE direction from the tag, at tag_range distance.
        world_dir = heading + bearing
        robot = tag_world - tag_range * (cos world_dir, sin world_dir)
    Note: the heading must be known separately (e.g. from a frontal, near tag, or from DR).
    """
    wd = math.radians(heading_deg + bearing_deg)
    tx, ty = tag_world
    return (tx - tag_range * math.cos(wd), ty - tag_range * math.sin(wd))


def robot_xy_from_two_tags(tagA_world, rA, tagB_world, rB):
    """Robot (x,y) from the ranges of TWO tags (intersection of circles), WITHOUT heading.
    Intersection points of two circles (centre=tag world pos, radius=range); of the two
    solutions the caller chooses with a sanity filter (home boundaries), or disambiguates with the bearing.
    Returns: [(x1,y1),(x2,y2)] or [] if there is no intersection.
    """
    x0, y0 = tagA_world; x1, y1 = tagB_world
    dx, dy = x1 - x0, y1 - y0
    d = math.hypot(dx, dy)
    if d == 0 or d > rA + rB or d < abs(rA - rB):
        return []                              # no (real) intersection
    a = (rA * rA - rB * rB + d * d) / (2 * d)
    h2 = rA * rA - a * a
    if h2 < 0:
        return []
    h = math.sqrt(h2)
    xm, ym = x0 + a * dx / d, y0 + a * dy / d
    ox, oy = -dy / d * h, dx / d * h
    return [(xm + ox, ym + oy), (xm - ox, ym - oy)]


if __name__ == "__main__":
    # self-test: a 6cm tag with fx=1217, 60px wide -> ~122cm
    fx = 1217.6
    fake = [(480, 330), (540, 330), (540, 390), (480, 390)]   # 60px side
    print("pixel_size:", round(tag_pixel_size(fake), 1))
    print("range_cm (6cm tag):", round(tag_range_cm(fake, 6.0, fx), 1))
    print("bearing (center, pan=20):", round(tag_bearing_deg(510, 20, fx, 518.3), 1))
