"""
localization.py — "Where am I?" — AprilTag-based pose estimation (navigation, phase C).

This is the heart of navigation: from the image of a single known AprilTag I compute WHERE
I am and WHICH WAY I am facing in the frame of the home map. This lets the robot "see itself"
on the map, and the accumulating error of dead reckoning (blind estimation) goes away — the
robot re-localizes after every step.

This module is DELIBERATELY pure geometry: it does NOT import `pupil_apriltags` or
`cv2`. Those run on the Pi (in the `localize` tool), and pass the FINISHED detection
results (`pose_R`, `pose_t`) in here. That way the maths is fully testable in a sandbox,
WITHOUT the robot (see `core/test_localization.py`).

──────────────────────────────────────────────────────────────────────────────
COORDINATE FRAMES
──────────────────────────────────────────────────────────────────────────────
All lengths are in CENTIMETRES, and ALL inputs must use the same unit:
  - the map (tags.json) x,y,z in cm,
  - the detector's `pose_t` in cm as well  →  so the detector must be given
    `tag_size` in cm (the `localize` tool's job).

W — WORLD (map) frame:
    +X right, +Y forward (along the hallway), +Z up. Right-handed.
    Origin: the robot's dock / start point (in the invented example flat: the dock
    spot in the hallway, see navigacio/tags_template.json).
    The robot's 2D pose: (x, y, heading). `heading` = the robot's FORWARD axis,
    in degrees, CCW (counter-clockwise) from the +X axis. So:
        heading =   0°  → robot faces +X (right)
        heading =  90°  → faces +Y (forward, down the hallway)
        heading = 180°  → faces −X
        heading = −90°  → faces −Y (back towards the dock / front door)

R — ROBOT frame:
    x_R = forward (the heading direction), y_R = left, z_R = up. Origin is the
    robot's centre projected onto the floor (the (x,y) we are looking for).

C — CAMERA optical frame (OpenCV / pupil_apriltags convention):
    x_C = right in the image, y_C = DOWN in the image, z_C = optical axis (forward,
    into the scene). The camera sits ~10 cm high, on a pan/tilt servo.

T — TAG frame (given by the detector as `pose_R`, `pose_t`, such that
    p_C = pose_R @ p_T + pose_t — the tag point in the camera frame):
    x_T = right, y_T = down, z_T = OUT of the tag face towards the viewer
    (`Z_OUT_OF_TAG=True`). IF the actual pupil_apriltags build defines it the
    other way (z into the wall), set the `Z_OUT_OF_TAG` flag to False —
    this is the ONLY thing that has to be verified live on the robot. Every
    OTHER part of the module is convention-independent and verified offline.

──────────────────────────────────────────────────────────────────────────────
THE TRANSFORM CHAIN
──────────────────────────────────────────────────────────────────────────────
    T_W_tag = T_W_R · T_R_C · T_C_tag
where
    T_W_tag : pose of the tag in the world  (from the map: x,y,z,yaw)
    T_C_tag : pose of the tag in the camera (from the detector: pose_R, pose_t)
    T_R_C   : pose of the camera in the robot (mount + pan/tilt — measured extrinsic)
    T_W_R   : pose of the robot in the world (THIS is the unknown we want)
Solution:
    T_W_R = T_W_tag · (T_R_C · T_C_tag)⁻¹

Several visible tags → one pose estimate per tag, then fusion (median position +
circular-mean heading), weighting outlier / poor-angle tags by quality.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

# ── The ONLY convention flag that must be checked live ─────────────────────────
# The z axis of the pupil_apriltags tag frame points towards the viewer (True) or into
# the wall (False). This cannot be decided offline without the library — it has to be
# checked once on the robot with a known tag. The module's algebra is correct either way.
Z_OUT_OF_TAG = False   # VERIFIED LIVE: pupil_apriltags z points into the wall


# ──────────────────────────────────────────────────────────────────────────────
#  Basic rotations and homogeneous transform
# ──────────────────────────────────────────────────────────────────────────────
def rot_x(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)


def rot_y(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)


def rot_z(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)


def make_T(R: np.ndarray, t) -> np.ndarray:
    """4×4 homogeneous transform from an R (3×3) rotation and a t (3) translation."""
    T = np.eye(4)
    T[:3, :3] = np.asarray(R, dtype=float)
    T[:3, 3] = np.asarray(t, dtype=float).reshape(3)
    return T


def invert_T(T: np.ndarray) -> np.ndarray:
    """Inverse of a homogeneous transform (assuming an orthonormal R)."""
    R = T[:3, :3]
    t = T[:3, 3]
    Ti = np.eye(4)
    Ti[:3, :3] = R.T
    Ti[:3, 3] = -R.T @ t
    return Ti


# ──────────────────────────────────────────────────────────────────────────────
#  Frame builders
# ──────────────────────────────────────────────────────────────────────────────
def tag_world_transform(x_cm: float, y_cm: float, z_cm: float, yaw_deg: float,
                        z_out_of_tag: bool = Z_OUT_OF_TAG) -> np.ndarray:
    """T_W_tag — world pose of a tag mounted vertically on a wall.

    The tag centre is (x,y,z) in cm, its face looks in the `yaw` direction (the angle
    the tag FACES, in degrees CCW from +X — the robot approaches from there).

    Convention (z_out_of_tag=True), with the tag z pointing towards the viewer (robot):
        z_T (out)    = (cos yaw, sin yaw, 0)          # face normal towards the room
        y_T (down)   = (0, 0, -1)                      # the tag is vertical, "down"=−Z
        x_T (right)  = y_T × z_T = (sin yaw, -cos yaw, 0)
    """
    yaw = math.radians(yaw_deg)
    n = np.array([math.cos(yaw), math.sin(yaw), 0.0])       # face normal (outward)
    y_T = np.array([0.0, 0.0, -1.0])                        # "down" in the image
    x_T = np.cross(y_T, n)                                  # right
    z_T = n
    R = np.column_stack([x_T, y_T, z_T])
    if not z_out_of_tag:
        # 180° about the tag's y axis: x and z flip sign (z points into the wall)
        R = R @ rot_y(math.pi)
    return make_T(R, [x_cm, y_cm, z_cm])


def camera_extrinsic(pan_deg: float = 0.0, tilt_deg: float = 0.0,
                     cam_height_cm: float = 10.0,
                     cam_forward_cm: float = 6.0,
                     cam_lateral_cm: float = 0.0) -> np.ndarray:
    """T_R_C — pose of the camera optical frame in the robot frame.

    With pan=0, tilt=0 the camera looks FORWARD, horizontally:
        z_C (optical, forward) = x_R (robot forward)
        x_C (image right)      = −y_R (the robot's right)
        y_C (image down)       = −z_R (downward)
    Pan rotates about the robot's z_R axis (+pan = right, as in the `look` tool),
    tilt about the camera's horizontal axis (+tilt = up).

    The mount parameters (height, forward offset) are ESTIMATED — refine them in an
    interactive session using a distance measured to a known tag. The pose algebra is
    correct regardless; the exact values only shift the absolute output (x,y).
    """
    # Base: camera forward, horizontal (columns = x_C, y_C, z_C in the robot frame)
    R_base = np.column_stack([
        np.array([0.0, -1.0, 0.0]),   # x_C = −y_R
        np.array([0.0, 0.0, -1.0]),   # y_C = −z_R
        np.array([1.0, 0.0, 0.0]),    # z_C = +x_R
    ])
    # +pan = right = NEGATIVE about the robot z_R (clockwise seen from above)
    R_pan = rot_z(math.radians(-pan_deg))
    # +tilt = up: a NEGATIVE rotation about the robot y_R (left) axis lifts the
    # forward axis (rot_y(α): +x → (cosα,0,−sinα); with −α, +z goes up)
    R_tilt = rot_y(math.radians(-tilt_deg))
    R = R_pan @ R_tilt @ R_base
    t = np.array([cam_forward_cm, cam_lateral_cm, cam_height_cm])
    return make_T(R, t)


# ──────────────────────────────────────────────────────────────────────────────
#  One tag → robot pose
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class TagWorld:
    """World pose of a map tag + metadata."""
    tag_id: int
    x_cm: float
    y_cm: float
    z_cm: float
    yaw_deg: float
    name: str = ""
    boundary: bool = False
    forbidden: bool = False     # forbidden-zone marker (e.g. bathroom)


@dataclass
class PoseEstimate:
    """A pose estimate (from one tag or from fusion)."""
    x_cm: float
    y_cm: float
    heading_deg: float
    n_tags: int = 1
    tag_ids: list = field(default_factory=list)
    spread_cm: float = 0.0       # position spread between tag estimates (for fusion)
    spread_deg: float = 0.0      # heading spread
    quality: float = 1.0         # 0..1, higher = better

    def as_dict(self) -> dict:
        return {
            "x_cm": round(self.x_cm, 1),
            "y_cm": round(self.y_cm, 1),
            "heading_deg": round(self.heading_deg, 1),
            "n_tags": self.n_tags,
            "tag_ids": list(self.tag_ids),
            "spread_cm": round(self.spread_cm, 1),
            "spread_deg": round(self.spread_deg, 1),
            "quality": round(self.quality, 2),
        }


def robot_pose_from_detection(pose_R, pose_t, tag: TagWorld,
                              extrinsic: np.ndarray) -> PoseEstimate:
    """Computes the robot world pose from a single tag detection.

    Args:
        pose_R: 3×3 rotation from the detector (p_C = pose_R @ p_T + pose_t)
        pose_t: 3-vector translation from the detector, the tag centre in the camera
                frame, in CM (the detector must be given tag_size in cm!)
        tag: the known world pose of the tag (from the map)
        extrinsic: T_R_C (result of camera_extrinsic(...))

    Returns:
        PoseEstimate (x_cm, y_cm, heading_deg)
    """
    pose_R = np.asarray(pose_R, dtype=float).reshape(3, 3)
    pose_t = np.asarray(pose_t, dtype=float).reshape(3)
    T_C_tag = make_T(pose_R, pose_t)
    T_W_tag = tag_world_transform(tag.x_cm, tag.y_cm, tag.z_cm, tag.yaw_deg)
    # T_W_R = T_W_tag · (T_R_C · T_C_tag)⁻¹
    T_W_R = T_W_tag @ invert_T(extrinsic @ T_C_tag)
    x, y = T_W_R[0, 3], T_W_R[1, 3]
    # heading = the robot's forward axis (x_R) in the world, projected onto the XY plane
    fwd = T_W_R[:3, :3] @ np.array([1.0, 0.0, 0.0])
    heading = math.degrees(math.atan2(fwd[1], fwd[0]))
    return PoseEstimate(x_cm=float(x), y_cm=float(y), heading_deg=float(heading),
                        n_tags=1, tag_ids=[tag.tag_id])


# ──────────────────────────────────────────────────────────────────────────────
#  REVERSE DIRECTION — the robot surveys a tag (from a known pose)
# ──────────────────────────────────────────────────────────────────────────────
def robot_world_transform(x_cm: float, y_cm: float, heading_deg: float) -> np.ndarray:
    """T_W_R — the robot world pose from a known (x,y,heading).
    x_R (forward) = heading direction, y_R (left) = +90°, z_R = up. On the floor (z=0)."""
    return make_T(rot_z(math.radians(heading_deg)), [x_cm, y_cm, 0.0])


def tag_world_from_robot(robot_x: float, robot_y: float, robot_heading_deg: float,
                         pose_R, pose_t, extrinsic: np.ndarray) -> dict:
    """Computes the tag's WORLD pose from a KNOWN robot pose + the tag detection.

    This is the inverse of `robot_pose_from_detection`: T_W_tag = T_W_R · T_R_C · T_C_tag.
    This way the robot can survey tags itself (self-calibration) when it knows where it stands.

    Returns: {"x_cm","y_cm","z_cm","yaw_deg"} — can be written straight into tags.json,
    and round-trips with `tag_world_transform` (with the current Z_OUT_OF_TAG setting).
    """
    pose_R = np.asarray(pose_R, dtype=float).reshape(3, 3)
    pose_t = np.asarray(pose_t, dtype=float).reshape(3)
    T_C_tag = make_T(pose_R, pose_t)
    T_W_R = robot_world_transform(robot_x, robot_y, robot_heading_deg)
    T_W_tag = T_W_R @ extrinsic @ T_C_tag
    x, y, z = T_W_tag[0, 3], T_W_tag[1, 3], T_W_tag[2, 3]
    zcol = T_W_tag[:3, :3][:, 2]                 # the tag's local z axis in the world
    n = zcol if Z_OUT_OF_TAG else -zcol          # outward-facing face normal
    yaw = math.degrees(math.atan2(n[1], n[0]))
    return {"x_cm": round(float(x), 1), "y_cm": round(float(y), 1),
            "z_cm": round(float(z), 1), "yaw_deg": round(float(yaw), 1)}


# ──────────────────────────────────────────────────────────────────────────────
#  Several tags → fusion
# ──────────────────────────────────────────────────────────────────────────────
def _circular_mean_deg(angles_deg, weights=None) -> tuple[float, float]:
    """Circular mean + spread of angles in degrees (handles the 0/360 wrap)."""
    a = np.radians(np.asarray(angles_deg, dtype=float))
    w = np.ones_like(a) if weights is None else np.asarray(weights, dtype=float)
    s = np.sum(w * np.sin(a))
    c = np.sum(w * np.cos(a))
    mean = math.degrees(math.atan2(s, c))
    R = math.hypot(s, c) / max(np.sum(w), 1e-9)     # 1 = full agreement
    spread = math.degrees(math.sqrt(max(0.0, -2.0 * math.log(max(R, 1e-9)))))
    return mean, spread


def fuse_poses(estimates: list[PoseEstimate],
               weights: Optional[list] = None) -> Optional[PoseEstimate]:
    """Merges the pose estimates of several tags into a single, more robust pose.

    Position: weighted mean + spread (spread_cm). Heading: circular mean.
    If there is only one estimate, it is returned as is (spread 0).
    """
    if not estimates:
        return None
    if len(estimates) == 1:
        return estimates[0]
    w = np.ones(len(estimates)) if weights is None else np.asarray(weights, float)
    w = w / max(w.sum(), 1e-9)
    xs = np.array([e.x_cm for e in estimates])
    ys = np.array([e.y_cm for e in estimates])
    x = float(np.sum(w * xs))
    y = float(np.sum(w * ys))
    spread_cm = float(math.sqrt(np.sum(w * ((xs - x) ** 2 + (ys - y) ** 2))))
    heading, spread_deg = _circular_mean_deg([e.heading_deg for e in estimates], w)
    # quality: from the spread (smaller spread → higher), rough 0..1 mapping
    quality = float(max(0.0, min(1.0, 1.0 - spread_cm / 50.0)) *
                    max(0.0, min(1.0, 1.0 - spread_deg / 30.0)))
    ids = [tid for e in estimates for tid in e.tag_ids]
    return PoseEstimate(x_cm=x, y_cm=y, heading_deg=heading, n_tags=len(estimates),
                        tag_ids=ids, spread_cm=spread_cm, spread_deg=spread_deg,
                        quality=quality)


def localize(detections: list[dict], tag_map: dict[int, TagWorld],
             extrinsic: np.ndarray,
             quality_weight: bool = True) -> Optional[PoseEstimate]:
    """CORE of the `localize` tool: fused robot pose from a list of detections.

    Args:
        detections: list of dicts, each with keys:
            'tag_id' (int), 'pose_R' (3×3), 'pose_t' (3, in cm),
            optionally 'decision_margin' (float, detection confidence).
        tag_map: {tag_id: TagWorld} — the map (as returned by load_tag_map).
        extrinsic: T_R_C.
        quality_weight: if True, fusion weights by decision_margin and tag
            proximity (near + strong tag → larger weight).

    Returns:
        the fused PoseEstimate, or None if not a single KNOWN tag is visible.
    """
    ests, weights = [], []
    for d in detections:
        tid = int(d["tag_id"])
        tag = tag_map.get(tid)
        if tag is None:
            continue        # unknown tag (not on the map) — skipped
        est = robot_pose_from_detection(d["pose_R"], d["pose_t"], tag, extrinsic)
        ests.append(est)
        if quality_weight:
            margin = float(d.get("decision_margin", 30.0))
            dist = float(np.linalg.norm(np.asarray(d["pose_t"], float))) + 1e-6
            # stronger margin + nearer tag → larger weight
            weights.append((margin / 30.0) * (200.0 / (dist + 200.0)))
        else:
            weights.append(1.0)
    if not ests:
        return None
    return fuse_poses(ests, weights if quality_weight else None)


# ──────────────────────────────────────────────────────────────────────────────
#  Map loading (tags.json — with derived coordinates)
# ──────────────────────────────────────────────────────────────────────────────
def load_tag_sizes(path, default: float = 6.0) -> dict[int, float]:
    """{tag_id: size_cm} from tags.json — the side of the tag's BLACK square in cm.

    This allows tags of mixed sizes (small for near spots, large for far ones).
    Detection must be run with a unit tag_size=1.0, then pose_t multiplied by the
    tag's own size -> cm. Unknown tags get `default`.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[int, float] = {}
    for k, v in data.get("tags", {}).items():
        try:
            out[int(k)] = float(v.get("size_cm", default))
        except (TypeError, ValueError):
            out[int(k)] = default
    return out


def load_tag_map(path) -> dict[int, TagWorld]:
    """{tag_id: TagWorld} map from a tags.json-like file.

    Only includes tags that have NON-null x/y/yaw.
    Accepts both the `x_cm`/`y_cm`/`z_cm`/`yaw_deg` (live tags.json) AND the shorter
    `x`/`y`/`z`/`yaw` (plan template) key names.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[int, TagWorld] = {}
    for k, v in data.get("tags", {}).items():
        x = v.get("x_cm", v.get("x"))
        y = v.get("y_cm", v.get("y"))
        z = v.get("z_cm", v.get("z", 12.0))
        yaw = v.get("yaw_deg", v.get("yaw"))
        if x is None or y is None or yaw is None:
            continue
        out[int(k)] = TagWorld(
            tag_id=int(k), x_cm=float(x), y_cm=float(y), z_cm=float(z),
            yaw_deg=float(yaw), name=v.get("room", v.get("name", "")),
            boundary=bool(v.get("boundary", False)),
            forbidden=bool(v.get("forbidden", "TILT" in str(v.get("room", "")).upper())),
        )
    return out
