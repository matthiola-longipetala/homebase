"""stream_lib.py — shared state and primitives for the live video stream.

Stdlib only (imported by tools/look.py, tools/scan.py, stream_supervisor.py and
stream_ctl.py alike). No heavy dependencies on the hot path.

Architecture in brief:
  - A single go2rtc service owns the camera (cam_source.sh -> rpicam-vid
    HW H.264). On demand: rpicam only runs while there is a consumer.
  - In active mode stream_supervisor keeps one persistent ffmpeg consumer running
    (ring-buffer .ts segments + a continuously refreshed /tmp/stream_latest.jpg).
    So the camera is live while active and released while passive (following the
    existing active/passive pattern: /tmp/robot_active_at mtime + active_timeout_s).
  - look/scan get a fresh frame INSTANTLY from stream_latest.jpg (fast path);
    if the stream is not running, the original rpicam-still path works unchanged.
"""
import os
import shutil
import signal
import time

# --- Paths ---
PI_ROOT = os.path.expanduser("~/picarx-mcp")
STREAM_DIR = os.path.join(PI_ROOT, "stream")
GO2RTC_BIN = os.path.join(STREAM_DIR, "go2rtc")
GO2RTC_CFG = os.path.join(STREAM_DIR, "go2rtc.yaml")

RBUF_DIR = "/tmp/rbuf"
LATEST = "/tmp/stream_latest.jpg"
EXPO_ENV = "/tmp/stream_expo.env"

# --- Flag files (touch-file pattern, same as hangkep_off) ---
ACTIVE_STAMP = "/tmp/robot_active_at"   # refreshed by mark_chat_active() (active mode)
OFF_FLAG = "/tmp/stream_off"            # manual off (overrides everything)
ON_FLAG = "/tmp/stream_on"              # manual on (independent of active mode)
PAUSE_FLAG = "/tmp/stream_pause"        # temporary camera release (for an on-demand photo)
CAP_LEASE = "/tmp/stream_cap_lease"     # camera lease: refreshed by core.capture.capture()
LEASE_TTL = 4.0                         # lease expires after this many s without a capture -> stream back
NAV_FLAG = "/tmp/stream_nav"            # NAV MODE: the stream stays paused for the whole nav burst
NAV_TTL = 30.0                         # nav mode expires after this many s without a capture -> stream back

# --- Pidfiles ---
SUP_PID = "/tmp/stream_supervisor.pid"
GO2RTC_PID = "/tmp/stream_go2rtc.pid"
FFMPEG_PID = "/tmp/stream_ffmpeg.pid"

# --- Logs ---
GO2RTC_LOG = "/tmp/stream_go2rtc.log"
FFMPEG_LOG = "/tmp/stream_ffmpeg.log"
SUP_LOG = "/tmp/stream_supervisor.log"

# --- Network ---
API = "http://127.0.0.1:1984"
RTSP = "rtsp://127.0.0.1:8554/robot"
# The robot's LAN address, used only to print viewer URLs. Taken from the PI_LAN_IP
# environment variable, else from "pi_host" in ~/picarx-mcp/config.json, else a placeholder.
def _lan_ip():
    if os.environ.get("PI_LAN_IP"):
        return os.environ["PI_LAN_IP"]
    try:
        import json
        with open(os.path.join(PI_ROOT, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("pi_host") or "<robot-ip>"
    except Exception:
        return "<robot-ip>"


LAN_IP = _lan_ip()
VIEWER_URL = f"http://{LAN_IP}:1984/stream.html?src=robot"  # go2rtc built-in player

# --- Ring-buffer parameters ---
SEG_SECONDS = 2
SEG_WRAP = 150
                              # (150 x 2 s = ~5 min; ~60 s was too short, the event -> notification
                              # -> reasoning -> SSH chain can take 1-2 minutes; ~150 MB tmpfs,
                              # no extra CPU, H.264 copy)
LATEST_FPS = 2                # refresh rate of stream_latest.jpg (CPU-friendly)

FRAME_FRESH_S = 2.5           # latest.jpg fresher than this -> the stream is "live"


# ---------------------------------------------------------------- pid handling
def read_pid(pidfile):
    try:
        with open(pidfile) as f:
            return int(f.read().strip())
    except Exception:
        return None


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def write_pid(pidfile, pid):
    try:
        with open(pidfile, "w") as f:
            f.write(str(pid))
    except Exception:
        pass


def kill_pidfile(pidfile, sig=signal.SIGTERM, wait_s=3.0):
    """Stop the process named in the pidfile, then delete the pidfile."""
    pid = read_pid(pidfile)
    if pid and pid_alive(pid):
        try:
            os.kill(pid, sig)
        except OSError:
            pass
        t0 = time.time()
        while time.time() - t0 < wait_s and pid_alive(pid):
            time.sleep(0.1)
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    try:
        os.remove(pidfile)
    except OSError:
        pass


# ---------------------------------------------------------------- state
def _mtime_age(path):
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def is_stream_up():
    """Is the stream CONSUMABLE right NOW: latest.jpg exists and is fresh.

    This is the ONLY thing the hot path (look) checks — if the frame is fresh,
    the stream is live and flowing; if not, it falls back to the original rpicam-still path.
    """
    age = _mtime_age(LATEST)
    return age is not None and age <= FRAME_FRESH_S


def grab_latest(dst):
    """Copy the latest stream frame to dst. True if it succeeded (fresh)."""
    if not is_stream_up():
        return False
    try:
        shutil.copyfile(LATEST, dst)
        return True
    except Exception:
        return False


def active_mode(timeout_s):
    """Active mode: /tmp/robot_active_at is fresher than timeout_s (mark_chat_active)."""
    age = _mtime_age(ACTIVE_STAMP)
    return age is not None and age <= timeout_s


def flag(path):
    return os.path.exists(path)


def set_flag(path, on):
    if on:
        try:
            open(path, "w").close()
        except Exception:
            pass
    else:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------- camera release
def rpicam_running():
    """Is any rpicam-vid (the stream source) running."""
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm") as f:
                    if f.read().strip().startswith("rpicam"):
                        return True
            except OSError:
                continue
    except OSError:
        pass
    return False


def pause_camera(timeout_s=3.0):
    """Ask the supervisor to temporarily release the camera (PAUSE_FLAG) so an
    on-demand rpicam-still (adaptive 'special eye') or a servo-moving look can
    run. Returns True if the camera was actually freed.

    If no stream (supervisor) is running, returns True immediately (nothing to release)."""
    if not is_stream_up() and not rpicam_running():
        return True
    set_flag(PAUSE_FLAG, True)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if not rpicam_running():
            return True
        time.sleep(0.15)
    return not rpicam_running()


def resume_camera():
    """Restore the stream after a pause (the supervisor restarts the consumer,
    and go2rtc re-execs cam_source.sh)."""
    set_flag(PAUSE_FLAG, False)


# ---------------------------------------------------------------- camera lease
# The standalone tag/nav scripts (whereami, face_tag, turn_scout, look3...) call
# core.capture.capture(), which starts its own rpicam-still -> it would COLLIDE
# with the stream. Solution: a self-expiring "lease". capture() refreshes the lease
# before every shot; the supervisor treats a fresh lease as PAUSE (releases the
# camera). At the end of a scan (no more captures for ~LEASE_TTL s) the lease
# expires and the supervisor restores the stream on its own. So there is NO
# per-frame pause/resume churn, and the calling script needs NO changes.
def lease_refresh():
    try:
        with open(CAP_LEASE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def lease_active(ttl=LEASE_TTL):
    age = _mtime_age(CAP_LEASE)
    return age is not None and age <= ttl


# ---------------------------------------------------------------- NAV mode
# The camera lease (LEASE_TTL 4 s) covers ONE capture burst: between two nav fixes
# (>4 s) the stream partly comes back and the next fix stops it again -> "pumping"
# (CPU thrash + each fix 2-3 s slower). NAV mode is a LONGER (NAV_TTL) flag that is
# turned on EXPLICITLY at the start of a nav leg (stream_ctl.py nav on); every
# capture EXTENDS it (does not ACTIVATE it — so a standalone whereami/look is
# unchanged and comes back with the 4 s lease). During nav the stream stays paused
# THROUGHOUT (go2rtc frozen, no per-frame up/down churn) and is restored ONCE at the
# end of the leg (nav off or TTL expiry). By default the nav flag is ABSENT -> behaviour unchanged.
def nav_active(ttl=NAV_TTL):
    age = _mtime_age(NAV_FLAG)
    return age is not None and age <= ttl


def nav_refresh():
    try:
        with open(NAV_FLAG, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def nav_off():
    try:
        os.remove(NAV_FLAG)
    except OSError:
        pass


def acquire_for_capture(timeout_s=2.5):
    """Called by core.capture.capture(): makes the stream release the camera for an
    rpicam-still. Returns immediately if no stream is running. Refreshes the lease
    (the supervisor honours it as PAUSE), then — for speed — also stops the
    consumer and rpicam-vid directly, and waits until the camera is free.
    Never raises (must not break the capture)."""
    try:
        if not is_stream_up() and not rpicam_running():
            if nav_active():
                nav_refresh()   # nav leg: keep it alive (the stream is paused anyway)
            return True
        lease_refresh()
        if nav_active():
            nav_refresh()       # extend an already enabled nav mode (not activate it!)
        kill_pidfile(FFMPEG_PID, wait_s=1.5)   # the consumer releases the go2rtc source
        try:
            import subprocess
            subprocess.run(["pkill", "-x", "rpicam-vid"], timeout=3)
            # If a BROWSER viewer is watching the stream, go2rtc restarts the source
            # IMMEDIATELY (exec on demand) -> FREEZE it for the lease (SIGSTOP);
            # the supervisor thaws it with CONT when the lease expires.
            subprocess.run(["pkill", "-STOP", "-x", "go2rtc"], timeout=3)
        except Exception:
            pass
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            if not rpicam_running():
                return True
            time.sleep(0.15)
        return not rpicam_running()
    except Exception:
        return True   # better to attempt the shot than to crash
