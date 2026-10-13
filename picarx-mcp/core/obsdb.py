"""obsdb.py — observation database for ONLINE refinement of the tag map.

During normal use we collect where each tag is seen from: whenever the robot sees a
tag from a known pose, we log the tag's IMPLIED world pose. Later refine_tags.py
computes a weighted median from many such observations -> the map refines itself.

THE CAVEAT: if the robot pose came ONLY from the tag being observed (n_tags==1, same tag),
the observation is circular (it just reproduces the map value). So every row records
the pose source (src), and the refinement uses the NON-circular rows (anchored on other/several tags).
"""
import os
import json, time
import numpy as np
from pathlib import Path
from .localization import tag_world_from_robot

DEFAULT_DB = os.path.expanduser("~/picarx-mcp/tag_observations.jsonl")


def record(robot_pose, dets, extrinsic, source="", path=DEFAULT_DB):
    """Logs the implied world pose of every detected tag, given the robot pose.

    Args:
        robot_pose: PoseEstimate (result of localize) — x_cm,y_cm,heading_deg,n_tags,tag_ids
        dets: the list used for localize; pose_t ALREADY in cm (size-scaled)
        extrinsic: T_R_C (with the pan/tilt used)
        source: free-form label (e.g. "goto_scan")
    Returns: number of rows written.
    """
    src_ids = list(getattr(robot_pose, "tag_ids", []) or [])
    lines = []
    for d in dets:
        pt = np.asarray(d["pose_t"], float).ravel()
        dist = float(np.linalg.norm(pt))
        w = tag_world_from_robot(robot_pose.x_cm, robot_pose.y_cm, robot_pose.heading_deg,
                                 np.asarray(d["pose_R"]), pt, extrinsic)
        tid = int(d["tag_id"])
        lines.append({
            "t": round(time.time(), 1), "src": source,
            "tag": tid,
            "x": w["x_cm"], "y": w["y_cm"], "z": w["z_cm"], "yaw": w["yaw_deg"],
            "rx": round(float(robot_pose.x_cm), 1), "ry": round(float(robot_pose.y_cm), 1),
            "rh": round(float(robot_pose.heading_deg), 1),
            "dist": round(dist, 1), "margin": round(float(d.get("decision_margin", 0)), 1),
            "pose_src": src_ids, "n_src": int(getattr(robot_pose, "n_tags", 1)),
            # circular: the pose came from a SINGLE tag, and it is this same one
            "circular": (int(getattr(robot_pose, "n_tags", 1)) == 1 and src_ids == [tid]),
        })
    if lines:
        with open(path, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(json.dumps(ln, ensure_ascii=False) + "\n")
    return len(lines)
