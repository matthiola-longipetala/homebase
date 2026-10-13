"""core/motion.py — motion helpers with an ALWAYS-ON sonar brake.

During every FORWARD MOVE we continuously watch the sonar, and if an
obstacle is closer than the threshold we stop IMMEDIATELY. Previously only pilot.py
braked while driving; the goto_* scripts only checked BETWEEN steps (so the robot could
slide into a wall within a single step). This helper makes the sonar brake the default.
"""
import time

SONAR_STOP_CM = 22.0     # stop immediately for any obstacle closer than this
POLL_S = 0.05            # sample the sonar every 50 ms while driving

def sonar_cm(px):
    """Sonar reading in cm; -1 if faulty / no echo."""
    try:
        d = px.ultrasonic.read()
    except Exception:
        return -1.0
    return d if (d is not None and d > 0) else -1.0

def forward_guarded(px, speed, dur, threshold=SONAR_STOP_CM, poll=POLL_S):
    """Drives forward for 'dur' seconds while continuously watching the sonar; if an
    obstacle is < threshold cm, stops immediately. Returns: (stopped_early: bool, last_cm: float).
    The caller decides what to do with stopped_early (replan / stop / report)."""
    px.forward(speed)
    el = 0.0
    last = sonar_cm(px)
    stopped = False
    while el < dur:
        time.sleep(poll); el += poll
        d = sonar_cm(px)
        if d > 0:
            last = d
        if 0 < d < threshold:
            stopped = True
            break
    px.forward(0)
    return stopped, last

def clear_ahead(px, threshold=SONAR_STOP_CM):
    """True if the way ahead is clear (no obstacle closer than threshold).
    -1 (no echo) counts as clear."""
    d = sonar_cm(px)
    return not (0 < d < threshold)

# A separate robot_hat.Ultrasonic instance on pins D1(Trig)/D0(Echo) (the front sonar
# is px.ultrasonic on D2/D3, untouched). Created lazily (on first use) so that
# forward-only runs do not claim the rear pins.
REAR_STOP_CM = 22.0
_rear = None

def _get_rear():
    global _rear
    if _rear is None:
        from robot_hat import Ultrasonic, Pin
        _rear = Ultrasonic(Pin("D1"), Pin("D0"))   # Ultrasonic(trig, echo)
    return _rear

def rear_cm(samples=3):
    """Median rear-sonar distance in cm; -1.0 if there is no valid echo."""
    try:
        rear = _get_rear()
    except Exception:
        return -1.0
    vals = []
    for _ in range(samples):
        try:
            d = rear.read()
        except Exception:
            d = -1
        if d and d > 0:
            vals.append(d)
        time.sleep(0.01)
    if not vals:
        return -1.0
    vals.sort()
    return vals[len(vals) // 2]

def clear_behind(px=None, threshold=REAR_STOP_CM):
    """True if the way behind is clear. -1 (no echo) counts as clear."""
    d = rear_cm(samples=1)
    return not (0 < d < threshold)

def back_guarded(px, speed, dur, threshold=REAR_STOP_CM, poll=POLL_S):
    """Drives backward for 'dur' seconds while continuously watching the REAR sonar; if
    an obstacle behind is < threshold cm, stops immediately. 'speed' is positive (it is
    negated for reversing). Returns: (stopped_early: bool, last_cm: float) — the forward brake's twin."""
    px.forward(-abs(speed))
    el = 0.0
    last = rear_cm()
    stopped = False
    while el < dur:
        time.sleep(poll); el += poll
        d = rear_cm(samples=1)
        if d > 0:
            last = d
        if 0 < d < threshold:
            stopped = True
            break
    px.forward(0)
    return stopped, last
