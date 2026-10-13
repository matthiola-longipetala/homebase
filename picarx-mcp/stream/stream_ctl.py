"""stream_ctl.py — manual controller for the live video stream.

Usage (all from ~/picarx-mcp/):
  python3 stream/stream_ctl.py start      # start the supervisor daemon (background)
  python3 stream/stream_ctl.py stop       # stop the supervisor + every stream process
  python3 stream/stream_ctl.py status     # status (JSON)
  python3 stream/stream_ctl.py on|off|auto  # manual on / manual off / back to auto (active mode)
  python3 stream/stream_ctl.py nav on|off   # NAV MODE: stay paused for the whole nav burst (no pumping)
  python3 stream/stream_ctl.py pause|resume # temporarily release the camera / take it back
  python3 stream/stream_ctl.py frame [path]   # latest stream frame -> path (default /tmp/stream_frame.jpg)
  python3 stream/stream_ctl.py adaptive [path]  # ON-DEMAND adaptive 'special eye' (capture.py)
  python3 stream/stream_ctl.py url
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
sys.path.insert(0, os.path.expanduser("~/picarx-mcp/stream"))
import stream_lib as S  # noqa: E402


def _supervisor_up():
    return S.pid_alive(S.read_pid(S.SUP_PID))


def cmd_start():
    if _supervisor_up():
        print("supervisor already running, pid", S.read_pid(S.SUP_PID))
        return
    with open(S.SUP_LOG, "ab", buffering=0) as lf:
        subprocess.Popen(
            [sys.executable, os.path.join(S.STREAM_DIR, "stream_supervisor.py")],
            stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, start_new_session=True,
        )
    time.sleep(1.0)
    print("supervisor started, pid", S.read_pid(S.SUP_PID))


def cmd_stop():
    if _supervisor_up():
        S.kill_pidfile(S.SUP_PID, wait_s=5.0)   # the supervisor shuts everything down in its finally block
        time.sleep(0.5)
    # belt and braces: clean up orphaned processes
    S.kill_pidfile(S.FFMPEG_PID)
    S.kill_pidfile(S.GO2RTC_PID)
    subprocess.run(["pkill", "-x", "rpicam-vid"], check=False)
    try:
        os.remove(S.LATEST)
    except OSError:
        pass
    print("stream stopped (supervisor + go2rtc + ffmpeg + rpicam)")


def cmd_status():
    ring = []
    try:
        ring = [f for f in os.listdir(S.RBUF_DIR) if f.endswith(".ts")]
    except OSError:
        pass
    ring_bytes = 0
    for f in ring:
        try:
            ring_bytes += os.path.getsize(os.path.join(S.RBUF_DIR, f))
        except OSError:
            pass
    lat_age = None
    try:
        lat_age = round(time.time() - os.path.getmtime(S.LATEST), 2)
    except OSError:
        pass
    st = {
        "supervisor": _supervisor_up(),
        "go2rtc": S.pid_alive(S.read_pid(S.GO2RTC_PID)),
        "consumer_ffmpeg": S.pid_alive(S.read_pid(S.FFMPEG_PID)),
        "rpicam": S.rpicam_running(),
        "stream_up": S.is_stream_up(),
        "latest_age_s": lat_age,
        "ring_segments": len(ring),
        "ring_MB": round(ring_bytes / 1e6, 1),
        "flags": {
            "on": S.flag(S.ON_FLAG), "off": S.flag(S.OFF_FLAG),
            "pause": S.flag(S.PAUSE_FLAG),
        },
        "active_stamp_age_s": S._mtime_age(S.ACTIVE_STAMP),
        "viewer_url": S.VIEWER_URL,
    }
    print(json.dumps(st, indent=2))


def cmd_on():
    S.set_flag(S.OFF_FLAG, False)
    S.set_flag(S.ON_FLAG, True)
    if not _supervisor_up():
        cmd_start()
    print("stream: manual ON (ON_FLAG)")


def cmd_off():
    S.set_flag(S.ON_FLAG, False)
    S.set_flag(S.OFF_FLAG, True)
    print("stream: manual OFF (OFF_FLAG) — the supervisor releases the camera")


def cmd_auto():
    S.set_flag(S.ON_FLAG, False)
    S.set_flag(S.OFF_FLAG, False)
    print("stream: AUTO (tied to active mode)")


def cmd_pause():
    ok = S.pause_camera()
    print("pause:", "camera released" if ok else "FAILED to release the camera")


def cmd_resume():
    S.resume_camera()
    print("resume: PAUSE_FLAG cleared, the supervisor restores the stream")


def cmd_frame(argv):
    dst = argv[0] if argv else "/tmp/stream_frame.jpg"
    t0 = time.time()
    ok = S.grab_latest(dst)
    dt = time.time() - t0
    if ok:
        sz = os.path.getsize(dst)
        print(f"OK: {dst} ({sz} B) {dt*1000:.0f} ms (fast path from the stream)")
    else:
        print("FAIL: the stream is not running / no fresh frame -> caller should fall back to the original path")
        sys.exit(2)


def cmd_adaptive(argv):
    """ON-DEMAND adaptive 'special eye': the stream briefly releases the camera,
    capture.py runs its per-frame smart tuning (dark corner / sunlit tag /
    night sky), then the stream is restored."""
    dst = argv[0] if argv else "/tmp/adaptive.jpg"
    w = int(argv[1]) if len(argv) > 1 else 1024
    h = int(argv[2]) if len(argv) > 2 else 576
    was_up = S.is_stream_up() or S.rpicam_running()
    released = S.pause_camera(timeout_s=3.0)
    if not released:
        print("WARNING: the camera was not released in time — trying anyway")
    try:
        from core.capture import capture
        E = capture(dst, w=w, h=h)
        print(f"OK adaptive: {dst} (E={E:.0f})")
    finally:
        if was_up:
            S.resume_camera()
            print("stream restored")


def cmd_nav(argv):
    """NAV MODE: during a navigation burst the stream stays paused THROUGHOUT (no
    per-frame up/down pumping between two tag photos); it is restored once at the end.
      nav on   -> enable (every capture extends it; TTL %ss without a capture)
      nav off  -> disable (the stream is restored)
      nav status
    """ % int(S.NAV_TTL)
    sub = argv[0] if argv else "status"
    if sub == "on":
        S.nav_refresh()
        print(f"stream: NAV MODE ON (the stream stays paused during the nav burst, "
              f"TTL {int(S.NAV_TTL)}s)")
    elif sub == "off":
        S.nav_off()
        print("stream: NAV MODE OFF (the stream is restored)")
    else:
        print(json.dumps({"nav_active": S.nav_active(),
                          "nav_flag_age_s": S._mtime_age(S.NAV_FLAG)}, indent=2))


def cmd_url():
    print("Live viewer window (LAN ONLY):")
    print("  ", S.VIEWER_URL)
    print("  (or the go2rtc home page:  http://%s:1984/ )" % S.LAN_IP)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd, rest = sys.argv[1], sys.argv[2:]
    fns = {
        "start": cmd_start, "stop": cmd_stop, "status": cmd_status,
        "on": cmd_on, "off": cmd_off, "auto": cmd_auto,
        "pause": cmd_pause, "resume": cmd_resume, "url": cmd_url,
    }
    if cmd in fns:
        fns[cmd]()
    elif cmd == "frame":
        cmd_frame(rest)
    elif cmd == "adaptive":
        cmd_adaptive(rest)
    elif cmd == "nav":
        cmd_nav(rest)
    else:
        print("unknown command:", cmd)
        print(__doc__)


if __name__ == "__main__":
    main()
