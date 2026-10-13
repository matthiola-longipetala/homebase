"""
`scan` tool — several images from one call (a POV sequence of the space).

Idea: do NOT take separate photos while moving (in low light they BLUR
because of the long exposure); instead record VIDEO and extract frames from it.
A video frame's short exposure → SHARP even while driving, and the motion is
continuous and smooth. Proven (rpicam-vid + ffmpeg).

- distance_cm > 0: record VIDEO (rpicam-vid) while rolling forward
  (sonar-guarded), then use ffmpeg to extract `frames` evenly spaced frames
  from the driving part → POV sequence.
- distance_cm <= 0: take `frames` still images in place (rpicam-still) —
  panorama/test mode (does not drive).

LIVE STREAM INTEGRATION: scan starts its own rpicam, which holds the camera
exclusively. If the live stream is running it owns the camera -> at the start of the
scan we make the stream release it TEMPORARILY (pause_camera), the ORIGINAL scan runs
unchanged, then the stream resumes (resume_camera). If the stream is not running,
everything takes the original path (fallback). A stream error must never break the scan.
"""
import glob
import json
import os
import subprocess
import sys
import time
from typing import Optional

from mcp.server.fastmcp import Image

from .status import get_robot
from core.robot_state import mark_chat_active
from core.sensing import read_distance_median

# stream helper (guarded import — the stream is only an ADD-ON)
sys.path.insert(0, os.path.expanduser("~/picarx-mcp/stream"))
try:
    import stream_lib as _stream
except Exception:
    _stream = None


def register_scan_tool(mcp, config):
    calib = config.get("movement_calibration", {})
    cm_per_sec_at_30 = calib.get("forward_cm_per_sec_at_30", 22.0)
    steer_center = calib.get("steering_center_offset", 0)
    safety = config.get("safety", {})
    obstacle_stop_cm = safety.get("obstacle_stop_cm", 20)
    # IMX708 Wide: resolution + LOCKED focus from camera_config.json.
    try:
        with open(os.path.expanduser("~/picarx-mcp/camera_config.json"), encoding="utf-8") as _f:
            _cc = json.load(_f)
    except Exception:
        _cc = {}
    cam = config.get("camera", {})
    width = int(cam.get("width", _cc.get("capture_width", 2304)))
    height = int(cam.get("height", _cc.get("capture_height", 1296)))
    lens_pos = str(_cc.get("lens_position", 1.0))
    # VIDEO: the Pi4 H264 hardware encoder tops out at 1920x1080 — video must NOT use
    # the still resolution (2304 would fall back to software encoding). Full FOV, scaled to 1080p.
    vid_w, vid_h = 1920, 1080

    VID = "/tmp/scan.h264"
    WARMUP_S = 1.0      # rpicam-vid camera warm-up before starting to drive

    def _still(idx):
        path = f"/tmp/scan_{idx}.jpg"
        subprocess.run(
            ["rpicam-still", "-o", path, "--width", str(width), "--height", str(height),
             "--autofocus-mode", "manual", "--lens-position", lens_pos,
             "-t", "250", "-n"],
            check=True, timeout=15,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return Image(path=path)

    def _scan_impl(distance_cm, speed, frames, tilt, steer_trim, shutter_us, gain):
        px = get_robot()
        frames = max(1, min(6, int(frames)))
        px.set_cam_pan_angle(0)
        px.set_cam_tilt_angle(max(-30, min(60, int(tilt))))
        center = steer_trim if steer_trim is not None else steer_center
        px.set_dir_servo_angle(center)
        time.sleep(0.4)

        # --- stationary mode: still images (does not drive) ---
        if distance_cm <= 0:
            imgs = []
            for i in range(frames):
                imgs.append(_still(i))
                time.sleep(0.15)
            return imgs

        # --- moving mode: video + frame extraction ---
        duration_s = distance_cm / cm_per_sec_at_30 * (30 / max(speed, 1))
        rec_ms = int((WARMUP_S + duration_s + 0.6) * 1000)

        # safety check before starting
        d0 = read_distance_median(px, 3)
        if 0 < d0 < obstacle_stop_cm:
            return [_still(0)]   # obstacle ahead → do not start, one still image

        # delete the old frames
        for f in glob.glob("/tmp/scanf*.jpg"):
            try:
                os.remove(f)
            except OSError:
                pass

        # start video recording in the background
        extra = []
        if shutter_us and shutter_us > 0:
            extra += ["--shutter", str(int(shutter_us))]
            # a short shutter needs gain, otherwise it is too dark
            g = gain if gain and gain > 0 else 8.0
            extra += ["--gain", str(g)]
        elif gain and gain > 0:
            extra += ["--gain", str(gain)]
        rec = subprocess.Popen(
            ["rpicam-vid", "-t", str(rec_ms), "--width", str(vid_w), "--height", str(vid_h),
             "--autofocus-mode", "manual", "--lens-position", lens_pos,
             "--codec", "h264", "--framerate", "30", "-o", VID, "-n", *extra],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(WARMUP_S)          # camera warms up before rolling
            px.forward(speed)
            t0 = time.time()
            while time.time() - t0 < duration_s:
                d = read_distance_median(px, 3)
                if 0 < d < obstacle_stop_cm:
                    break
                time.sleep(0.02)
            px.forward(0)
        finally:
            px.forward(0)
            try:
                rec.wait(timeout=max(2, int(rec_ms / 1000) + 3))
            except subprocess.TimeoutExpired:
                rec.terminate()
                try:
                    rec.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    rec.kill()

        # extract frames (3 per second), then pick them evenly
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", "30", "-i", VID,
             "-vf", "fps=3", "-q:v", "3", "/tmp/scanf%03d.jpg"],
            timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        files = sorted(glob.glob("/tmp/scanf*.jpg"))
        if not files:
            return [_still(0)]   # fallback: if no frames were produced, one still image

        # drop roughly the first warm-up frames (we want the driving part)
        warm_drop = min(3, max(0, len(files) - frames))
        usable = files[warm_drop:] or files

        # `frames` evenly spaced frames
        if len(usable) <= frames:
            chosen = usable
        elif frames == 1:
            chosen = [usable[len(usable) // 2]]
        else:
            chosen = [usable[round(k * (len(usable) - 1) / (frames - 1))] for k in range(frames)]

        return [Image(path=f) for f in chosen]

    @mcp.tool()
    def scan(distance_cm: float = 60, speed: int = 30, frames: int = 4,
             tilt: int = 5, steer_trim: Optional[int] = None,
             shutter_us: Optional[int] = None, gain: float = 0):
        """Take several images in one call — a POV sequence of the space.

        Moving mode (distance_cm > 0): records VIDEO while rolling forward and
        extracts `frames` sharp frames from it (a video frame does not blur the way
        a photo does). Sonar-guarded: stops at obstacles.
        Stationary mode (distance_cm <= 0): `frames` still images in place (no driving).

        Args:
            distance_cm: how far to roll forward meanwhile (0 = stand still).
            speed: motor level 0-100 (default 30).
            frames: how many frames to return (1-6, default 4).
            tilt: vertical camera angle in degrees (default 5; higher = more of the room).
            steer_trim: live override of the steering centre (None = config offset).
            shutter_us: fixed shutter time in microseconds (e.g. 8000 = 8 ms) —
                freezes motion in low light (at the cost of a darker/grainier image).
                None = automatic exposure.
            gain: analogue gain (ISO-like); 0 = auto. Useful with a short shutter (e.g. 8).

        Returns:
            The sequence of frames (JPEG) along the way / from the series.
        """
        mark_chat_active()
        # if the live stream is running, make it release the camera temporarily (then back)
        paused = False
        try:
            if _stream is not None and _stream.is_stream_up():
                paused = _stream.pause_camera()
        except Exception:
            paused = False
        try:
            return _scan_impl(distance_cm, speed, frames, tilt, steer_trim,
                              shutter_us, gain)
        finally:
            if paused:
                try:
                    _stream.resume_camera()
                except Exception:
                    pass
