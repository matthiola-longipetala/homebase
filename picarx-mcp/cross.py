"""cross.py — SMART THRESHOLD CROSSING: the decision is based on "DO I SEE AN OBSTACLE IN THE IMAGE".

Key principle: up close you CANNOT see the threshold (it is low and falls out of the image) ->
do NOT try to decide "is it a threshold". Instead: if there is NO OBSTACLE IN THE IMAGE
(wall/furniture/cat/person in the path), then the sonar's close-range signal is just the
threshold/door jamb -> YOU MAY GO. This way the brake stays active on open legs (it stops at a
real obstacle), but you cross the threshold -- and you see the cat/wall IN ADVANCE instead of
bumping into it with a test push.

TWO STEPS (look-before-cross), a PERCEIVER decides based on the image:
  1) cross.py <cm> [speed]      -> drives with the brake; if the brake stops, it TAKES A PICTURE
                                   (/tmp/cross.jpg) and EXITS with a "DECISION NEEDED" message +
                                   the remaining distance. It does NOT force it.
  2) the PERCEIVER looks at the image (now: the agent on the laptop; later: a perception model):
       - if there is NO obstacle in the path -> cross.py --go <remaining_cm> [speed]  (brake OFF, across with momentum)
       - if there IS an obstacle -> do NOT call --go; stop / go around.

So "switching the brake off" is NEVER blind: it is always preceded by an image-based obstacle check.
In autonomous operation the laptop-side navigator runs the perception model on /tmp/cross.jpg.

Usage:
    python3 cross.py 80 40        # drives 80 with the brake; if it stops, saves a picture and reports
    python3 cross.py 30 50 --go   # brake OFF: 30 cm across with momentum (ONLY after the perceiver's OK!)
"""
import os
import sys, time
sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
from core.motion import forward_guarded, sonar_cm
from core.capture import capture
from picarx import Picarx

V = 35.0             # rough velocity model (cm/s)
CHUNK_S = 1.5

raw = sys.argv[1:]
GO = "--go" in raw                      # brake-OFF crossing (the perceiver has already approved)
args = [a for a in raw if a != "--go" and a != "--"]
dist_cm = float(args[0]) if args else 60.0
speed = int(args[1]) if len(args) > 1 else 40

px = Picarx()
for a in (15, -15, 0):
    px.set_dir_servo_angle(a); time.sleep(0.18)
px.set_dir_servo_angle(0); time.sleep(0.15)

if GO:
    # BRAKE OFF: the perceiver has looked at the image, no obstacle -> across with momentum.
    print(f"CROSS --go: BRAKE OFF, {dist_cm:.0f} cm @speed {speed} (the perceiver has approved the image!)")
    t = dist_cm / V
    done = 0.0
    try:
        while done < t:
            seg = min(CHUNK_S, t - done)
            px.forward(speed); time.sleep(seg); px.forward(0)
            done += seg
    finally:
        px.forward(0); px.set_dir_servo_angle(0)
    print(f"DONE: ~{done*V:.0f} cm crossing; sonar now {sonar_cm(px):.0f}cm")
    print("done")
    sys.exit(0)

# DEFAULT MODE: drive with the brake; if it stops, save a picture and report (do NOT force).
print(f"CROSS: target {dist_cm:.0f} cm @speed {speed} (with brake; on a stop: picture + decision request)")
remaining = dist_cm
done_cm = 0.0
tripped = False
try:
    while remaining > 2:
        seg_s = min(CHUNK_S, remaining / V)
        stopped, last = forward_guarded(px, speed, seg_s)
        moved = (seg_s if not stopped else seg_s * 0.5) * V
        done_cm += moved; remaining -= moved
        if stopped:
            tripped = True
            capture("/tmp/cross.jpg", w=640, h=480)
            break
finally:
    px.forward(0); px.set_dir_servo_angle(0)

if tripped:
    print(f"BRAKE @ {last:.0f}cm (travelled ~{done_cm:.0f}cm, remaining ~{remaining:.0f}cm).")
    print(f"DECISION NEEDED: look at /tmp/cross.jpg -> if there is NO obstacle in the path:")
    print(f"   python3 cross.py {max(20,remaining):.0f} {speed} --go")
    print("   if there IS an obstacle (wall/furniture/cat/person) -> NO --go; go around/stop.")
else:
    print(f"DONE: ~{done_cm:.0f} cm travelled (the brake did not stop); sonar now {sonar_cm(px):.0f}cm")
print("done")
