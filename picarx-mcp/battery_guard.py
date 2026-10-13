"""battery_guard.py — BATTERY GUARD: prevents a full discharge AND an unclean shutdown.

Problem (lesson learned): the robot ran the battery to zero -> sudden power loss -> unclean
shutdown -> the SD card's file system can be damaged (it did not boot afterwards). The 2-cell
Li battery: ~8.4 V full, ~6.0 V "empty"/brownout. It must NEVER be run down that far.

Solution: a continuously running guard that:
  - below WARN_V WARNS (log + /tmp/battery_status flag that the agent can read),
  - below CRIT_V (BEFORE the brownout) shuts the Pi down GRACEFULLY (`sudo poweroff`),
    so the file system unmounts cleanly -> no SD damage, and the battery does not hit 0.

Debounce: under motor load the voltage SAGS momentarily; so CRIT needs CRIT_CONSEC
consecutive readings, so that a transient dip does not switch it off while driving.

Installation (must run as root so poweroff works without a password) -> battery_guard.service.
Refine the thresholds based on the data in battery_guard.log.
"""
import os
import time
import subprocess
from datetime import datetime

try:
    from robot_hat import get_battery_voltage
except Exception:
    get_battery_voltage = None

POLL_S = 20.0          # measures every this many seconds
WARN_V = 6.9           # warning (should charge)
CRIT_V = 6.6           # graceful shutdown AT this level (BEFORE the ~6.0 brownout, with a good margin)
CRIT_CONSEC = 3        # this many consecutive CRIT readings needed (against transient dips under motor load)
# runs as root (see battery_guard.service): log next to this script, not in root's home
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "battery_guard.log")
FLAG = "/tmp/battery_status"   # "V PCT STATE" — the agent can read it (over SSH) for a quick check

FULL_V, EMPTY_V = 8.4, 6.0


def vpct(v):
    return max(0, min(100, round((v - EMPTY_V) / (FULL_V - EMPTY_V) * 100)))


def log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def stop_motors():
    """Safety: stop the motor before shutting down (if it is running)."""
    try:
        from picarx import Picarx
        Picarx().forward(0)
    except Exception:
        pass


def main():
    if get_battery_voltage is None:
        log("ERROR: robot_hat get_battery_voltage not available -> exiting")
        return
    log(f"battery_guard STARTING (WARN={WARN_V}V CRIT={CRIT_V}V poll={POLL_S}s consec={CRIT_CONSEC})")
    crit_count = 0
    while True:
        try:
            v = round(float(get_battery_voltage()), 2)
        except Exception as e:
            log(f"read error: {e}")
            time.sleep(POLL_S)
            continue
        p = vpct(v)
        state = "CRIT" if v <= CRIT_V else ("WARN" if v <= WARN_V else "OK")
        try:
            with open(FLAG, "w") as f:
                f.write(f"{v} {p} {state}\n")
        except Exception:
            pass

        if v <= CRIT_V:
            crit_count += 1
            log(f"CRITICAL battery: {v}V ({p}%) [{crit_count}/{CRIT_CONSEC}]")
            if crit_count >= CRIT_CONSEC:
                log(f"GRACEFUL SHUTDOWN now ({v}V {p}%) — to protect the file system and the battery")
                stop_motors()
                subprocess.run(["sudo", "poweroff"])
                return
        else:
            if crit_count:
                log(f"battery recovered from CRIT: {v}V ({p}%)")
            crit_count = 0
            if v <= WARN_V:
                log(f"WARNING low battery: {v}V ({p}%) — needs charging")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
