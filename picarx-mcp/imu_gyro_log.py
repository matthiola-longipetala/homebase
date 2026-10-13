#!/usr/bin/env python3
"""imu_gyro_log.py — the deciding test for the x0.86 IMUPLUS under-reading.

During a turn it integrates the RAW gyro z axis (~20 Hz) and logs the fused euler heading
next to it. Comparison with the camera (whereami tag bearing):
  - raw integral ≈ camera, euler-Δ ≈ 0.86×camera -> the FUSION errs because of the jerky
    K-cycle -> our own gyro integration is needed, not a factor (it would be angle-dependent);
  - raw integral ≈ euler-Δ ≈ 0.86×camera -> SCALE error -> the factor stays.

Usage (in a SEPARATE ssh, started BEFORE smart_turn, in the background):
  python3 imu_gyro_log.py <seconds> [output.csv]
It shares the I2C bus with the sentinel and smart_turn — that already works today
(sentinel + scripts).
"""
import sys
import time
import imu

T = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/imu_gyro_log.csv"

t0 = time.time()
last = t0
integ = 0.0
h0 = None
n = 0
with open(out, "w") as f:
    f.write("t,gyro_z_dps,integral_deg,euler_heading,mode\n")
    while time.time() - t0 < T:
        try:
            g = imu.sensor.gyro
            e = imu.sensor.euler
        except Exception:
            g, e = None, None
        now = time.time()
        dt = now - last
        last = now
        if g is not None and g[2] is not None:
            # the lib returns rad/s; we integrate in degrees
            gz = g[2] * 57.29577951308232
            integ += gz * dt
        else:
            gz = float("nan")
        h = e[0] if e and e[0] is not None else float("nan")
        if h0 is None and h == h:
            h0 = h
        f.write(f"{now - t0:.3f},{gz:.2f},{integ:.2f},{h:.1f},{imu.sensor.mode}\n")
        n += 1
        time.sleep(0.05)

dh = None
try:
    e = imu.sensor.euler
    if e and e[0] is not None and h0 is not None:
        dh = ((e[0] - h0 + 540.0) % 360.0) - 180.0
except Exception:
    pass
print(f"DONE: {n} samples, {T:.0f} s; raw gyro integral Δ = {integ:+.1f}°; "
      f"euler-Δ = {dh:+.1f}°" if dh is not None else
      f"DONE: {n} samples; raw gyro integral Δ = {integ:+.1f}°; euler-Δ = ?")
print(f"log: {out}  (start: {imu.IMU_INDITAS})")
