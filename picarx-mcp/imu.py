#!/usr/bin/env python3
"""imu.py — BNO055 9-DOF IMU (body sense + drift-free heading).

Wiring (FINAL): the BNO055 is the 4th member of the SHARED QWIIC CHAIN (hardware
i2c-1, address 0x28). The earlier D4/D5 software-bus plan is FORBIDDEN — GPIO23/24
are the MOTOR DIRECTION lines, and a D4/D5 header does not physically exist on the
Robot HAT V4 (see technika/eszkoztar.md)!
Because of clock stretching the bus is slowed down: /boot/firmware/config.txt ->
  dtparam=i2c_arm_baudrate=10000
(This also slows the VL53L1X ranging — an accepted compromise; if it becomes a
problem, the BNO055's UART mode is plan B.)
Lib: pip3 install adafruit-circuitpython-bno055

Usage:
  python3 imu.py           # one-shot: euler (heading/roll/pitch) + calibration status
  python3 imu.py loop      # continuous, until Ctrl+C
  python3 imu.py calib     # calibration mode: status every second (slow figure-8s until 3/3/3/3)
"""
import sys
import time

import json
import os

import board
import adafruit_bno055

i2c = board.I2C()  # hardware i2c-1 (slowed down, 10 kHz)

# The Adafruit BNO055.__init__ does a FULL chip reset (RST_SYS) on EVERY instantiation ->
# the fusion starts from 0, the calibration is cleared, and when the magnetometer fusion
# settles a step jump follows (this caused false turns and an IMU that never settled).
# Meanwhile orszem.service keeps the chip in NDOF since boot. Hence: GENTLE ATTACH — if the
# chip is already running in NDOF, we do NOT reset and do NOT write offsets, we just attach.
# Full init + offset restore ONLY if the mode is not NDOF (fresh power, or after a process
# that got stuck in IMUPLUS).
from adafruit_bus_device.i2c_device import I2CDevice

_ID_REG, _CHIP_ID, _MODE_REG = 0x00, 0xA0, 0x3D


class _KimeletesBNO055(adafruit_bno055.BNO055_I2C):
    """Attach without a reset: BNO055.__init__ is deliberately NOT called."""
    def __init__(self, i2c, address=0x28):  # pylint: disable=super-init-not-called
        self.buffer = bytearray(2)
        # probe=False is MANDATORY (measured): I2CDevice's default probe write (an empty
        # write to the device) sets the BNO055's mode register to 0 (CONFIG) -> the fusion
        # stops, euler is None. An i2c.scan() does the same (preflight!).
        self.i2c_device = I2CDevice(i2c, address, probe=False)


def _csatlakozas():
    """(sensor, 'csatlakozva'|'reset') — to a live NDOF chip we only attach ('csatlakozva' = attached)."""
    try:
        k = _KimeletesBNO055(i2c)
        if k._read_register(_ID_REG) == _CHIP_ID and k.mode == adafruit_bno055.NDOF_MODE:
            return k, "csatlakozva"
    except Exception as e:
        print(f"(gentle attach failed: {e} — full init)")
    return adafruit_bno055.BNO055_I2C(i2c), "reset"


sensor, IMU_INDITAS = _csatlakozas()  # address: 0x28

# Offset restore is needed only after a reset; on a live chip the offsets are already
# loaded, and the CONFIG switch itself would restart the fusion.
_OFFS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bno055_offsets.json")
if IMU_INDITAS == "reset" and os.path.exists(_OFFS):
    try:
        with open(_OFFS) as f:
            o = json.load(f)
        _prev = sensor.mode
        sensor.mode = adafruit_bno055.CONFIG_MODE
        time.sleep(0.05)
        sensor.offsets_accelerometer = tuple(o["offsets_accelerometer"])
        sensor.offsets_gyroscope = tuple(o["offsets_gyroscope"])
        sensor.offsets_magnetometer = tuple(o["offsets_magnetometer"])
        sensor.radius_accelerometer = o["radius_accelerometer"]
        sensor.radius_magnetometer = o["radius_magnetometer"]
        sensor.mode = _prev
        time.sleep(0.1)
    except Exception as e:
        print(f"(offset restore failed: {e} — continuing in raw mode)")


import threading
import subprocess


class GyroIntegrator:
    """RELATIVE turn measurement by integrating the RAW gyro-z, in a SEPARATE PROCESS.
    Why raw gyro: the fused euler UNDER-READS during the jerky K-cycle (0.84–0.91 depending
    on the manoeuvre), while the raw integral agrees with the camera to 0.98 (logged from a
    separate process against an AprilTag). Why a separate process: as a thread it is starved
    by the main thread's sonar reads (GIL, busy-wait) and mostly gets samples in the quiet
    BETWEEN motions -> 78° by camera vs ~64° integrated. The child: `python3 imu.py gyrointeg`
    (20 Hz, outlier filter |rate|>400°/s against I2C reads colliding with the sentinel; first
    line is BIAS, then 'deg n errors' per line).
    Convention: heading() is compass-like (increases CW) — the gyro-z (+ = CCW) with a minus sign."""

    def __init__(self, h0=0.0):
        self.h0 = float(h0); self.deg = 0.0; self.bias = 0.0
        self.n = 0; self.hiba = 0; self._p = None; self._t = None; self._run = True

    def _olvaso(self):
        for line in self._p.stdout:
            if not self._run: break
            try:
                a = line.split()
                if a and a[0] == "BIAS":
                    self.bias = float(a[1]); continue
                self.deg = float(a[0]); self.n = int(a[1]); self.hiba = int(a[2])
            except Exception:
                pass

    def bias_meres(self, s=0.5):
        """(compatibility) the child measures the resting value at start; here we only return it."""
        return self.bias

    def start(self, varakozas=3.0):
        here = os.path.dirname(os.path.abspath(__file__))
        self._p = subprocess.Popen([sys.executable, "-u", os.path.join(here, "imu.py"), "gyrointeg"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self._t = threading.Thread(target=self._olvaso, daemon=True); self._t.start()
        t0 = time.time()
        while time.time() - t0 < varakozas and self.n == 0:
            time.sleep(0.05)
        return self.n > 0

    def heading(self):
        return (self.h0 + self.deg) % 360.0

    def stop(self):
        self._run = False
        if self._p:
            try: self._p.terminate(); self._p.wait(timeout=1.0)
            except Exception: pass


def _gyrointeg_gyermek(hz=20.0, bias_s=0.5, max_dps=400.0):
    """Child process of GyroIntegrator: raw gyro-z integral with an outlier filter."""
    R = 57.29577951308232; dt_cel = 1.0 / hz
    vals = []; t0 = time.time()
    while time.time() - t0 < bias_s:
        try:
            g = sensor.gyro
            if g and g[2] is not None and abs(g[2] * R) < max_dps: vals.append(g[2] * R)
        except Exception: pass
        time.sleep(dt_cel)
    bias = sum(vals) / len(vals) if vals else 0.0
    print(f"BIAS {bias:.4f}", flush=True)
    deg = 0.0; n = 0; hiba = 0; last = time.time()
    while True:
        try: g = sensor.gyro
        except Exception: g = None
        now = time.time(); dt = now - last; last = now
        if g and g[2] is not None and abs(g[2] * R) < max_dps:
            deg -= (g[2] * R - bias) * dt; n += 1
        else:
            hiba += 1
        print(f"{deg:.3f} {n} {hiba}", flush=True)
        time.sleep(dt_cel)


def ndof_vissza():
    """Puts the SHARED chip back into NDOF if a process left it in IMUPLUS
    (smart_turn/go_heading fallback). With reset-free attaching it would otherwise stay stuck."""
    try:
        if sensor.mode != adafruit_bno055.NDOF_MODE:
            sensor.mode = adafruit_bno055.CONFIG_MODE
            time.sleep(0.05)
            sensor.mode = adafruit_bno055.NDOF_MODE
            time.sleep(0.1)
            return True
    except Exception as e:
        print(f"(ndof_vissza failed: {e})")
    return False


def bealt(max_s=4.0, stabil_s=1.0, tures=2.0):
    """After the instantiation SOFT RESET the heading starts from a relative 0, and when the
    magnetometer fusion settles it JUMPS in one STEP to the absolute direction (stationary
    test at t=0.5 s: 360->325; at a disturbed spot — junction, metal, charger — later, even
    DURING a turn -> smart_turn once counted it as rotation). So BEFORE anchoring we wait:
    sys==3 AND the heading within `tures` degrees for `stabil_s` seconds.
    Returns: (beallt: bool, heading: float|None, calib: str)."""
    t0 = time.time()
    minta = []
    utolso = None
    while time.time() - t0 < max_s:
        try:
            h = sensor.euler[0]
            sysc = sensor.calibration_status[0]
        except Exception:
            h, sysc = None, 0
        most = time.time()
        if h is not None:
            minta.append((most, h))
            minta = [(t, v) for (t, v) in minta if most - t <= stabil_s]
            utolso = h
            if sysc == 3 and most - t0 >= stabil_s and len(minta) >= 3:
                ref = minta[0][1]
                ds = [((v - ref + 540.0) % 360.0) - 180.0 for (_, v) in minta]
                if max(ds) - min(ds) <= tures:
                    return True, h, read_once()["calib"]
        time.sleep(0.1)
    return False, utolso, read_once()["calib"]


def imuplus_mod():
    """gyro+acc (IMUPLUS) mode for this process: RELATIVE heading, no magnetometer jumps.
    NOT usable for an absolute target (smart_turn 'to')."""
    sensor.mode = adafruit_bno055.CONFIG_MODE
    time.sleep(0.05)
    sensor.mode = adafruit_bno055.IMUPLUS_MODE
    time.sleep(0.5)


def read_once():
    h, r, p = sensor.euler or (None, None, None)
    sysc, gyro, acc, mag = sensor.calibration_status
    return {
        "heading": round(h, 1) if h is not None else None,
        "roll": round(r, 1) if r is not None else None,
        "pitch": round(p, 1) if p is not None else None,
        "calib": f"sys{sysc} gyro{gyro} acc{acc} mag{mag}",
    }


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "gyrointeg":
        _gyrointeg_gyermek()
    elif mode == "loop":
        try:
            while True:
                d = read_once()
                print(f"heading={d['heading']}°  roll={d['roll']}°  pitch={d['pitch']}°  [{d['calib']}]")
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("done")
    elif mode == "calib":
        print("Calibration: move it in slow figure-8s, target is 3 for all (sys/gyro/acc/mag).")
        try:
            while True:
                print("calib:", read_once()["calib"])
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("done")
    else:
        print(read_once())
