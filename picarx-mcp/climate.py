#!/usr/bin/env python3
"""climate.py — BME280: temperature + humidity + air pressure ("what is it like out here?").

Wiring: its OWN software bus — i2c-3 on GPIO13(SDA)/GPIO6(SCL)
(dtoverlay=i2c-gpio,bus=3 in config.txt; the sensor was re-soldered off the QWIIC chain
to end the i2c-1 bus collision with the battery guard -> false −126 °C readings).
Address: 0x77 (Adafruit STEMMA QT). Lib: adafruit-circuitpython-bme280 + adafruit-extended-bus.
Fallback: if bus 3 is not available (e.g. reverted config), it tries bus 1.

Sanity filter: a reading outside the BME280 spec range (T −40..85 °C, p 300..1100 hPa,
humidity 0..100%) is NOT data but a communication error -> ValueError + a drop counter in
/tmp/bme_eldobott.txt. THE FILTERING IS NOT SILENT: every successful reading also carries
the number of drops so far (the "eldobott" = dropped field).

Usage:
  python3 climate.py         # one-shot
  python3 climate.py loop    # continuous (every 5 s)
"""
import sys
import time

from adafruit_bme280 import basic as adafruit_bme280

_ELDOBOTT_FAJL = "/tmp/bme_eldobott.txt"
_eldobott = 0


def _busz():
    try:
        from adafruit_extended_bus import ExtendedI2C
        return ExtendedI2C(3)
    except Exception:
        import board
        return board.I2C()  # fallback: the old chain (i2c-1)


i2c = _busz()
bme = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=0x77)

# WARM-UP READ: after the init's soft reset the first temperature read still catches the
# raw reset-default sample (0x80000), which with our calibration is exactly 22.96 °C ≈ an
# "eternal 23.0" — while p/h are already live. So the first measurement is DISCARDED, and we
# leave time for one full real measurement.
_ = bme.temperature
time.sleep(0.15)


def _eldobas_konyveles(t, p, h):
    global _eldobott
    _eldobott += 1
    try:
        with open(_ELDOBOTT_FAJL, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} #{_eldobott} T={t} p={p} h={h}\n")
    except OSError:
        pass


def read_once():
    t = bme.temperature
    p = bme.pressure
    h = bme.relative_humidity
    # BME280 spec range: outside it this is not a measurement but a corrupted read
    if not (-40.0 <= t <= 85.0) or not (300.0 <= p <= 1100.0) or not (0.0 <= h <= 100.0):
        _eldobas_konyveles(round(t, 1), round(p, 1), round(h, 1))
        raise ValueError(
            f"BME280 out-of-spec reading DROPPED ({_eldobott} so far): "
            f"T={t:.1f} p={p:.1f} h={h:.1f}")
    return {
        "homerseklet_C": round(t, 1),
        "para_pct": round(h, 1),
        "legnyomas_hPa": round(p, 1),
        "eldobott": _eldobott,
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "loop":
        try:
            while True:
                try:
                    d = read_once()
                    print(f"{d['homerseklet_C']} °C  |  {d['para_pct']} %  |  "
                          f"{d['legnyomas_hPa']} hPa  |  dropped: {d['eldobott']}")
                except ValueError as e:
                    print(e)
                time.sleep(5)
        except KeyboardInterrupt:
            print("done")
    else:
        print(read_once())
