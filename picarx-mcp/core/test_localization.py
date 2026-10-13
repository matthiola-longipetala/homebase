"""
test_localization.py — OFFLINE self-consistency test of the pose estimation.

Verifies WITHOUT the robot that the world-frame algebra of `localization.py` is correct:
from a KNOWN robot pose a FORWARD model computes what the detector would "see"
(T_C_tag = pose_R, pose_t), this is fed back into `localize`, and we check
that the original pose comes back.

This does NOT verify the actual pupil_apriltags convention (that has to be done on the
robot with a known tag — see the Z_OUT_OF_TAG flag). It DOES verify: the maths of the
inverse transform chain is flawless, multi-tag fusion reduces noise, and the
map loader is correct. Run:  python3 core/test_localization.py
"""
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import localization as L


# ── FORWARD model: "detector output" from a known robot pose ──────────────────
def robot_T(x_cm, y_cm, heading_deg):
    return L.make_T(L.rot_z(math.radians(heading_deg)), [x_cm, y_cm, 0.0])


def synth_detection(tag: L.TagWorld, robot_x, robot_y, robot_heading,
                    extrinsic, z_out_of_tag=L.Z_OUT_OF_TAG):
    """Perfect (noise-free) detection of a tag from the given robot pose.

    T_C_tag = (T_W_R · T_R_C)⁻¹ · T_W_tag
    """
    T_W_R = robot_T(robot_x, robot_y, robot_heading)
    T_W_tag = L.tag_world_transform(tag.x_cm, tag.y_cm, tag.z_cm, tag.yaw_deg,
                                    z_out_of_tag=z_out_of_tag)
    T_C_tag = L.invert_T(T_W_R @ extrinsic) @ T_W_tag
    return {"tag_id": tag.tag_id, "pose_R": T_C_tag[:3, :3].copy(),
            "pose_t": T_C_tag[:3, 3].copy(), "decision_margin": 40.0}


def small_rot_noise(sigma_deg):
    """Small, valid (orthonormal) rotation noise about a random axis."""
    axis = np.random.randn(3)
    axis /= np.linalg.norm(axis)
    ang = math.radians(np.random.randn() * sigma_deg)
    K = np.array([[0, -axis[2], axis[1]],
                  [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * (K @ K)


def ang_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}   {detail}")


# ── Test map: tags of the INVENTED example flat (navigacio/tags_template.json) ──
EXTR = L.camera_extrinsic(pan_deg=0, tilt_deg=0, cam_height_cm=10, cam_forward_cm=6)
TAGS = {
    1: L.TagWorld(1, x_cm=0,   y_cm=-100, z_cm=14, yaw_deg=90,  name="front_door"),        # south end of hallway, looks up the hallway (+Y)
    2: L.TagWorld(2, x_cm=0,   y_cm=500,  z_cm=14, yaw_deg=270, name="hallway_north_end"), # north end, looks back (−Y)
    3: L.TagWorld(3, x_cm=-50, y_cm=220,  z_cm=14, yaw_deg=0,   name="living_room_door"),  # hallway west wall, looks right (+X)
    6: L.TagWorld(6, x_cm=50,  y_cm=260,  z_cm=14, yaw_deg=180, name="bedroom_door"),      # hallway east wall, looks left (−X)
}


def test_single_tag_roundtrip():
    print("\n[1] Single-tag round-trip (noise-free) — several poses, several tags")
    poses = [(0, 0, 90), (0, 100, 90), (30, 200, 60), (-20, 150, 120),
             (0, 350, -90), (40, 50, 0), (-40, 250, 170)]
    worst = 0.0
    for (x, y, h) in poses:
        for tag in TAGS.values():
            det = synth_detection(tag, x, y, h, EXTR)
            est = L.robot_pose_from_detection(det["pose_R"], det["pose_t"], tag, EXTR)
            ep = math.hypot(est.x_cm - x, est.y_cm - y)
            eh = ang_diff(est.heading_deg, h)
            worst = max(worst, ep, eh)
            ok = ep < 1e-6 and eh < 1e-6
            if not ok:
                check(f"pose({x},{y},{h}) tag{tag.tag_id}", False,
                      f"Δpos={ep:.2e} Δhead={eh:.2e}")
    check("every noise-free round-trip is exact (<1e-6)", worst < 1e-6,
          f"worst error={worst:.2e}")


def test_localize_fusion_noisefree():
    print("\n[2] localize() fusion — all tags visible, noise-free")
    x, y, h = 0, 180, 90
    dets = [synth_detection(t, x, y, h, EXTR) for t in TAGS.values()]
    est = L.localize(dets, TAGS, EXTR)
    check("fusion of 4 tags returns the pose", est is not None and
          math.hypot(est.x_cm - x, est.y_cm - y) < 1e-6 and
          ang_diff(est.heading_deg, h) < 1e-6,
          f"{est.as_dict() if est else None}")
    check("spread ~0 when noise-free", est.spread_cm < 1e-6 and est.spread_deg < 1e-6)
    check("n_tags==4", est.n_tags == 4)


def test_unknown_tag_ignored():
    print("\n[3] Unknown tag is skipped")
    x, y, h = 0, 180, 90
    dets = [synth_detection(TAGS[1], x, y, h, EXTR)]
    dets.append({"tag_id": 99, "pose_R": np.eye(3), "pose_t": np.array([0, 0, 100.0])})
    est = L.localize(dets, TAGS, EXTR)
    check("unknown tag (id99) does not spoil it", est is not None and
          est.n_tags == 1 and math.hypot(est.x_cm - x, est.y_cm - y) < 1e-6)
    est_none = L.localize([{"tag_id": 99, "pose_R": np.eye(3),
                            "pose_t": np.array([0, 0, 100.0])}], TAGS, EXTR)
    check("only unknown tags → None", est_none is None)


def test_noise_robustness():
    print("\n[4] Noise robustness + fusion improves on single tags")
    np.random.seed(42)
    x, y, h = 0, 180, 90
    N = 300
    single_errs, fused_errs = [], []
    for _ in range(N):
        dets = []
        for t in TAGS.values():
            d = synth_detection(t, x, y, h, EXTR)
            d["pose_t"] = d["pose_t"] + np.random.randn(3) * 1.5      # ~1.5 cm noise
            d["pose_R"] = small_rot_noise(1.0) @ d["pose_R"]          # ~1° noise
            dets.append(d)
        # single-tag errors
        for d in dets:
            e = L.robot_pose_from_detection(d["pose_R"], d["pose_t"], TAGS[d["tag_id"]], EXTR)
            single_errs.append(math.hypot(e.x_cm - x, e.y_cm - y))
        # fusion error
        ef = L.localize(dets, TAGS, EXTR)
        fused_errs.append(math.hypot(ef.x_cm - x, ef.y_cm - y))
    sm = float(np.mean(single_errs))
    fm = float(np.mean(fused_errs))
    check(f"single-tag mean error is small (<8 cm): {sm:.2f} cm", sm < 8.0)
    check(f"fusion mean error (<6 cm): {fm:.2f} cm", fm < 6.0)
    check(f"fusion better than the single-tag mean ({fm:.2f} < {sm:.2f})", fm < sm)


def test_convention_flag_closes():
    print("\n[5] The convention flag closes in both settings (algebra independence)")
    # If the FORWARD model and the inverse use the SAME tag-z convention, the
    # round-trip is exact even when the flag is flipped — verified by calling
    # tag_world_transform with an explicit flag on both sides.
    x, y, h = 10, 150, 80
    tag = TAGS[3]
    for flag in (True, False):
        T_W_R = robot_T(x, y, h)
        T_W_tag = L.tag_world_transform(tag.x_cm, tag.y_cm, tag.z_cm, tag.yaw_deg,
                                        z_out_of_tag=flag)
        T_C_tag = L.invert_T(T_W_R @ EXTR) @ T_W_tag
        # inverse with the same flag
        T_W_R2 = T_W_tag @ L.invert_T(EXTR @ T_C_tag)
        ep = math.hypot(T_W_R2[0, 3] - x, T_W_R2[1, 3] - y)
        check(f"flag={flag}: round-trip closes", ep < 1e-6, f"Δ={ep:.2e}")


def test_load_tag_map():
    print("\n[6] load_tag_map — live tags.json schema + null filtering")
    sample = {
        "tags": {
            "1": {"room": "front door", "x_cm": 0, "y_cm": -100, "z_cm": 14, "yaw_deg": 90},
            "5": {"room": "bathroom (TILTOTT)", "x_cm": 275, "y_cm": -100, "z_cm": 14,
                  "yaw_deg": 90, "forbidden": True},
            "9": {"room": "not yet", "x_cm": None, "y_cm": None, "z_cm": None,
                  "yaw_deg": None},        # incomplete → must be skipped
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        import json
        json.dump(sample, f)
        p = f.name
    m = L.load_tag_map(p)
    check("only complete tags are loaded (1 and 5, not 9)", set(m.keys()) == {1, 5})
    check("bathroom forbidden=True", m[5].forbidden is True)
    check("yaw read correctly", abs(m[1].yaw_deg - 90) < 1e-9)
    Path(p).unlink(missing_ok=True)


if __name__ == "__main__":
    print("=" * 70)
    print("localization.py — offline self-consistency test")
    print("=" * 70)
    test_single_tag_roundtrip()
    test_localize_fusion_noisefree()
    test_unknown_tag_ignored()
    test_noise_robustness()
    test_convention_flag_closes()
    test_load_tag_map()
    print("\n" + "=" * 70)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 70)
    sys.exit(1 if FAIL else 0)
