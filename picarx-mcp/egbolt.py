# -*- coding: utf-8 -*-
"""egbolt.py — night-sky picture: camera tilt + dark mode.

ONLY WITH THE ROBOT STANDING STILL (the dark-mode shutter time is in seconds — motion blurs it).
The body does not move, only the camera servo -> not driving.

Usage: python3 egbolt.py <tilt_deg> <output.jpg> [pan_deg]
  tilt: -30..+60 (+ is up; moon ~35, stars ~60)
  pan : -90..+90 (0 = ahead)
"""
import os
import sys, time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.capture import capture
from picarx import Picarx

tilt = int(sys.argv[1]) if len(sys.argv) > 1 else 60
path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/eg.jpg"
pan = int(sys.argv[3]) if len(sys.argv) > 3 else 0

tilt = max(-30, min(60, tilt))
pan = max(-90, min(90, pan))

px = Picarx()
px.set_cam_pan_angle(pan)
px.set_cam_tilt_angle(tilt)
time.sleep(0.9)                      # let the servo settle before the shutter opens

E = capture(path, mode="dark")
print("tilt=%d pan=%d -> %s (E=%s)" % (tilt, pan, path, E))
