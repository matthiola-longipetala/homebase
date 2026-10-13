"""core/motor_balance.py — speed BALANCING of the two rear motors.

## Why it is needed (how it was found)
After an SD-card rebuild, every long run curved to the right and the segments covered only
half of the requested distance. The investigation ruled out/fixed two software bugs
(frozen IMU anchor, steering angle written in small steps); then, holding the robot with
the wheels spinning in the air, a human made the diagnosis: **the RIGHT rear wheel
spins slower** — even when plugged into the charger, so it is not a voltage issue.

This single cause explains everything:
  - it curves right regardless of steering (the faster left wheel pushes it around),
  - steering right adds up (+44°), steering left they CANCEL out (−1.2°),
  - progress is slower → "half the distance".

⚠️ This is an INTERMITTENT phenomenon: an earlier teleop session had already spotted the
uneven wheel with a ruler; back then the built-in differential (inner wheel 75% power at 25°)
was the explanation — which is TRUE but NOT COMPLETE: the differential has no effect with
straight steering, yet the phenomenon is present there too. It then worked fine for a while,
and later came back. That is why this module is NOT a hard-coded number but a re-measurable
correction loaded from JSON.

## How it acts (picarx 2.0.5 internals)
`Picarx.set_motor_speed(motor, speed)` computes:
    speed = int(speed/2) + 50          # 45 → 72 (PWM %)
    speed = speed - cali_speed_value[motor]
so `cali_speed_value` is SUBTRACTED: a positive value SLOWS DOWN, a negative one SPEEDS UP.
Index: 0 = LEFT rear, 1 = RIGHT rear.

The stock `motor_speed_calibration()` is BROKEN (it overwrites the list with a scalar, then
tries to index it → TypeError), so we set `cali_speed_value` directly.

## Usage
    from core.motor_balance import apply
    px = Picarx()
    apply(px)        # balancing per the JSON; no file or zero values: no-op

Measuring/tuning: `motor_kalib.py` (measures straight-line drift with the IMU and suggests a value).
The JSON: `motor_balance.json` in the picarx-mcp root.
"""
import json
import os

BALANCE_PATH = os.path.expanduser("~/picarx-mcp/motor_balance.json")

# Safety limit: no larger correction than this many PWM points is applied automatically
# (the drive runs around 72 PWM% at speed 45 — 20 is already a very large deviation,
#  that points to a mechanical fault: inspect it by hand instead of masking it).
MAX_CORR = 20


def load():
    """Reads the balancing. Returns: (cali_list, info_dict).

    cali_list is in picarx `cali_speed_value` format: [left, right], where a POSITIVE
    value slows that motor down. Missing/invalid file -> [0, 0] (no-op)."""
    try:
        with open(BALANCE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        # Two accepted formats: {"bal": N, "jobb": N, ...} OR a plain [left, right] list.
        # (A plain [0, 0] list once crashed the dict-reading code; if there is no correction,
        #  drift is handled by go_heading's heading hold — on error a silent no-op.)
        if isinstance(d, (list, tuple)):
            bal, jobb = int(d[0]), int(d[1])
            merve, megj = "?", "list format"
        else:
            bal = int(d.get("bal", 0))
            jobb = int(d.get("jobb", 0))
            merve, megj = d.get("merve", "?"), d.get("megjegyzes", "")
    except Exception:
        return [0, 0], {"forras": "missing/invalid file (no-op)"}

    bal = max(-MAX_CORR, min(MAX_CORR, bal))
    jobb = max(-MAX_CORR, min(MAX_CORR, jobb))
    return [bal, jobb], {
        "forras": BALANCE_PATH,
        "merve": merve,
        "megjegyzes": megj,
    }


def apply(px, verbose=True):
    """Applies the balancing to the Picarx instance. Returns: the list that was set."""
    cali, info = load()
    try:
        px.cali_speed_value = list(cali)
        _wrap_stop_safe(px, cali[0], cali[1])
    except Exception as e:
        if verbose:
            print(f"  (motor balancing could NOT be applied: {e})")
        return [0, 0]
    if verbose and (cali[0] or cali[1]):
        print(f"  (motor balancing: left {cali[0]:+d} / right {cali[1]:+d} "
              f"— measured: {info.get('merve')})")
    return cali


def save(bal, jobb, merve="", megjegyzes=""):
    """Saves the balancing (used by motor_kalib.py)."""
    bal = max(-MAX_CORR, min(MAX_CORR, int(bal)))
    jobb = max(-MAX_CORR, min(MAX_CORR, int(jobb)))
    d = {
        "bal": bal,
        "jobb": jobb,
        "merve": merve,
        "megjegyzes": megjegyzes,
        "_doc": ("picarx cali_speed_value [bal=left, jobb=right]; POSITIVE = slows that motor down. "
                 "If the RIGHT motor is weak, 'jobb' is NEGATIVE (speed-up). "
                 "Measure with motor_kalib.py — intermittent phenomenon, re-measure if it gets worse!"),
    }
    tmp = BALANCE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, BALANCE_PATH)
    return d

def _wrap_stop_safe(px, bal, jobb):
    """Lesson learned outdoors: picarx subtracts the cali even at speed 0 ->
    0-6 = -6 PWM = UNDERFLOW, so the left wheel spins backwards INSTEAD OF STOPPING
    (first seen live during an outdoor run, spotted by a human). Therefore at speed 0
    the cali is temporarily disabled — the correction only applies WHILE DRIVING."""
    orig = px.set_motor_speed
    def safe_set_motor_speed(motor, speed):
        if speed == 0:
            regi = list(px.cali_speed_value)
            px.cali_speed_value = [0, 0]
            try:
                orig(motor, 0)
            finally:
                px.cali_speed_value = regi
        else:
            orig(motor, speed)
    px.set_motor_speed = safe_set_motor_speed
