"""core/capture.py — auto-calibrated, ADAPTIVE-exposure image capture.

The built-in rpicam auto-exposure underexposes (~33/255) and ignores --ev,
so we use manual exposure. A single fixed setting cannot cover the extremes
either (dark under-the-bed vs. sunny hallway is a ~215x difference), therefore:

  - ITERATIVE convergence to the target brightness: take a frame, measure the mean,
    scale the 'exposure' value (E = gain*shutter product) by the error, repeat.
  - Gain is adjusted TOO: in the dark high gain + long shutter, in sunlight low gain + short.

Interface (unchanged for callers): capture(path, w, h, shutter_us=None, target).
The 'shutter_us' parameter now means the cached EXPOSURE value (E):
None -> converges from cold by iterating (2-3 frames); if an E is given -> one quick
frame with it (reused within a sweep). Returns the E that was used.

"DARK-PEEK" MODE:
  - The old behaviour SILENTLY accepted the ceiling (gain16/330ms) and returned an
    underexposed image without any warning (a silent failure).
    NOW: if we are stuck at the ceiling and the image is still dark, a WARNING
    goes to stderr (stdout is left clean so the callers' parsers are protected).
  - capture(..., mode="dark"): with the robot standing still the shutter can be
    stretched to seconds — E_HI up to 32M (gain 8 @ 4 s), preferring SHUTTER over
    gain (less noise). Only use while stationary (motion blur!).
"""
import os
import json
import subprocess
import sys

import numpy as np
from PIL import Image

# Camera config (IMX708 Wide): resolution + LOCKED focus.
# Passing lens_position is MANDATORY on every capture — without it the autofocus
# moves the lens and fx/fy (distance estimation) silently drifts!
try:
    with open(os.path.expanduser("~/picarx-mcp/camera_config.json"), encoding="utf-8") as _f:
        _CAM = json.load(_f)
except Exception:
    _CAM = {}
CAM_W = int(_CAM.get("capture_width", 2304))
CAM_H = int(_CAM.get("capture_height", 1296))
LENS_POS = _CAM.get("lens_position", 1.0)

# Live stream integration: if the stream is running, it owns the camera.
# capture() uses a self-expiring "lease" to make the stream release the camera for an
# rpicam-still (the supervisor treats a fresh lease as PAUSE); after the sweep the
# stream restores itself. Guarded import — without stream_lib everything takes the OLD path.
try:
    sys.path.insert(0, os.path.expanduser("~/picarx-mcp/stream"))
    import stream_lib as _stream
except Exception:
    _stream = None

TARGET = 105.0
E_START = 60000.0                 # initial exposure guess (gain*us) when nothing is cached
E_LO, E_HI = 100.0, 5_280_000.0   # gain1/0.1ms .. gain16/330ms (a floor of 1500 blew out on a sunny terrace, mean 250+)

# "Dark-peek" mode: with the robot stationary the shutter can be stretched.
# E_HI_DARK = gain 8 @ 4 s; the formula stretches the SHUTTER first (max 4 s, gain=1),
# and only raises gain up to 8 if that is not enough — the least noisy path for a still photo.
E_HI_DARK = 32_000_000.0
SHUTTER_MAX_DARK = 4_000_000.0

ACCEPT = (0.72, 1.45)             # if target/mean is within this band -> done
MAX_IT = 4

def _expo_to_gs(E):
    """E (gain*us product) -> (gain, shutter_us). Aims the shutter around ~30 ms,
    the remainder is carried by gain (1..16)."""
    E = min(E_HI, max(E_LO, E))
    gain = min(16.0, max(1.0, E / 30000.0))
    shutter = min(330000.0, max(100.0, E / gain))
    return gain, shutter

def _expo_to_gs_dark(E):
    """Dark variant: stretches the SHUTTER first (max 4 s, gain=1), only then
    raises gain (1..8). Only with a STATIONARY robot — sensitive to motion."""
    E = min(E_HI_DARK, max(E_LO, E))
    if E <= SHUTTER_MAX_DARK:
        return 1.0, max(1500.0, E)
    gain = min(8.0, E / SHUTTER_MAX_DARK)
    return gain, SHUTTER_MAX_DARK

def _grab(path, w, h, gain, shutter_us, settle_ms):
    subprocess.run(
        ["rpicam-still", "-o", path, "--width", str(w), "--height", str(h),
         "-t", str(int(settle_ms)), "-n", "--awb", "auto",
         "--autofocus-mode", "manual", "--lens-position", str(LENS_POS),
         "--gain", f"{gain:.2f}", "--shutter", str(int(shutter_us))],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _mean(path):
    return float(np.asarray(Image.open(path).convert("L")).mean())

def capture(path, w=None, h=None, shutter_us=None, target=TARGET, mode="normal"):
    """Takes a well-exposed image to path, in any lighting. Returns the exposure
    value (E) used, which the caller can cache to speed up a sweep.

    mode="dark": dark-peek — long shutter (max gain8 @ 4s), ONLY with a stationary robot.

    IMX708 Wide: the resolution is owned by camera_config.json — the callers'
    legacy w=1024,h=576 parameters are OVERRIDDEN so calibration and images stay
    consistent (the old values belonged to the previous camera)."""
    if w in (None, 1024) and h in (None, 576):
        w, h = CAM_W, CAM_H
    dark = (mode == "dark")
    e_hi = E_HI_DARK if dark else E_HI
    expo_fn = _expo_to_gs_dark if dark else _expo_to_gs
    E = float(shutter_us) if shutter_us else E_START
    reuse = bool(shutter_us)
    iters = 1 if reuse else MAX_IT
    # the stream (if running) releases the camera for this capture burst
    if _stream is not None:
        try:
            _stream.acquire_for_capture()
        except Exception:
            pass
    m = None
    for _ in range(iters):
        if _stream is not None:
            try:
                _stream.lease_refresh()   # keep the lease alive during the sweep
            except Exception:
                pass
        g, sh = expo_fn(E)
        _grab(path, w, h, g, sh, 250)
        if reuse:
            break
        m = max(_mean(path), 1.0)
        ratio = target / m
        if m >= 240:            # saturated image (e.g. sunny terrace) -> big step down
            ratio = min(ratio, 0.12)
        if ACCEPT[0] <= ratio <= ACCEPT[1]:
            break
        E = min(e_hi, max(E_LO, E * ratio))
    # SILENT-CEILING GUARD: if we got stuck at the ceiling and the image is still
    # dark, the caller previously never found out. Goes to stderr (stdout parsers protected).
    if (not reuse) and m is not None and m < target * 0.6 and E >= e_hi * 0.999:
        print(f"[capture] WARNING: exposure CEILING reached (mode={mode}, E={E:.0f}), "
              f"image is UNDEREXPOSED (mean={m:.0f}, target={target:.0f})"
              + ("" if dark else " — with a stationary robot try: mode='dark'"),
              file=sys.stderr)
    return E
