#!/usr/bin/env python3
"""lux.py — VEML7700: light as a sense (dusk, cloud, lamp).

Wiring: QWIIC chain (hardware i2c-1), address 0x10.
Lib: pip3 install adafruit-circuitpython-veml7700

Rough scale for orientation: <10 lx dark/night, 10–100 mood lighting,
100–500 room, 500–5000 overcast daylight, >10000 sunshine.

Usage:
  python3 lux.py         # one-shot
  python3 lux.py loop    # continuous
"""
import sys
import time

import board
import adafruit_veml7700

i2c = board.I2C()
veml = adafruit_veml7700.VEML7700(i2c)  # address: 0x10


def read_once():
    return round(veml.lux, 1)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        try:
            while True:
                print(f"{read_once()} lx")
                time.sleep(1)
        except KeyboardInterrupt:
            print("done")
    else:
        print(f"{read_once()} lx")
