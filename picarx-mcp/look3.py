"""look3.py — 3 reference images at pan -30/0/+30 degrees (for route recording: bal30/elore/jobb30).

It turns the camera left/ahead/right with the pan servo (the BODY does not move, only the
camera -> not driving, no permission needed), and saves an image at every angle.
+pan = right (skill convention), so -30 = left, +30 = right.
The file-name suffixes are bal30 = left 30, elore = ahead, jobb30 = right 30 (kept for
compatibility with existing route recordings).

Usage: python3 look3.py <prefix> [--flash]
  e.g. look3.py /tmp/wp0  ->  /tmp/wp0_bal30.jpg, /tmp/wp0_elore.jpg, /tmp/wp0_jobb30.jpg
  --flash: an LED flash for every image (lampa.py, P4!) — for dark corners, robot STANDING.
"""
import os
import sys, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.capture import capture
from picarx import Picarx

args = [a for a in sys.argv[1:] if a != "--flash"]
FLASH = "--flash" in sys.argv[1:]
prefix = args[0] if args else "/tmp/look"

if FLASH:
    try:
        from lampa import be as lampa_be, ki as lampa_ki
    except Exception as e:
        print(f"(--flash requested, but the lamp is not available: {e} — continuing without a flash)")
        FLASH = False

px = Picarx()
px.set_cam_tilt_angle(0); time.sleep(0.2)
for pan, name in [(-30, "bal30"), (0, "elore"), (30, "jobb30")]:
    px.set_cam_pan_angle(pan); time.sleep(0.5)
    if FLASH:
        lampa_be(100); time.sleep(0.15)   # light up + a moment for auto-exposure
    capture(f"{prefix}_{name}.jpg", w=640, h=480)
    if FLASH:
        lampa_ki()
    print(f"saved: {prefix}_{name}.jpg (pan {pan}{', FLASH' if FLASH else ''})")
px.set_cam_pan_angle(0)
print("done")
