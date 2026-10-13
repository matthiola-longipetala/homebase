#!/usr/bin/env python3
"""lampa.py — control of the 3 W LED flash/lamp (TavIR #17546).

Wiring: **P4 PWM header** (S/V/G — 5 V rail!), the LED sits on the pan plate and turns
with the camera. If the LED module's own switching stage is not enough, the LR7843
opto-FET module goes in between — the software is the same.

WHY P4 AND NEVER P3: the Robot HAT's PWM channels share a TIMER IN GROUPS OF FOUR
(P0-P3 / P4-P7 / P8-P11). Changing the frequency of P3 also rewrote the 50 Hz clock of
the P0-P2 SERVOS -> all three servos jerked convulsively, and the current spikes even took
down the wifi. LEDs/other PWM loads may ONLY go into a servo-free group (P4-P11)!

RULES:
- Default use is the FLASH (a short flash for a photo, robot standing still) — the 5 V
  rail's 3 A budget is tight (Pi+ReSpeaker+servos+motor!).
- FULL BRIGHTNESS WHILE DRIVING IS FORBIDDEN (headlight mode only at low duty, standing).
- 3 W without a heat sink gets hot -> sustained "on" only at low duty, for minutes.
- Glare: a nearby white wall/paper tag can burn out -> duty is the regulator.

Usage:
  python3 lampa.py on [duty]        # sustained light (default 20%, ceiling 60% for sustained)
  python3 lampa.py off
  python3 lampa.py villanas [ms] [duty]   # flash (also: vaku, flash): default 800 ms @ 100%
  python3 lampa.py proba            # careful ramp-up test: 5->10->20%, 1 s each

State signal for other scripts: /tmp/lampa_on (content: duty), off deletes it.
Import interface (look3 --flash): from lampa import be, ki   (be = on, ki = off)
"""
import os
import sys
import time

from robot_hat import PWM

PIN = "P4"  # NEVER P0-P3 (servo timer group — see the header warning)
FREQ_HZ = 200        # flicker-free for eyes and camera; comfortable for an opto-FET too
TARTOS_PLAFON = 60   # duty ceiling for sustained use (heat + rail budget)
ALLAPOT = "/tmp/lampa_on"

_pwm = None


def _p():
    global _pwm
    if _pwm is None:
        _pwm = PWM(PIN)
        _pwm.freq(FREQ_HZ)
    return _pwm


def be(duty=20):
    duty = max(0, min(100, int(duty)))
    _p().pulse_width_percent(duty)
    try:
        with open(ALLAPOT, "w") as f:
            f.write(str(duty))
    except Exception:
        pass
    return duty


def ki():
    _p().pulse_width_percent(0)
    try:
        os.remove(ALLAPOT)
    except Exception:
        pass


def villanas(ms=800, duty=100):
    be(duty)
    time.sleep(ms / 1000.0)
    ki()


if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "proba"
    if mode == "on":
        duty = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        if duty > TARTOS_PLAFON:
            print(f"(for sustained use the ceiling is {TARTOS_PLAFON}% — using that instead of {duty}; "
                  f"full light only for a flash: lampa.py villanas)")
            duty = TARTOS_PLAFON
        print(f"LAMP ON: {be(duty)}% ({PIN}, {FREQ_HZ} Hz) — do not forget to switch it off!")
    elif mode == "off":
        ki()
        print("LAMP OFF")
    elif mode in ("villanas", "vaku", "flash"):
        ms = int(sys.argv[2]) if len(sys.argv) > 2 else 800
        duty = int(sys.argv[3]) if len(sys.argv) > 3 else 100
        print(f"FLASH: {ms} ms @ {duty}%")
        villanas(ms, duty)
        print("done")
    elif mode == "proba":
        print("Ramp-up test: 5% → 10% → 20%, 1 s each (watch: is it lit? steady?)")
        for d in (5, 10, 20):
            print(f"  duty {d}%")
            be(d); time.sleep(1.0)
        ki()
        print("done — if all 3 steps were visible, the control works")
    else:
        print("usage: lampa.py on [duty] | off | villanas [ms] [duty] | proba (test)")
