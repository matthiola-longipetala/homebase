#!/usr/bin/env python3
"""doa.py — the robot's SOUND DIRECTION (Direction of Arrival) from the ReSpeaker XVF3800's
internal XMOS controller, via USB vendor control transfers (pyusb).

The XVF3800 returns the DOA_VALUE parameter as two uint16 values:
  azimuth_deg = b[1] + 256*b[2]   (0..359, direction of the last/current sound source)
  speech      = b[3] + 256*b[4]   (1 = speech detected right now)
The same in radians in AEC_AZIMUTH_VALUES[0] (processed azimuth, NaN if there is no speech).

The raw azimuth is in the ReSpeaker's own reference frame. Alignment to the robot's front
is done by the 'doa' section of voice_config.json (offset + direction), see doa_to_robot().

Usage:
  python3 doa.py            # continuous: one line every ~0.3 s (only when there is speech)
  python3 doa.py once       # a single instantaneous reading (raw + robot-relative)
  python3 doa.py raw        # continuous RAW (for calibration, every reading)
  python3 doa.py calib [s]  # robust direction measurement over s seconds (default 8)
"""
import sys, time, struct, math, json, os

import usb.core
import usb.util

VID, PID = 0x2886, 0x001A
CFG = os.path.expanduser("~/picarx-mcp/voice_config.json")


def get_dev():
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        raise SystemExit("ReSpeaker (2886:001a) not found")
    return dev


def read_doa(dev):
    """(azimuth_deg:int 0..359, speech:bool) from the DOA_VALUE parameter."""
    r = list(dev.ctrl_transfer(
        usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
        0, 0x80 | 18, 20, 4 + 1, 100000))
    az = r[1] + 256 * r[2]
    speech = (r[3] + 256 * r[4]) > 0
    return az % 360, speech


def read_azimuth_rad(dev):
    """AEC_AZIMUTH_VALUES — 4 floats in radians (the first is the processed azimuth)."""
    r = dev.ctrl_transfer(
        usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE,
        0, 0x80 | 75, 33, 16 + 1, 100000)
    b = r.tobytes()
    n = (len(b) - 1) // 4
    return struct.unpack('<' + 'f' * n, b[1:1 + 4 * n])


def load_doa_cfg():
    try:
        return json.load(open(CFG)).get("doa", {})
    except Exception:
        return {}


def doa_to_robot(az_raw, cfg):
    """Raw ReSpeaker azimuth -> robot-relative bearing in degrees [-180,180].
    +bearing = to the RIGHT (relative to the robot's front). cfg: {offset_deg, sign}.
    bearing = 0 means the sound is right IN FRONT of the robot.
    """
    offset = cfg.get("offset_deg", 0.0)
    sign = cfg.get("sign", 1)
    b = sign * (az_raw - offset)
    b = (b + 180) % 360 - 180
    return b


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "loop"
    dev = get_dev()
    cfg = load_doa_cfg()

    if mode == "once":
        az, sp = read_doa(dev)
        print(f"raw_azimuth={az}deg  speech={sp}  robot_bearing={doa_to_robot(az, cfg):+.0f}deg")
        return

    if mode == "raw":
        # calibration mode: EVERY reading, raw number + radian azimuth
        while True:
            az, sp = read_doa(dev)
            rad = read_azimuth_rad(dev)
            d0 = "NaN" if math.isnan(rad[0]) else f"{math.degrees(rad[0]):.0f}"
            mark = "  <== SPEECH" if sp else ""
            print(f"raw={az:3d}deg  azim0={d0:>4}deg  speech={int(sp)}{mark}", flush=True)
            time.sleep(0.3)
        return

    if mode == "calib":
        # robust direction measurement in an echoing room: ~N s of sampling ONLY during
        # speech, then the most frequent azimuth (mode, in 10-degree buckets) -> the stable direction.
        secs = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
        import collections
        buckets = collections.Counter()
        samples = []
        t0 = time.time()
        while time.time() - t0 < secs:
            az, sp = read_doa(dev)
            if sp:
                buckets[round(az / 10) * 10 % 360] += 1
                samples.append(az)
            time.sleep(0.1)
        if not samples:
            print("NO speech samples (louder/closer)"); return
        top = buckets.most_common(3)
        # refinement: mean of the samples around the winning bucket (circular)
        win = top[0][0]
        near = [a for a in samples if min((a - win) % 360, (win - a) % 360) <= 20]
        # circular mean
        sx = sum(math.cos(math.radians(a)) for a in near)
        sy = sum(math.sin(math.radians(a)) for a in near)
        mean = round(math.degrees(math.atan2(sy, sx)) % 360)
        print(f"SAMPLES={len(samples)}  mode_bucket={win}deg ({top[0][1]}x)  "
              f"refined_azimuth={mean}deg")
        print(f"  top3 buckets: {top}")
        return

    # default loop: only when speech is detected
    last = None
    while True:
        az, sp = read_doa(dev)
        if sp:
            br = doa_to_robot(az, cfg)
            print(f"SOUND: raw={az}deg  robot_bearing={br:+.0f}deg", flush=True)
            last = az
        time.sleep(0.25)


if __name__ == "__main__":
    main()
