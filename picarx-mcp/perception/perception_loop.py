"""perception_loop.py — local vision subagent on the Pi (the owner's idea).

Gives EYES to the robot's REFLEX layer: the sonar only sees straight ahead and is blind
to rugs, slanted glass, furniture legs, and the sides. This process recognizes objects
from the camera stream with YOLO11n and, via the core.perception telemetry core, writes:
  - /tmp/perception.json  : compact telemetry for the STRATEGIST (Claude reads it between steps)
  - /tmp/perception_stop  : reflex flag (exists = stop; motion.py can check it cheaply)
  - /tmp/perception.jpg   : last (annotated) frame, for visual inspection

Claude stays the captain; this is the fast, local "eye+hand". It does NOT drive on its own.

====================================================================
STATUS: NOT YET VALIDATED ON HARDWARE (the robot was switched off when this was written).
The core.perception telemetry core is unit-tested LOCALLY (test_perception.py, 23/23).
YOLO loading, the camera stream and the FPS are decided on the Pi.
====================================================================

MODES:
  python3 perception_loop.py --export
      One-off: yolo11n.pt -> NCNN export (yolo11n_ncnn_model/). NCNN = the recommended,
      torch-free inference backend for the Pi 4 (fast on CPU).

  python3 perception_loop.py --benchmark [N] [--imgsz 320]
      FIRST PI TEST: pure inference speed on N (default 50) dummy frames,
      WITHOUT THE CAMERA (does not clash with pilot.py's rpicam-still calls). Prints the
      FPS and the per-frame time. This decides whether the model is fast enough.

  python3 perception_loop.py [--imgsz 320] [--conf 0.4]
      Live loop: picamera2 continuous stream -> detection -> telemetry/flag/jpg.
      WARNING: while it runs it OWNS the camera -> the navigation rpicam-still calls
      won't work until then. Integration (nav getting its image from here) = a separate step.

CAMERA-OWNERSHIP PLAN (for full integration, later): the daemon should be the ONLY
camera owner; the nav scripts read /tmp/perception.jpg and the telemetry
instead of their own rpicam-still. This matches the "the subagent owns the eye" principle.
"""
import sys, os, time, json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core.perception import build_telemetry

MODEL_PT = os.path.join(HERE, "yolo11n.pt")
MODEL_NCNN = os.path.join(HERE, "yolo11n_ncnn_model")
TELEMETRY = "/tmp/perception.json"
STOP_FLAG = "/tmp/perception_stop"
LAST_JPG = "/tmp/perception.jpg"

DEFAULT_IMGSZ = 320     # smaller = faster; a good compromise on the Pi 4
DEFAULT_CONF = 0.4


def _arg(flag, default=None, cast=str):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return cast(sys.argv[i + 1])
    return default


def _load_model(prefer_ncnn=None):
    """Load the YOLO model.

    DEFAULT: PyTorch .pt (STABLE, ~5 FPS @320 on the Pi 4). NCNN would be FASTER, BUT
    on this aarch64 build combo (ultralytics 8.4.69 + ncnn wheel + torch 2.12+cpu)
    inference CRASHES with heap corruption (corrupted double-linked list /
    double free) — a native abort that cannot be caught from Python. So NCNN is only
    OPT-IN (--ncnn) until the crash is resolved (TODO: pin the ncnn version / isolated
    ncnn binding without torch). See perception/README.md."""
    if prefer_ncnn is None:
        prefer_ncnn = ("--ncnn" in sys.argv)
    from ultralytics import YOLO
    if prefer_ncnn and os.path.isdir(MODEL_NCNN):
        print(f"[model] NCNN: {MODEL_NCNN}")
        return YOLO(MODEL_NCNN, task="detect")
    if os.path.exists(MODEL_PT):
        print(f"[model] PyTorch: {MODEL_PT} (export to NCNN for speed!)")
        return YOLO(MODEL_PT)
    # ultralytics downloads yolo11n.pt on first use if it is missing
    print("[model] downloading/loading yolo11n.pt...")
    return YOLO("yolo11n.pt")


def _boxes_from_result(res, names):
    """ultralytics Result -> core.perception input boxes."""
    out = []
    b = getattr(res, "boxes", None)
    if b is None:
        return out
    xyxy = b.xyxy.tolist() if hasattr(b.xyxy, "tolist") else b.xyxy
    cls = b.cls.tolist() if hasattr(b.cls, "tolist") else b.cls
    conf = b.conf.tolist() if hasattr(b.conf, "tolist") else b.conf
    for (x1, y1, x2, y2), c, cf in zip(xyxy, cls, conf):
        out.append({
            "label": names.get(int(c), str(int(c))) if isinstance(names, dict) else str(int(c)),
            "conf": float(cf), "x1": float(x1), "y1": float(y1), "x2": float(x2), "y2": float(y2),
        })
    return out


def do_export():
    from ultralytics import YOLO
    print("[export] loading yolo11n.pt...")
    m = YOLO("yolo11n.pt")
    print("[export] NCNN export (may take minutes on the Pi)...")
    m.export(format="ncnn")
    print(f"[export] DONE -> {MODEL_NCNN}")


def do_benchmark(n, imgsz):
    """Pure inference speed on a dummy frame, WITHOUT THE CAMERA."""
    import numpy as np
    model = _load_model()
    names = getattr(model, "names", {})
    # realistically sized dummy image (the camera will run at 640x480)
    frame = (np.random.rand(480, 640, 3) * 255).astype("uint8")
    # warm-up (the first call is slow: graph build, cache)
    for _ in range(3):
        model(frame, imgsz=imgsz, conf=DEFAULT_CONF, verbose=False)
    t0 = time.time()
    for _ in range(n):
        res = model(frame, imgsz=imgsz, conf=DEFAULT_CONF, verbose=False)[0]
    dt = time.time() - t0
    fps = n / dt if dt > 0 else 0.0
    boxes = _boxes_from_result(res, names)
    print(f"\n=== BENCHMARK (imgsz={imgsz}, n={n}) ===")
    print(f"  total time: {dt:.2f}s   avg/frame: {dt/n*1000:.1f} ms   FPS: {fps:.2f}")
    print(f"  ({len(boxes)} detections on the dummy image — noise, only the speed matters)")
    if fps >= 3:
        print("  VERDICT: >=3 FPS -> ENOUGH for burst navigation. ✓")
    else:
        print("  VERDICT: <3 FPS -> need a smaller imgsz (256/192) or a lighter model.")


def do_loop(imgsz, conf):
    """Live loop: picamera2 continuous stream -> telemetry/flag/jpg."""
    import numpy as np
    try:
        from picamera2 import Picamera2
    except Exception as e:
        print(f"[error] picamera2 not available: {e}\n"
              "Install: sudo apt install -y python3-picamera2  (or use the --benchmark mode)")
        sys.exit(3)
    import cv2
    model = _load_model()
    names = getattr(model, "names", {})

    picam = Picamera2()
    cfg = picam.create_preview_configuration(main={"size": (640, 480), "format": "RGB888"})
    picam.configure(cfg)
    picam.start()
    time.sleep(1.0)  # AE/AWB settling
    print("[loop] running. Ctrl-C to stop.")
    try:
        while True:
            t = time.time()
            frame = picam.capture_array()         # HxWx3 RGB
            h, w = frame.shape[:2]
            res = model(frame, imgsz=imgsz, conf=conf, verbose=False)[0]
            boxes = _boxes_from_result(res, names)
            tel = build_telemetry(boxes, w, h, ts=t)
            tmp = TELEMETRY + ".tmp"
            with open(tmp, "w") as f:
                json.dump(tel, f)
            os.replace(tmp, TELEMETRY)            # atomic replace
            # reflex flag: exists = stop
            if tel["stop"]:
                with open(STOP_FLAG, "w") as f:
                    f.write(json.dumps(tel["closest_danger"]))
            elif os.path.exists(STOP_FLAG):
                os.remove(STOP_FLAG)
            # save the annotated image (meant to be throttled to ~every 2 s so it doesn't slow the loop)
            try:
                cv2.imwrite(LAST_JPG, cv2.cvtColor(res.plot(), cv2.COLOR_RGB2BGR))
            except Exception:
                pass
    except KeyboardInterrupt:
        print("\n[loop] stopped.")
    finally:
        picam.stop()
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)


if __name__ == "__main__":
    imgsz = _arg("--imgsz", DEFAULT_IMGSZ, int)
    conf = _arg("--conf", DEFAULT_CONF, float)
    if "--export" in sys.argv:
        do_export()
    elif "--benchmark" in sys.argv:
        n = 50
        # optional N right after --benchmark, if it is a number
        i = sys.argv.index("--benchmark")
        if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit():
            n = int(sys.argv[i + 1])
        do_benchmark(n, imgsz)
    else:
        do_loop(imgsz, conf)
