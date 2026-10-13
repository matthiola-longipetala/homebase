#!/usr/bin/env python3
"""teleop_server.py — web-based TELEOP controller for the PiCar-X (imitation learning demo).

This is the agent's own tool: a human drives the robot from a laptop browser,
every command is LOGGED to a JSONL file, and the agent later analyses the log +
the stream's rolling buffer ("what was seen meanwhile") -> from the human's driving patterns
explicit rule/parameter/route fixes are distilled into TEXT/code (the end of the teaching
pipeline is ALWAYS an explicit artifact, otherwise it dies with the agent instance).

STDLIB only (http.server + threading) -> a single file has to be scp'd to the Pi;
the HTML/JS/CSS is embedded in this file. No Flask/websocket dependency (it may not
be installed on the Pi).

Safety (critical):
  - DEAD-MAN SWITCH: server-side watchdog — if no command arrives for ~500 ms,
    IMMEDIATE motor stop (steering and camera angle are NOT touched).
  - STICKY STEERING (at the owner's request): after releasing A/D the steering
    STAYS where it is; the X key brings it to centre (with a small ramp). The SPACE
    emergency stop ALSO centres the steering (in an emergency straight wheels are safest).
  - SONAR BRAKE forward: obstacle < 22 cm -> motor stop, BLOCKED state.
  - PUSH-THROUGH (at the owner's request): if forward is held for >=1.0 s AFTER a block
    of forward motion -> momentum burst (speed >=55, max ~1.5 s), ignoring the sonar
    (threshold crossing). Afterwards back to protected mode.
  - Reversing has no front sonar brake, but speed is capped at 45.

STEERING SERVO — WHY the servo writes work this way (v1.2, lessons from live runs):
  A) STICKING on small steps: the client inches the desired angle in 3-degree steps,
     but small nudges do NOT overcome the linkage friction -> the servo lags behind,
     may stick completely (observed: software said +25 while the wheel pointed left).
     Large, single jumps, however, are flawless. So the SERVER only allows
     DECISIVE writes: target deviation >= 8 degrees OR the desired angle has been
     unchanged for ~150 ms (the driver "stopped") -> then the EXACT target in one jump (see
     Controller._apply_steer). The client may change smoothly; the servo takes big steps.
  B) ZEROING ONLY FROM THE LEFT: arriving from the RIGHT (positive angle) the servo does
     not reach the physical centre -> zero must be approached from the left. So wherever we
     go to 0 (X centre, SPACE, exit, startup zeroing): if the servo is on the positive side,
     first -12 degrees, then 0 (two decisive steps, ~0.3 s apart); from the left directly
     0. The -12->0 steps are exempt from the 8-degree threshold (always written).

Usage on the Pi:
    cd ~/picarx-mcp/teleop && TELEOP_BIND=<robot-ip> python3 teleop_server.py
    -> open in a browser: http://<robot-ip>:8070   (windscreen: the go2rtc stream window)
    NO AUTHENTICATION: without TELEOP_BIND it listens on 127.0.0.1 only (this Pi). Set the Pi's LAN IP
    to drive from a phone on the home Wi-Fi; never on a network you don't control.

On a laptop (without hardware) the UI can be tried out:
    python teleop_server.py --dry-run
"""
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TELEOP_VERSION = "1.2"

# ---------------------------------------------------------------- parameters
PORT = 8070                 # go2rtc runs on 1984, do NOT touch that
WATCHDOG_S = 0.5            # dead-man stop after this much command silence
CONTROL_DT = 0.05          # control loop period (20 Hz)
STATE_SAMPLE_S = 0.2       # 5 Hz periodic "state" sampling (while moving)
ACTIVE_TOUCH_S = 10.0      # how often /tmp/robot_active_at is refreshed

SONAR_STOP_CM = 22.0       # forward: obstacle closer than this -> BLOCKED
OVERRIDE_HOLD_S = 1.0      # after a block, this much continuous forward press -> push-through
OVERRIDE_BURST_S = 1.5     # max length of the push-through burst
OVERRIDE_SPEED = 55        # minimum speed of the push-through burst
BACK_MAX_SPEED = 45        # reverse maximum

STEER_MAX = 25.0           # steering +-25 degrees
GEARS = {1: 30, 2: 45, 3: 60}   # speed gears for keys 1/2/3
DEFAULT_SPEED = 40

# DECISIVE steering-servo writes (lessons from live runs, see header):
# A) against sticking we only write to the servo in big steps or on settle
STEER_WRITE_STEP = 8.0     # target deviation >= this -> IMMEDIATE write (big, decisive jump)
STEER_SETTLE_S = 0.15      # desired angle unchanged for this long -> write the EXACT target
# B) zeroing only from the left (from the right the servo does not reach centre)
STEER_LEFT_ZERO = -12.0    # going to 0 from the right: first here (approach zero from the left)
STEER_ZERO_GAP_S = 0.3     # gap between the two steps -12 -> 0
STEER_ZERO_EPS = 1.0       # |angle| below this counts as "centre"

# Camera servo ranges (approximation of the PiCar-X hardware limits)
CAM_PAN_MAX = 90.0         # +-90 degrees pan
CAM_TILT_MIN = -35.0       # down
CAM_TILT_MAX = 65.0        # up

BATTERY_FILE = "/tmp/battery_status"     # "V PCT STATE"
ACTIVE_STAMP_FILE = "/tmp/robot_active_at"

# ---------------------------------------------------------------- stream-nav helper
# From the teleop/ folder, the parent folder + the stream/ folder go onto sys.path -> we use
# the CANONICAL nav helpers (stream_lib) via import if possible; otherwise fall back to the
# touch file (/tmp/stream_nav). This way the stream stays paused throughout movement.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_PARENT, os.path.join(_PARENT, "stream")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import stream_lib as _sl
    def nav_refresh():
        try: _sl.nav_refresh()
        except Exception: pass
    def nav_off():
        try: _sl.nav_off()
        except Exception: pass
    _NAV_SRC = "stream_lib"
except Exception:
    _NAV_FLAG = "/tmp/stream_nav"
    def nav_refresh():
        try:
            with open(_NAV_FLAG, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass
    def nav_off():
        try:
            os.remove(_NAV_FLAG)
        except OSError:
            pass
    _NAV_SRC = "fallback"


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


# ================================================================ CONTROLLER
class Controller:
    """The robot state machine + the SOLE owner of the hardware (Picarx is NOT
    thread-safe). Every hardware call and every log line happens in this thread;
    the HTTP handler only updates the shared 'desired' state under a lock."""

    def __init__(self, dry_run, log_dir):
        self.dry_run = dry_run
        self.log_dir = log_dir
        self.lock = threading.Lock()
        self.running = True

        self.desired = {
            "steer": 0.0,       # -25..25 (- left, + right)
            "throttle": 0,      # 1 forward, -1 back, 0 stop
            "speed": DEFAULT_SPEED,
            "cam_pan": 0.0,     # -90..90
            "cam_tilt": 0.0,    # -35..65
        }
        self.last_cmd_ts = time.time()
        self.pending_stop_reason = None   # signalled by the client (keyup/space/blur)
        self.sim_sonar = 200.0            # --dry-run only: simulated sonar (for UI testing)

        # state served (read) to the panel
        self.status = {
            "mode": "NORMAL", "sonar_cm": -1.0, "steer": 0.0, "speed": 0,
            "dir": "stop", "cam_pan": 0.0, "cam_tilt": 0.0,
            "blocked": False, "override": False, "watchdog": False,
            "moving": False, "battery": "", "logfile": "", "dry_run": dry_run,
            "version": TELEOP_VERSION,
        }

        # log
        os.makedirs(self.log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.logfile = os.path.join(self.log_dir, f"teleop_{stamp}.jsonl")
        self.logname = os.path.basename(self.logfile)
        self.status["logfile"] = self.logname
        self._logf = open(self.logfile, "a", encoding="utf-8")
        self._loglock = threading.Lock()

        # internal state-machine variables (written only by the control thread)
        self.mode = "NORMAL"       # NORMAL | BLOCKED | OVERRIDE
        self.blocked_since = 0.0
        self.override_start = 0.0
        self._applied_speed = None   # last (signed) speed written to the motor
        self._applied_steer = None   # last steering angle written to the SERVO
        self._steer_seen = None      # last seen desired angle (for settle time)
        self._steer_seen_since = 0.0 # when the desired angle last changed
        self._zero_stage = 0         # 0=none; 1=(-12 written) going to 0 in progress
        self._zero_stage_ts = 0.0    # time the -12 was written
        self._applied_pan = None
        self._applied_tilt = None
        self._last_logged_cmd = None
        self._last_sample = 0.0
        self._last_active_touch = 0.0
        self._batt_ts = 0.0

        # hardware
        self.px = None
        if not self.dry_run:
            from picarx import Picarx
            self.px = Picarx()
            # nudge the steering against sticking (pilot.py pattern), then ZERO FROM THE LEFT.
            # Rule (B): from the right the servo does not reach centre -> the
            # -12 -> 0 last two steps are the left-side approach. Steering zero is
            # CALIBRATED on the Pi -> only set_dir_servo_angle (we do not calibrate).
            self.px.set_dir_servo_angle(10);  time.sleep(0.2)
            self.px.set_dir_servo_angle(STEER_LEFT_ZERO); time.sleep(STEER_ZERO_GAP_S)
            self.px.set_dir_servo_angle(0)
            self._applied_steer = 0.0
            self.px.set_cam_pan_angle(0)
            self.px.set_cam_tilt_angle(0)
            self._applied_pan = 0.0
            self._applied_tilt = 0.0

    # ------------------------------------------------------------ log
    def log(self, obj):
        obj.setdefault("ts", time.time())
        line = json.dumps(obj, ensure_ascii=False)
        with self._loglock:
            self._logf.write(line + "\n")
            self._logf.flush()

    # ------------------------------------------------------------ hardware
    def _drive(self, speed_signed):
        if self._applied_speed == speed_signed:
            return
        self._applied_speed = speed_signed
        if self.dry_run:
            return
        try:
            self.px.forward(speed_signed)   # negative = backward
        except Exception as e:
            print(f"[teleop] motor error: {e}", file=sys.stderr)

    def _write_steer(self, angle, why):
        """The single, DECISIVE command WRITTEN to the servo (after the quantisation decision).
        Every steering write is logged (visible for analysis + dry-run smoke tests)."""
        prev = self._applied_steer
        self._applied_steer = angle
        self.log({"type": "steer_write", "angle": round(angle, 1),
                  "from": (round(prev, 1) if prev is not None else None), "why": why})
        if self.dry_run:
            print(f"[teleop][dry] steer_write {prev} -> {angle} ({why})", file=sys.stderr)
            return
        try:
            self.px.set_dir_servo_angle(angle)
        except Exception as e:
            print(f"[teleop] steering error: {e}", file=sys.stderr)

    def _apply_steer(self, target, now):
        """DECISIVE control of the steering servo (lessons from live runs, see header).

        A) STICKING: the client inches in 3-degree steps, but small steps do not
           overcome the linkage friction -> the servo lags/sticks. So we ONLY write
           to the servo if (a) the difference between the target and the last WRITTEN
           angle >= STEER_WRITE_STEP (8 degrees), OR (b) the desired angle has not changed
           for ~STEER_SETTLE_S (150 ms)
           (the driver "stopped" on an angle) -> then the EXACT target in one jump. So
           while driving it moves in big steps (no sticking), and is exact on release.
        B) ZEROING ONLY FROM THE LEFT: arriving from the RIGHT (positive angle) the servo
           does not reach centre. When going to 0 and the servo is on the POSITIVE side,
           we first jump to STEER_LEFT_ZERO (-12) degrees, then after STEER_ZERO_GAP_S (0.3 s)
           to 0 (two decisive steps). Arriving from the left (negative): directly 0.
           The -12->0 steps are EXEMPT from the 8-degree threshold (always written).
        """
        # track changes of the desired angle (for settle time)
        if self._steer_seen is None or abs(target - self._steer_seen) >= 0.5:
            self._steer_seen = target
            self._steer_seen_since = now
        settled = (now - self._steer_seen_since) >= STEER_SETTLE_S

        applied = self._applied_steer
        target_is_zero = abs(target) <= STEER_ZERO_EPS

        # --- B) two-step zeroing in progress (-12 -> 0) ---
        if self._zero_stage == 1:
            if target_is_zero:
                if (now - self._zero_stage_ts) >= STEER_ZERO_GAP_S:
                    self._write_steer(0.0, "zero")        # step 2: exact 0 (from the left)
                    self._zero_stage = 0
                return                                     # hold -12 until then
            self._zero_stage = 0                           # target moved away from 0 -> abort

        # --- unknown starting point (dry-run: init does not set it) -> write it ---
        if applied is None:
            self._write_steer(target, "init")
            return

        # --- B) going to 0: from the left or in two steps ---
        if target_is_zero:
            if abs(applied) <= STEER_ZERO_EPS:
                return                                     # already centred
            if not settled:
                return                                     # client ramp still running, wait
            if applied > STEER_ZERO_EPS:                   # coming from the RIGHT -> first -12
                self._write_steer(STEER_LEFT_ZERO, "zero_left")
                self._zero_stage = 1
                self._zero_stage_ts = now
            else:                                          # from the left -> directly 0
                self._write_steer(0.0, "zero")
            return

        # --- A) non-zero target: quantised, decisive write ---
        diff = abs(target - applied)
        if diff >= STEER_WRITE_STEP:
            self._write_steer(target, "step")              # big step -> immediately
        elif settled and diff >= 0.5:
            self._write_steer(target, "settle")            # stopped on an angle -> exact
        # otherwise: hold the last written angle (anti-stick protection)

    def _cam_to(self, pan, tilt):
        # The camera angle STAYS (not reset to zero after movement — the pilot.py
        # tilt-reset gotcha is NOT reproduced here).
        if self._applied_pan is None or abs(self._applied_pan - pan) >= 0.5:
            self._applied_pan = pan
            if not self.dry_run:
                try: self.px.set_cam_pan_angle(pan)
                except Exception as e: print(f"[teleop] cam_pan error: {e}", file=sys.stderr)
        if self._applied_tilt is None or abs(self._applied_tilt - tilt) >= 0.5:
            self._applied_tilt = tilt
            if not self.dry_run:
                try: self.px.set_cam_tilt_angle(tilt)
                except Exception as e: print(f"[teleop] cam_tilt error: {e}", file=sys.stderr)

    def _read_sonar(self):
        if self.dry_run:
            return self.sim_sonar
        try:
            d = self.px.ultrasonic.read()
        except Exception:
            return -1.0
        return d if (d is not None and d > 0) else -1.0

    def _read_battery(self):
        try:
            return open(BATTERY_FILE).read().strip()
        except Exception:
            return ""

    def _touch_active(self):
        try:
            with open(ACTIVE_STAMP_FILE, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass

    # ------------------------------------------------------------ control loop
    def run(self):
        self.log({"type": "session_start", "version": TELEOP_VERSION, "dry_run": self.dry_run,
                  "params": {"watchdog_s": WATCHDOG_S, "sonar_stop_cm": SONAR_STOP_CM,
                             "override_hold_s": OVERRIDE_HOLD_S, "override_burst_s": OVERRIDE_BURST_S,
                             "override_speed": OVERRIDE_SPEED, "back_max_speed": BACK_MAX_SPEED,
                             "steer_max": STEER_MAX, "gears": GEARS, "nav_src": _NAV_SRC,
                             "steer_write_step": STEER_WRITE_STEP, "steer_settle_s": STEER_SETTLE_S,
                             "steer_left_zero": STEER_LEFT_ZERO, "steer_zero_gap_s": STEER_ZERO_GAP_S},
                  "logfile": self.logname})
        try:
            while self.running:
                self._tick()
                time.sleep(CONTROL_DT)
        finally:
            self._safe_stop()

    def _tick(self):
        now = time.time()
        with self.lock:
            d = dict(self.desired)
            last_cmd = self.last_cmd_ts
            stop_reason = self.pending_stop_reason

        watchdog = (now - last_cmd) > WATCHDOG_S
        sonar = self._read_sonar()
        sonar_blocked = (sonar > 0 and sonar < SONAR_STOP_CM)

        # --- camera: always followed (dead-man/stop does NOT affect it) ---
        pan = _clamp(d["cam_pan"], -CAM_PAN_MAX, CAM_PAN_MAX)
        tilt = _clamp(d["cam_tilt"], CAM_TILT_MIN, CAM_TILT_MAX)
        self._cam_to(pan, tilt)

        # --- motor state machine ---
        thr = d["throttle"]
        sp = int(d["speed"])
        steer = _clamp(d["steer"], -STEER_MAX, STEER_MAX)
        eff = 0   # signed effective speed

        if watchdog:
            # DEAD-MAN: command silence -> immediate MOTOR stop. Steering and
            # camera are NOT touched (sticky steering): the servo stays
            # where it last was -- only the drive stops.
            if self.mode != "NORMAL":
                self.mode = "NORMAL"
            eff = 0
        elif thr == 1:
            # FORWARD
            self._apply_steer(steer, now)
            if self.mode == "OVERRIDE" and (now - self.override_start) >= OVERRIDE_BURST_S:
                self.log({"type": "override_burst", "phase": "end", "reason": "timeout",
                          "dur": round(now - self.override_start, 3)})
                self.mode = "NORMAL"
            if self.mode == "OVERRIDE":
                eff = max(sp, OVERRIDE_SPEED)          # sonar is IGNORED
            elif self.mode == "BLOCKED":
                if (now - self.blocked_since) >= OVERRIDE_HOLD_S:
                    self.mode = "OVERRIDE"
                    self.override_start = now
                    eff = max(sp, OVERRIDE_SPEED)
                    self.log({"type": "override_burst", "phase": "start",
                              "speed": eff, "sonar_cm": round(sonar, 1)})
                elif not sonar_blocked:
                    self.mode = "NORMAL"               # obstacle gone -> continue
                    eff = sp
                else:
                    eff = 0
            else:  # NORMAL
                if sonar_blocked:
                    self.mode = "BLOCKED"
                    self.blocked_since = now
                    eff = 0
                    self.log({"type": "sonar_block", "sonar_cm": round(sonar, 1),
                              "speed": sp, "steer": round(steer, 1)})
                else:
                    eff = sp
        elif thr == -1:
            # BACKWARD (no front sonar brake; speed max 45)
            self.mode = "NORMAL"
            self._apply_steer(steer, now)
            eff = -min(sp, BACK_MAX_SPEED)
        else:
            # STOP intent
            self.mode = "NORMAL"
            self._apply_steer(steer, now)
            eff = 0

        # --- motor output + stop-event log (edge-triggered) ---
        was_moving = (self._applied_speed not in (None, 0))
        self._drive(eff)
        if was_moving and eff == 0:
            reason = "watchdog" if watchdog else (stop_reason or "release")
            self.log({"type": "stop", "reason": reason})
            with self.lock:
                self.pending_stop_reason = None

        moving = (eff != 0)

        # --- active-mode stamp (the NAV flag is NOT used in teleop: to the supervisor NAV mode
        # means "stream paused, camera belongs to nav photos" -- in teleop the stream is the windscreen)
        if (now - self._last_active_touch) >= ACTIVE_TOUCH_S:
            self._touch_active()
            self._last_active_touch = now

        dir_str = "fwd" if thr == 1 else ("back" if thr == -1 else "stop")
        cmd_key = (round(steer), sp, dir_str, round(pan), round(tilt))
        if cmd_key != self._last_logged_cmd:
            self._last_logged_cmd = cmd_key
            self.log({"type": "cmd", "steer": round(steer, 1), "speed": sp, "dir": dir_str,
                      "cam_pan": round(pan, 1), "cam_tilt": round(tilt, 1)})

        # --- "state": 5 Hz periodic sampling, WHILE moving ---
        if moving and (now - self._last_sample) >= STATE_SAMPLE_S:
            self._last_sample = now
            self.log({"type": "state", "steer": round(steer, 1), "speed": abs(eff),
                      "dir": dir_str, "sonar_cm": round(sonar, 1),
                      "cam_pan": round(pan, 1), "cam_tilt": round(tilt, 1)})

        # --- battery every ~5 s ---
        if (now - self._batt_ts) >= 5.0:
            self._batt_ts = now
            batt = self._read_battery()
        else:
            batt = self.status.get("battery", "")

        # --- update panel state ---
        with self.lock:
            self.status.update({
                "mode": self.mode, "sonar_cm": round(sonar, 1),
                "steer": round(steer, 1), "speed": abs(eff), "dir": dir_str,
                "cam_pan": round(pan, 1), "cam_tilt": round(tilt, 1),
                "blocked": self.mode == "BLOCKED", "override": self.mode == "OVERRIDE",
                "watchdog": watchdog, "moving": moving, "battery": batt,
            })

    # ------------------------------------------------------------ HTTP-side API
    def set_desired(self, data):
        """Called by the client's POST /cmd: updates the desired state + the command timestamp."""
        now = time.time()
        with self.lock:
            for k in ("steer", "throttle", "speed", "cam_pan", "cam_tilt"):
                if k in data:
                    try:
                        self.desired[k] = float(data[k]) if k != "throttle" else int(data[k])
                    except (TypeError, ValueError):
                        pass
            if data.get("event") in ("keyup", "space", "blur", "stop"):
                self.pending_stop_reason = data["event"]
            if self.dry_run and "sim_sonar" in data:
                try: self.sim_sonar = float(data["sim_sonar"])
                except (TypeError, ValueError): pass
            self.last_cmd_ts = now
            return dict(self.status)

    def get_status(self):
        with self.lock:
            return dict(self.status)

    def add_note(self, text):
        text = (text or "").strip()
        if text:
            self.log({"type": "note", "text": text})
        return True

    # ------------------------------------------------------------ shutdown
    def _safe_stop(self):
        try:
            self._drive(0)
            if not self.dry_run and self.px is not None:
                # steering to centre FROM THE LEFT (rule B): if coming from the right (positive
                # angle), first -12, then 0; from the left directly 0. (camera STAYS)
                if self._applied_steer is not None and self._applied_steer > STEER_ZERO_EPS:
                    self.px.set_dir_servo_angle(STEER_LEFT_ZERO)
                    time.sleep(STEER_ZERO_GAP_S)
                self.px.set_dir_servo_angle(0)
                self._applied_steer = 0.0
        except Exception:
            pass
        nav_off()
        self.log({"type": "session_end", "version": TELEOP_VERSION})
        try:
            self._logf.flush(); self._logf.close()
        except Exception:
            pass

    def shutdown(self):
        self.running = False


# ================================================================ HTTP
CTRL = None   # global controller (accessed by the handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass   # silent (the log is the JSONL)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/status":
            self._send(200, json.dumps(CTRL.get_status(), ensure_ascii=False))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        # Same-origin only: a page from another site, open in a browser on the LAN, must not drive the robot.
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1].rstrip("/") != (self.headers.get("Host") or ""):
            self._send(403, json.dumps({"error": "cross-origin request refused"}))
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            n = 0
        raw = self.rfile.read(n) if n > 0 else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}
        if path == "/cmd":
            st = CTRL.set_desired(data)
            self._send(200, json.dumps(st, ensure_ascii=False))
        elif path == "/note":
            CTRL.add_note(data.get("text", ""))
            self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, json.dumps({"error": "not found"}))


# ================================================================ EMBEDDED PAGE
PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PiCar-X TELEOP</title>
<style>
  :root { --bg:#111418; --panel:#1b2027; --fg:#eef2f6; --muted:#8b95a1;
          --ok:#3ddc84; --warn:#ffb020; --block:#ff4d4d; --over:#ff8c00; --accent:#4aa3ff; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif; }
  .wrap { max-width:820px; margin:0 auto; padding:16px; }
  h1 { font-size:22px; margin:4px 0 12px; }
  .logfile { color:var(--muted); font-size:14px; margin-bottom:12px; word-break:break-all; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; }
  .tile { background:var(--panel); border-radius:12px; padding:12px 14px; }
  .tile .k { color:var(--muted); font-size:13px; text-transform:uppercase; letter-spacing:.04em; }
  .tile .v { font-size:30px; font-weight:700; margin-top:4px; }
  #mode { border-radius:12px; padding:14px 16px; margin:12px 0; text-align:center;
          font-size:30px; font-weight:800; background:#20323f; }
  #mode.normal { background:#1f3a2a; color:var(--ok); }
  #mode.blocked { background:#3a1f1f; color:var(--block); }
  #mode.override { background:#3a2a12; color:var(--over); }
  #mode.watchdog { background:#3a3416; color:var(--warn); }
  #stopbtn { width:100%; padding:22px; font-size:30px; font-weight:800; color:#fff;
             background:var(--block); border:none; border-radius:14px; margin:12px 0;
             cursor:pointer; letter-spacing:.05em; }
  #stopbtn:active { filter:brightness(.85); }
  .help { background:var(--panel); border-radius:12px; padding:12px 16px; margin:12px 0; font-size:16px; }
  .help b { color:var(--accent); }
  .help kbd { background:#2a323c; border-radius:6px; padding:2px 8px; font-family:monospace; }
  .note { display:flex; gap:8px; margin:12px 0; }
  .note input { flex:1; padding:12px; font-size:16px; border-radius:10px; border:1px solid #2a323c;
                background:#0e1216; color:var(--fg); }
  .note button { padding:12px 18px; font-size:16px; border-radius:10px; border:none;
                 background:var(--accent); color:#04121f; font-weight:700; cursor:pointer; }
  .foot { color:var(--muted); font-size:13px; margin-top:10px; }
  .dot { display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; }
  .dot.on { background:var(--ok); } .dot.off { background:#555; }
</style>
</head>
<body>
<div class="wrap">
  <h1>PiCar-X TELEOP <span id="dry" style="color:var(--warn);font-size:15px"></span></h1>
  <div class="logfile">Log: <span id="logfile">…</span></div>

  <div id="mode" class="normal">NORMAL</div>

  <button id="stopbtn">■ STOP (Space)</button>

  <div class="grid">
    <div class="tile"><div class="k">Direction</div><div class="v" id="dir">–</div></div>
    <div class="tile"><div class="k">Speed</div><div class="v" id="speed">0</div></div>
    <div class="tile"><div class="k">Steering°</div><div class="v" id="steer">0</div></div>
    <div class="tile"><div class="k">Sonar cm</div><div class="v" id="sonar">–</div></div>
    <div class="tile"><div class="k">Camera pan°</div><div class="v" id="pan">0</div></div>
    <div class="tile"><div class="k">Camera tilt°</div><div class="v" id="tilt">0</div></div>
    <div class="tile"><div class="k">Gear</div><div class="v" id="gear">–</div></div>
    <div class="tile"><div class="k">Battery</div><div class="v" id="batt" style="font-size:20px">–</div></div>
  </div>

  <div class="help">
    <b>W</b> forward &nbsp; <b>S</b> back &nbsp; <b>A/D</b> steer left/right (gradual, max ±25° — STAYS on release) &nbsp;
    <b>X</b> centre steering &nbsp; <b>Space</b> STOP + centre steering<br>
    <b>1/2/3</b> speed 30/45/60 &nbsp; <b>←/→</b> camera pan &nbsp; <b>↑/↓</b> camera tilt &nbsp;
    <b>R</b> centre camera<br>
    <span class="foot">Releasing the throttle → stop (steering and camera stay put). Windscreen: the stream window.</span>
  </div>

  <div id="simrow" style="display:none" class="help">
    <b>[dry-run]</b> simulated sonar: <input id="simsonar" type="range" min="5" max="200" value="200"
      style="width:60%;vertical-align:middle"> <span id="simval">200</span> cm
    <span class="foot">(BLOCKED &lt;22 cm; hold W &gt;1 s → OVERRIDE)</span>
  </div>

  <div class="note">
    <input id="notetext" type="text" placeholder="Note to the log (Enter)…">
    <button id="notebtn">Send</button>
  </div>

  <div class="foot">
    <span class="dot" id="conn"></span><span id="connlbl">connecting…</span>
    &nbsp;·&nbsp; teleop v<span id="ver">?</span> &nbsp;·&nbsp; port 8070
  </div>
</div>

<script>
(function(){
  "use strict";
  var STEER_MAX=25, PAN_MAX=90, TILT_MIN=-35, TILT_MAX=65;
  var GEARS={1:30,2:45,3:60};

  var keys={};           // pressed keys
  var steer=0;           // -25..25
  var speed=40, gear=0;  // gear 0 = default 40
  var camPan=0, camTilt=0;
  var stopLatch=false;   // after STOP button / space: W disabled until pressed again
  var centering=false;   // after X / SPACE: steering ramps to centre
  var dryRun=false;

  // gradual ramp steps (every 50 ms)
  var STEER_STEP=3, STEER_RETURN=4.5;   // steering deflection / X-SPACE centring ramp
  var CAM_STEP=3.5;                     // camera pan/tilt (camera does NOT return)

  function clamp(v,lo,hi){ return v<lo?lo:(v>hi?hi:v); }

  function physics(){
    // steering: A/D step by step; on release it STAYS (sticky steering, at the
    // owner's request). To centre: X or SPACE -> small ramp (no jump).
    if(keys["a"]){ centering=false; steer=clamp(steer-STEER_STEP,-STEER_MAX,STEER_MAX); }
    else if(keys["d"]){ centering=false; steer=clamp(steer+STEER_STEP,-STEER_MAX,STEER_MAX); }
    else if(centering){
      if(steer>0) steer=Math.max(0,steer-STEER_RETURN);
      else if(steer<0) steer=Math.min(0,steer+STEER_RETURN);
      if(steer===0) centering=false;
    }
    // camera pan: arrow left/right, STAYS where it is
    if(keys["arrowleft"]) camPan=clamp(camPan-CAM_STEP,-PAN_MAX,PAN_MAX);
    if(keys["arrowright"]) camPan=clamp(camPan+CAM_STEP,-PAN_MAX,PAN_MAX);
    // camera tilt: arrow up/down
    if(keys["arrowup"]) camTilt=clamp(camTilt+CAM_STEP,TILT_MIN,TILT_MAX);
    if(keys["arrowdown"]) camTilt=clamp(camTilt-CAM_STEP,TILT_MIN,TILT_MAX);
  }

  function throttle(){
    if(stopLatch) return 0;
    if(keys["w"]) return 1;
    if(keys["s"]) return -1;
    return 0;
  }

  function payload(extra){
    var p={ steer:Math.round(steer), throttle:throttle(), speed:speed,
            cam_pan:Math.round(camPan), cam_tilt:Math.round(camTilt) };
    if(dryRun){ var s=document.getElementById("simsonar"); if(s) p.sim_sonar=parseFloat(s.value); }
    if(extra) for(var k in extra) p[k]=extra[k];
    return p;
  }

  var conn=document.getElementById("conn"), connlbl=document.getElementById("connlbl");
  function send(extra){
    fetch("/cmd",{method:"POST",headers:{"Content-Type":"application/json"},
                  body:JSON.stringify(payload(extra))})
      .then(function(r){return r.json();})
      .then(function(st){ paint(st); conn.className="dot on"; connlbl.textContent="connected"; })
      .catch(function(){ conn.className="dot off"; connlbl.textContent="NO connection"; });
  }

  function paint(st){
    if(!st) return;
    var m=document.getElementById("mode");
    if(st.watchdog){ m.className="watchdog"; m.textContent="DEAD-MAN STOP"; }
    else if(st.override){ m.className="override"; m.textContent="PUSH-THROUGH ▶▶"; }
    else if(st.blocked){ m.className="blocked"; m.textContent="BLOCKED (sonar)"; }
    else { m.className="normal"; m.textContent="NORMAL"; }
    var dirmap={fwd:"FORWARD ▲",back:"BACK ▼",stop:"stopped"};
    document.getElementById("dir").textContent=dirmap[st.dir]||st.dir;
    document.getElementById("speed").textContent=st.speed;
    document.getElementById("steer").textContent=st.steer;
    document.getElementById("sonar").textContent=(st.sonar_cm>0?st.sonar_cm:"–");
    document.getElementById("pan").textContent=st.cam_pan;
    document.getElementById("tilt").textContent=st.cam_tilt;
    document.getElementById("batt").textContent=st.battery||"–";
    document.getElementById("logfile").textContent=st.logfile||"…";
    document.getElementById("ver").textContent=st.version||"?";
    document.getElementById("gear").textContent=speed;
    if(st.dry_run && !dryRun){ dryRun=true;
      document.getElementById("dry").textContent="[DRY-RUN — no hardware]";
      document.getElementById("simrow").style.display="block"; }
  }

  // --- keys ---
  var MOVE=["w","s","a","d","arrowleft","arrowright","arrowup","arrowdown"];
  window.addEventListener("keydown",function(e){
    var k=e.key.toLowerCase();
    if(k===" "||e.code==="Space"){ e.preventDefault(); doStop("space"); return; }
    if(k==="x"){ centering=true; send(); return; }     // centre steering (with ramp)
    if(k==="r"){ camPan=0; camTilt=0; send(); return; }
    if(k==="1"||k==="2"||k==="3"){ speed=GEARS[k]; document.getElementById("gear").textContent=speed; send(); return; }
    if(MOVE.indexOf(k)>=0){
      e.preventDefault();
      if((k==="w"||k==="s")) stopLatch=false;   // a new movement command releases the latch
      if(!keys[k]){ keys[k]=true; send(); }
    }
  });
  window.addEventListener("keyup",function(e){
    var k=e.key.toLowerCase();
    if(MOVE.indexOf(k)>=0){ keys[k]=false;
      if(k==="w"||k==="s") send({event:"keyup"});   // immediate stop signal
      else send();
    }
  });
  window.addEventListener("blur",function(){
    keys={}; stopLatch=true; send({event:"blur"});   // focus lost -> stop
  });

  function doStop(reason){
    // emergency stop: motor off + steering ALSO to centre (in an emergency straight
    // wheels are safest) -- centring uses the same small ramp
    keys["w"]=false; keys["s"]=false; stopLatch=true; centering=true;
    send({event:reason||"space"});
  }
  document.getElementById("stopbtn").addEventListener("click",function(){ doStop("space"); });

  // note
  function sendNote(){
    var el=document.getElementById("notetext"); var t=el.value.trim();
    if(!t) return;
    fetch("/note",{method:"POST",headers:{"Content-Type":"application/json"},
                   body:JSON.stringify({text:t})}).then(function(){ el.value=""; });
  }
  document.getElementById("notebtn").addEventListener("click",sendNote);
  document.getElementById("notetext").addEventListener("keydown",function(e){
    if(e.key==="Enter") sendNote();
  });

  var sim=document.getElementById("simsonar");
  if(sim) sim.addEventListener("input",function(){ document.getElementById("simval").textContent=sim.value; });

  // loops: 50 ms physics, 100 ms send, 5 s status poll
  setInterval(physics,50);
  setInterval(function(){ send(); },100);
  setInterval(function(){
    fetch("/status").then(function(r){return r.json();}).then(paint).catch(function(){});
  },5000);
  // initial status
  fetch("/status").then(function(r){return r.json();}).then(paint).catch(function(){});
})();
</script>
</body>
</html>
"""


# ================================================================ MAIN
def main():
    global CTRL
    dry_run = "--dry-run" in sys.argv
    log_dir = os.path.join(_HERE, "logs")
    if "--log-dir" in sys.argv:
        i = sys.argv.index("--log-dir")
        if i + 1 < len(sys.argv):
            log_dir = sys.argv[i + 1]

    CTRL = Controller(dry_run=dry_run, log_dir=log_dir)

    ctrl_thread = threading.Thread(target=CTRL.run, daemon=True)
    ctrl_thread.start()

    # NO AUTHENTICATION: anyone who can reach this port can drive the robot. Default: this Pi only.
    # TELEOP_BIND=<the Pi's LAN IP> opens it to the home Wi-Fi (a phone); never on a network you don't control.
    BIND = os.environ.get("TELEOP_BIND") or "127.0.0.1"
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    httpd.daemon_threads = True   # request threads must NEVER keep the process alive

    # The HTTP server runs on a SEPARATE (daemon) thread; the MAIN thread only waits on a stop event.
    # WHY (SIGTERM hang seen in a live run): the Python signal handler ALWAYS runs on the
    # main thread -- if the main thread sits in serve_forever() and the handler calls
    # httpd.shutdown(), that is a DEADLOCK: shutdown() waits for the serve_forever loop
    # to exit, which is stuck in the handler. (kill -9 was needed and the session_end
    # log line was lost.) So the handler ONLY sets the event; all
    # cleanup happens on the main thread, after the wait.
    srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv_thread.start()

    stop_event = threading.Event()

    def _signal(signum=None, frame=None):
        print("\n[teleop] shutting down…", file=sys.stderr)
        stop_event.set()

    signal.signal(signal.SIGINT, _signal)
    try:
        signal.signal(signal.SIGTERM, _signal)
    except (ValueError, AttributeError):
        pass
    # On Windows SIGTERM is a force-kill (the handler does not run) -> Ctrl-Break (SIGBREAK)
    # is the closest clean signal; Linux has no SIGBREAK, so this is a no-op there. (On the Pi
    # SIGTERM/SIGINT is the normal path -- this is only for a clean exit in laptop smoke tests.)
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _signal)
        except (ValueError, AttributeError):
            pass

    mode = "DRY-RUN (no hardware)" if dry_run else "LIVE (Picarx)"
    print(f"[teleop] v{TELEOP_VERSION} {mode} — http://{BIND}:{PORT} (no auth — home network only)  log={CTRL.logname}", file=sys.stderr)
    if BIND in ("0.0.0.0", "::"):
        print("[teleop] WARNING: listening on EVERY interface without authentication", file=sys.stderr)
        _open = os.environ.get('PI_LAN_IP', '<robot-ip>')
    else:
        _open = BIND
    print(f"[teleop] open: http://{_open}:{PORT}   nav-source={_NAV_SRC}", file=sys.stderr)

    try:
        while not stop_event.is_set():
            stop_event.wait(0.5)   # short timeout: the handler flag takes effect here
    except KeyboardInterrupt:
        pass

    # --- orderly shutdown (guaranteed within a few seconds) ---
    CTRL.shutdown()                # control thread's finally: motor 0, steering 0, session_end
    ctrl_thread.join(timeout=3.0)
    try:
        httpd.shutdown()           # serve_forever runs on ANOTHER thread -> safe from here
    except Exception:
        pass
    srv_thread.join(timeout=2.0)
    try:
        httpd.server_close()
    except Exception:
        pass
    print("[teleop] exited.", file=sys.stderr)
    if ctrl_thread.is_alive() or srv_thread.is_alive():
        # emergency exit: if any thread is still stuck, do NOT wait forever
        # (motor stop + session_end have already happened or timed out by now)
        os._exit(0)


if __name__ == "__main__":
    main()
