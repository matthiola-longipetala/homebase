"""
`look` tool — camera image, with optional look-around (phase 2).

This is what the agent sees in physical space. The image comes back directly as
an MCP image (no more SSH+scp detour). The camera head can be turned with the
pan/tilt servos (P0 = pan, P1 = tilt), so the robot can look around without moving.

LIVE STREAM INTEGRATION: if the live video stream is running (active mode), a
FORWARD look (pan=tilt=0) IMMEDIATELY returns the stream's latest frame
(~5 ms, no per-call warmup). If it has to look around (pan/tilt != 0), the stream
temporarily releases the camera, the original rpicam-still runs, then the stream resumes.
If the stream is NOT running, everything takes the ORIGINAL path (fallback) — behaviour
is unchanged. On any stream error it also falls back to the original path.
"""
import os
import json
import subprocess
import sys
import time

from mcp.server.fastmcp import Image

from .status import get_robot
from core.robot_state import mark_chat_active

# stream helper (guarded import — the stream is only an ADD-ON, it must never break the server)
sys.path.insert(0, os.path.expanduser("~/picarx-mcp/stream"))
try:
    import stream_lib as _stream
except Exception:
    _stream = None


def register_look_tool(mcp, config):
    # IMX708 Wide: the canonical place for resolution + LOCKED focus is
    # camera_config.json — the server config's "camera" section is only an override.
    try:
        with open(os.path.expanduser("~/picarx-mcp/camera_config.json"), encoding="utf-8") as _f:
            _cc = json.load(_f)
    except Exception:
        _cc = {}
    cam = config.get("camera", {})
    width = int(cam.get("width", _cc.get("capture_width", 2304)))
    height = int(cam.get("height", _cc.get("capture_height", 1296)))
    lens_pos = str(_cc.get("lens_position", 1.0))

    def _rpicam_still(path, pan, tilt):
        """The ORIGINAL capture path: set the servos + rpicam-still."""
        px = get_robot()
        px.set_cam_pan_angle(pan)
        px.set_cam_tilt_angle(tilt)
        time.sleep(0.5)   # let the head settle
        subprocess.run(
            ["rpicam-still", "-o", path,
             "--width", str(width), "--height", str(height),
             "--autofocus-mode", "manual", "--lens-position", lens_pos,
             "-t", "600", "-n"],
            check=True, timeout=20,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    @mcp.tool()
    def look(pan: int = 0, tilt: int = 0) -> Image:
        """Take a photo with the robot's camera — what the robot currently sees.

        Optionally look around: the camera head is turned before the shot
        (the robot body does NOT move, only the "head").

        Args:
            pan: horizontal camera angle in degrees (-60..60; + = right). 0 = straight ahead.
            tilt: vertical camera angle in degrees (-30..60; + = up). 0 = horizontal.

        Returns:
            The snapshot (JPEG).
        """
        mark_chat_active()
        pan = max(-60, min(60, int(pan)))
        tilt = max(-30, min(60, int(tilt)))
        path = "/tmp/look.jpg"

        # --- FAST PATH: stream running + looking forward -> fresh frame from the stream ---
        try:
            if _stream is not None and pan == 0 and tilt == 0 and _stream.is_stream_up():
                if _stream.grab_latest(path):
                    return Image(path=path)
        except Exception:
            pass   # any problem -> original path

        # --- Stream running but looking around: temporary camera release + original path ---
        paused = False
        try:
            if _stream is not None and _stream.is_stream_up():
                paused = _stream.pause_camera()
        except Exception:
            paused = False
        try:
            _rpicam_still(path, pan, tilt)
        finally:
            if paused:
                try:
                    _stream.resume_camera()
                except Exception:
                    pass
        return Image(path=path)
