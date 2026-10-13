"""capture_perception_test.py — real image capture for the perception test.

Moves ONLY the CAMERA servos (pan/tilt) + takes photos. It NEVER starts the WHEELS
(drive motor) → the robot stays in place (owner's permission: "anything that only
moves the camera keeps the robot in place"). Runs with the system Python
(robot_hat lives in the system Python), NOT from the venv.

Output: /tmp/ptest_0.jpg (left), _1 (center), _2 (right).
"""
import os
import sys, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.capture import capture
from picarx import Picarx

# Usage: python3 capture_perception_test.py [tilt_deg]   (default tilt=22, upward)
tilt = float(sys.argv[1]) if len(sys.argv) > 1 else 22.0
pans = [-50, -25, 0, 25, 50]   # wider sweep for looking around (+pan=right)

px = Picarx()
# safety: NO driving. Camera servos only. (We never call forward()/backward().)
ANGLES = [(p, tilt) for p in pans]   # (pan_deg, tilt_deg); +tilt=upward
try:
    for i, (pan, tilt) in enumerate(ANGLES):
        px.set_cam_pan_angle(pan)
        px.set_cam_tilt_angle(tilt)
        time.sleep(0.6)
        capture(f"/tmp/ptest_{i}.jpg", w=640, h=480)
        print(f"image {i}: pan={pan} tilt={tilt} -> /tmp/ptest_{i}.jpg")
finally:
    px.set_cam_pan_angle(0)
    px.set_cam_tilt_angle(0)
print("DONE: 3 images, robot stayed in place (only the camera servos moved).")
