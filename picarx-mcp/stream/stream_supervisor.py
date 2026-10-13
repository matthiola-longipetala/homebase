"""stream_supervisor.py — lifecycle controller for the live video stream.

A SINGLE daemon that ties the stream to ACTIVE mode (following the existing
active/passive pattern) and handles the manual overrides. It is the EXCLUSIVE actor
that starts/stops the camera-pipeline processes — so there are no races.

Desired state (by priority):
  OFF_FLAG            -> OFF   (manual off; overrides everything)
  PAUSE_FLAG         -> PAUSED (camera released, go2rtc stays -> fast restore)
  ON_FLAG            -> ON     (manual on)
  active mode        -> ON     (robot_active_at is fresh)
  otherwise          -> OFF

  ON     : go2rtc running + ffmpeg consumer running (ring buffer + latest.jpg) -> camera live
  PAUSED : go2rtc running, consumer+rpicam stopped -> camera free
  OFF    : everything stopped, camera free, no orphan processes

By default (nobody is working with the robot) the stream is NOT running.
stream_lib.pause_camera()/resume_camera() coordinate with this via PAUSE_FLAG.

The daemon is started in the background by server.py (guarded), or manually:
  python3 stream/stream_supervisor.py &
Stop: stream_ctl.py stop  (or kill using /tmp/stream_supervisor.pid).
"""
import glob
import json
import os
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))
sys.path.insert(0, os.path.expanduser("~/picarx-mcp/stream"))
import stream_lib as S  # noqa: E402

LOOP_S = 1.0
EXPO_INTERVAL_S = 20.0        # how often we look at the histogram (rolling correction)
# AGAINST MOTION BLUR: the STREAM exposure CAPS the shutter time
# (the capture.py tag path does NOT — a settled still does not mind a long shutter).
# In bright light the gain drops to 1 and the shutter slides below the cap (even sharper);
# in the dark the shutter stays at the cap and the gain rises to the ceiling (noisier, but sharp in motion).
STREAM_SHUTTER_CAP = 16000.0   # us — MAX shutter time of the stream (~1/62 s), against blur
STREAM_SHUTTER_MIN = 1500.0    # us — may slide down to this in a bright room
STREAM_GAIN_MAX = 32.0         # to limit noise (44 was already too noisy)
# MIRRORS the capped default of cam_source.sh (keep them in sync!) — the rolling
# corrector scales from here (otherwise the first correction would start from a wrong base).
STREAM_DEFAULT_GAIN = 24.0
STREAM_DEFAULT_SHUTTER = 16000.0
STREAM_DEFAULT_E = STREAM_DEFAULT_GAIN * STREAM_DEFAULT_SHUTTER
_running = True


def _stream_expo_to_gs(E):
    """For the STREAM: capped shutter + gain-led exposure (instead of
    capture._expo_to_gs, which aims for ~30 ms -> would blur in motion)."""
    E = max(1.0, E)
    gain = min(STREAM_GAIN_MAX, max(1.0, E / STREAM_SHUTTER_CAP))
    shutter = min(STREAM_SHUTTER_CAP, max(STREAM_SHUTTER_MIN, E / gain))
    return gain, shutter


def _load_timeout():
    try:
        with open(os.path.join(S.PI_ROOT, "config.json")) as f:
            return int(json.load(f).get("active_timeout_s", 60))
    except Exception:
        return 60


def _log(msg):
    try:
        with open(S.SUP_LOG, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------- process start
# Liveness is checked from the POPEN object (poll+reap), NOT from os.kill(pid,0): a
# defunct (zombie) child still looks "alive" to os.kill, so it would never be restarted.
# The pidfile is only for external tools (stream_ctl status/stop).
_go2rtc_proc = None
_consumer_proc = None


def _spawn(cmd, pidfile, logfile, cwd=None):
    lf = open(logfile, "ab", buffering=0)
    p = subprocess.Popen(
        cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
        start_new_session=True,   # own session -> not killed when the parent dies
    )
    S.write_pid(pidfile, p.pid)
    return p


def _proc_up(p):
    return p is not None and p.poll() is None   # poll() also reaps the zombie


def _go2rtc_up():
    return _proc_up(_go2rtc_proc)


def _consumer_up():
    return _proc_up(_consumer_proc)


def _port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _start_go2rtc():
    global _go2rtc_proc
    if _go2rtc_up():
        return
    _go2rtc_proc = _spawn([S.GO2RTC_BIN, "-config", S.GO2RTC_CFG], S.GO2RTC_PID,
                          S.GO2RTC_LOG, cwd=S.STREAM_DIR)
    _log("go2rtc started")


def _stop_go2rtc():
    global _go2rtc_proc
    if _proc_up(_go2rtc_proc):
        try:
            _go2rtc_proc.terminate()
            _go2rtc_proc.wait(timeout=3)
        except Exception:
            try:
                _go2rtc_proc.kill()
            except Exception:
                pass
        _log("go2rtc stopped")
    _go2rtc_proc = None
    S.kill_pidfile(S.GO2RTC_PID)


def _start_consumer():
    global _consumer_proc
    if _consumer_up():
        return
    # go2rtc must already be LISTENing, otherwise ffmpeg dies immediately
    # (Connection refused) and stays a zombie -> only if 8554 is open
    if not _port_open(8554):
        return   # retry in the next loop
    try:
        os.makedirs(S.RBUF_DIR, exist_ok=True)
        for f in glob.glob(os.path.join(S.RBUF_DIR, "seg_*.ts")):
            try:
                os.remove(f)   # fresh session -> do not mix old content into the replay
            except OSError:
                pass
    except OSError:
        pass
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-rtsp_transport", "tcp", "-i", S.RTSP,
        # 1) ring buffer: H.264 copy (CHEAP) -> 2 s segments, SEG_WRAP in a ring
        "-map", "0:v", "-c:v", "copy", "-f", "segment",
        "-segment_time", str(S.SEG_SECONDS), "-segment_wrap", str(S.SEG_WRAP),
        "-reset_timestamps", "1", "-segment_format", "mpegts",
        os.path.join(S.RBUF_DIR, "seg_%03d.ts"),
        # 2) latest.jpg: continuously refreshed single frame (the look fast path)
        "-map", "0:v", "-r", str(S.LATEST_FPS), "-q:v", "4", "-update", "1",
        "-y", S.LATEST,
    ]
    _consumer_proc = _spawn(cmd, S.FFMPEG_PID, S.FFMPEG_LOG)
    _log("consumer (ring+latest) started")


def _stop_consumer(release_camera=True):
    global _consumer_proc
    if _proc_up(_consumer_proc):
        try:
            _consumer_proc.terminate()
            _consumer_proc.wait(timeout=3)
        except Exception:
            try:
                _consumer_proc.kill()
            except Exception:
                pass
        _log("consumer stopped")
    _consumer_proc = None
    S.kill_pidfile(S.FFMPEG_PID)
    if release_camera:
        # go2rtc on-demand would kill rpicam only after a delay -> release it right away
        _pkill_rpicam()
    try:
        if os.path.exists(S.LATEST):
            os.remove(S.LATEST)   # an old frame must not look "fresh"
    except OSError:
        pass


def _pkill_rpicam():
    # exact-name kill (NOT -f, so it does not match our own command line)
    try:
        subprocess.run(["pkill", "-x", "rpicam-vid"], timeout=3)
    except Exception:
        pass


# ---------------------------------------------------------------- rolling exposure
_last_expo_check = 0.0
_expo_E = STREAM_DEFAULT_E   # current exposure estimate (gain*us); starts from the cam_source
                            # capped default -> the first correction scales from a good base


def _sample_mean():
    try:
        import numpy as np
        from PIL import Image
        return float(np.asarray(Image.open(S.LATEST).convert("L")).mean())
    except Exception:
        return None


def _rolling_exposure():
    """Infrequent, histogram-based correction (a rolling variant of the capture.py logic).
    If latest.jpg is persistently under-/overexposed, sets a manual exposure and
    restarts the source (rpicam kill -> go2rtc re-exec with the new expo.env)."""
    global _last_expo_check, _expo_E
    now = time.time()
    if now - _last_expo_check < EXPO_INTERVAL_S:
        return
    _last_expo_check = now
    m = _sample_mean()
    if m is None or m < 1:
        return
    try:
        from core.capture import TARGET, E_START, E_LO, E_HI, _expo_to_gs
    except Exception:
        return
    # within the band -> nothing to do (hysteresis, so it does not flap)
    if 0.60 * TARGET <= m <= 1.55 * TARGET:
        return
    base = _expo_E if _expo_E else E_START
    newE = min(E_HI, max(E_LO, base * (TARGET / m)))
    # only write/restart on a meaningful change (>25%)
    if _expo_E and abs(newE - _expo_E) / _expo_E < 0.25:
        return
    g, sh = _stream_expo_to_gs(newE)   # capped shutter (against blur), gain carries the light
    try:
        with open(S.EXPO_ENV, "w") as f:
            f.write(f"GAIN={g:.2f}\nSHUTTER={int(sh)}\n")
    except Exception:
        return
    _expo_E = newE
    _pkill_rpicam()   # go2rtc re-execs cam_source.sh with the new exposure
    _log(f"rolling expo: mean={m:.0f} -> gain={g:.2f} shutter={int(sh)}")


# ---------------------------------------------------------------- main loop
def _desired(timeout_s):
    if S.flag(S.OFF_FLAG):
        return "off"
    if S.flag(S.PAUSE_FLAG):
        return "paused"
    if S.lease_active() or S.nav_active():  # capture burst or nav mode -> stream paused
        return "paused"
    if S.flag(S.ON_FLAG):
        return "on"
    if S.active_mode(timeout_s):
        return "on"
    return "off"


def _cont_go2rtc():
    """SIGCONT to go2rtc (the capture lease STOPs it so it does not respawn the
    source even with a viewer attached). No-op for a running process; silently nothing if absent."""
    try:
        subprocess.run(["pkill", "-CONT", "-x", "go2rtc"], timeout=3)
    except Exception:
        pass


def _reconcile(state):
    if state == "on":
        _cont_go2rtc()             # if the lease froze it, thaw it
        _start_go2rtc()
        _start_consumer()
        _rolling_exposure()
    elif state == "paused":
        if S.lease_active() or S.nav_active():
            pass                   # capture burst / nav: go2rtc stays FROZEN (must not respawn)
        else:
            _cont_go2rtc()
            _start_go2rtc()        # manual PAUSE: go2rtc stays (fast restore)
        _stop_consumer(release_camera=True)
    else:  # off
        _cont_go2rtc()             # a STOPped process only receives TERM after CONT
        _stop_consumer(release_camera=True)
        _stop_go2rtc()
        _pkill_rpicam()            # make sure there is no orphan


def _cleanup(*_a):
    global _running
    _running = False


def main():
    # single-instance guard
    old = S.read_pid(S.SUP_PID)
    if old and S.pid_alive(old) and old != os.getpid():
        print(f"stream_supervisor already running (pid {old})", file=sys.stderr)
        return
    S.write_pid(S.SUP_PID, os.getpid())
    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)
    timeout_s = _load_timeout()
    # the first rolling-expo correction should not wait 20 s: the capped default (gain 24 /
    # 16 ms) is already blur-free, but light tuning should be quicker (~5 s).
    global _last_expo_check
    _last_expo_check = time.time() - (EXPO_INTERVAL_S - 5.0)
    _log(f"supervisor start (pid {os.getpid()}, active_timeout={timeout_s}s)")
    try:
        while _running:
            try:
                _reconcile(_desired(timeout_s))
            except Exception as e:
                _log(f"reconcile error: {e}")
            time.sleep(LOOP_S)
    finally:
        # clean shutdown: every stream process down, camera free
        _cont_go2rtc()
        _stop_consumer(release_camera=True)
        _stop_go2rtc()
        _pkill_rpicam()
        try:
            os.remove(S.SUP_PID)
        except OSError:
            pass
        _log("supervisor stopped, all stream processes down")


if __name__ == "__main__":
    main()
