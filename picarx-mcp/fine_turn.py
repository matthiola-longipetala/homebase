#!/usr/bin/env python3
# fine_turn.py <left|right> <deg>
# SMALL-ANGLE turn in place with mini K-manoeuvres (~5 degrees / pulse).
# BELOW turn90.py (which is at least ~22 degrees/cycle) — for fine heading correction.
# Open loop (no camera) -> verify the bearing with face_tag.py AFTERWARDS!
#
# Mechanics: one pulse = a short FORWARD arc (steering towards the turn) + a short
# BACKWARD arc (counter-steering). Both give the SAME rotation direction (like the K-turn),
# but the two legs cancel the displacement -> it rotates ~in place, by a small angle.
import sys, time
from picarx import Picarx

UNIT_DEG = 5.0   # degrees / pulse (measured)
SPD = 35
LEG = 0.13       # seconds / leg

def read_sonar(px):
    for fn in ('get_distance',):
        try:
            return float(getattr(px, fn)())
        except Exception:
            pass
    try:
        return float(px.ultrasonic.read())
    except Exception:
        return -1.0

def main():
    if len(sys.argv) < 3:
        print("usage: fine_turn.py <left|right> <deg>")
        return
    direction = sys.argv[1].lower()
    try:
        deg = abs(float(sys.argv[2]))
    except ValueError:
        print("the degrees must be a number")
        return
    if direction not in ('left', 'right'):
        print("direction: left or right")
        return

    px = Picarx()
    n = max(1, int(round(deg / UNIT_DEG)))
    # left = CCW: FORWARD steer -30, BACKWARD steer +30 ; right = CW the other way round
    s = -30 if direction == 'left' else 30
    print("FINE_TURN: %s ~%d deg target = %d pulses x ~%g deg" % (direction, round(deg), n, UNIT_DEG))
    try:
        # WIGGLE: at small angles the servo does not always take the command,
        for w in (20, -20, 0):
            px.set_dir_servo_angle(w); time.sleep(0.25)
        for i in range(n):
            d = read_sonar(px)
            px.set_dir_servo_angle(s); time.sleep(0.2)
            if d < 0 or d > 12:
                px.forward(SPD); time.sleep(LEG); px.stop()
            else:
                print("  [%d] sonar %.0f cm -> forward leg skipped (obstacle), backward only" % (i + 1, d))
            time.sleep(0.2)
            px.set_dir_servo_angle(-s); time.sleep(0.2)
            px.backward(SPD); time.sleep(LEG); px.stop()
            time.sleep(0.2)
            px.set_dir_servo_angle(0); time.sleep(0.15)
            print("  [%d/%d] ok" % (i + 1, n))
        print("DONE: ~%d deg %s turned. Verify: face_tag.py <id>" % (round(n * UNIT_DEG), direction))
    finally:
        px.set_dir_servo_angle(0)
        px.stop()

if __name__ == '__main__':
    main()
