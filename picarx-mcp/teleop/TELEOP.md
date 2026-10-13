# TELEOP — web teleop controller (imitation learning demo)

A human drives the robot (the agent's physical body) from a laptop browser; **every command is logged**.
The agent later analyses the log + the stream's rolling buffer -> from the human's driving patterns, explicit
rule/parameter/route corrections are distilled **into text/code** (the teaching pipeline always ends in an explicit
artefact).

A single, stdlib-only file (`teleop_server.py`) with the HTML/JS/CSS embedded → **only one file needs to be scp'd**.

## Starting on the Pi
```bash
cd {PI_HOME}/picarx-mcp/teleop && python3 teleop_server.py
```
- **Driver URL:** http://<robot-ip>:8070
- **Windscreen (what the driver watches while driving):** the go2rtc stream window —
  http://<robot-ip>:1984/stream.html?src=robot . Put the teleop page and the stream window **side
  by side**: the camera pan/tilt turns exactly what the driver sees in the stream window.
- It runs on port **8070** (go2rtc is on 1984 — it leaves that alone).
- The server creates the log folder (`teleop/logs/`) itself.
- Can be tried on a laptop without hardware: `python3 teleop_server.py --dry-run` (a slider on the page
  simulates the sonar → the BLOCKED/OVERRIDE states can be tried too). The log folder can be overridden:
  `--log-dir <path>`.

## Keys
| Key | Function |
|---|---|
| **W** | forward |
| **S** | reverse (max speed 45) |
| **A / D** | steer left / right — **gradual** (grows while held, max ±25°); on release it **STAYS THERE** (sticky steering) |
| **X** | steering back to centre (with a small ramp, not a jump) |
| releasing throttle (W/S) | stop — **steering and camera stay where they are** |
| **Space** | immediate STOP + **steering to centre** (latch: blocks W until it is pressed again) |
| **1 / 2 / 3** | speed gear 30 / 45 / 60 (default 40) |
| **← / →** | camera pan left / right (gradual, ~±90°) — **stays there** |
| **↑ / ↓** | camera tilt up / down (~−35..+65°) — **stays there** |
| **R** | camera back to centre (pan 0, tilt 0) |

There is also a big red **STOP** button on the page. The client sends its state at ~10 Hz; key release,
STOP and window focus loss send an immediate stop message.

The page UI is in English: `STOP (Space)`, `Send` (note to the log); the dead-man stop and the push-through (OVERRIDE) mode show up in the status line.

## Steering servo — why servo writes are "decisive" (v1.2, lessons from live testing)
Two hardware lessons came out of live testing; the **server** handles them (the UI and the
keys did not change; the steering display still shows the *desired* angle):
- **A) STICKING on small steps.** The client nudges the desired angle in 3° increments, but against linkage
  friction the small nudge **does not get through** — the servo lags behind or sticks completely (live: the
  software showed +25° while the wheel was physically pointing left). **Large, single jumps**, however, are
  flawless. So the server writes to the servo **only decisively**: when (a) the difference between the target and
  the last written angle is **≥ 8°** (`STEER_WRITE_STEP`), **or** (b) the desired angle has been unchanged for **~150 ms**
  (`STEER_SETTLE_S`) (the driver "stopped" on an angle) → then the **exact** target in a single
  jump. So while driving it moves in large steps (no sticking), and on release it is exact.
- **B) ZEROING ONLY FROM THE LEFT.** Coming **from the right** (from a positive angle) the servo **does not reach** the physical
  centre. So whenever it goes to 0 (end of X-centre, SPACE emergency stop, exit, initial zeroing): if the
  servo is on the **positive** side, first **−12°** (`STEER_LEFT_ZERO`), then after **~0.3 s** (`STEER_ZERO_GAP_S`)
  **0** (two decisive steps); coming from the left (negative), straight to 0. The −12→0 steps are **exempt**
  from the 8° threshold (they are always written). Every servo write is logged (`steer_write`).

## Safety behaviour
- **DEAD-MAN SWITCH (watchdog):** if the server receives no command for ~**500 ms** (frozen tab,
  network dropped) → **immediate motor stop**. On the client side `keyup` → immediate stop; window blur
  (focus loss) → stop. The dead-man stop **only stops the motor** — it does NOT touch the steering or the
  camera angle (they stay where the driver put them).
- **SONAR BRAKE (forward):** obstacle < **22 cm** → motor stop, state **BLOCKED** (shown in red on the page).
- **OVERRIDE / push-through (threshold crossing):** if AFTER a block the driver holds forward continuously for **≥1.0 s**
  -> **momentum burst** (speed ≥55, max ~**1.5 s**), ignoring the sonar meanwhile. Afterwards
  back to normal guarded mode (if the obstacle is still < 22 cm, it blocks again). Separate log events:
  `sonar_block` and `override_burst` (start+end).
- **Reversing:** no front sonar brake (the rear sonar is a separate script, not integrated); the
  reverse speed max is **45**.
- On shutdown (SIGINT/SIGTERM/exception): motor stop, steering 0 (**from the left** if it was on the positive side —
  see lesson B; **camera stays**), `session_end`, clean exit.

## Log format (for analysis by the agent)
JSONL: `teleop/logs/teleop_<YYYYMMDD_HHMMSS>.jsonl` — **the file name is shown at the top of the page**.
Every line is a JSON object with a `ts` (unix-epoch float) and a `type` field:

| `type` | When | Key fields |
|---|---|---|
| `session_start` | at startup | `version`, `dry_run`, `params` (watchdog/sonar/override/speed thresholds, gears, nav source, **steering thresholds**: `steer_write_step`, `steer_settle_s`, `steer_left_zero`, `steer_zero_gap_s`), `logfile` |
| `cmd` | a **change** in the driver's intent (not the 10 Hz repetition!) | `steer` (−25..25), `speed`, `dir` (`fwd`/`back`/`stop`), `cam_pan`, `cam_tilt` |
| `state` | **5 Hz**, while the robot is **moving** | `steer`, `speed`, `dir`, `sonar_cm`, `cam_pan`, `cam_tilt` |
| `steer_write` | **actual** servo write (quantised) | `angle` (the written angle), `from` (previous written angle or `null`), `why` (`step` ≥8° / `settle` / `zero_left` −12 / `zero` 0 / `init`) |
| `sonar_block` | sonar brake engages | `sonar_cm`, `speed`, `steer` |
| `override_burst` | push-through | `phase` (`start`/`end`), `speed`, `sonar_cm` (start), `reason`+`dur` (end) |
| `stop` | motor stops | `reason` (`keyup`/`space`/`blur`/`watchdog`/`release`) |
| `note` | from the page's note field (Enter/Send) | `text` |
| `session_end` | on shutdown | `version` |

**Analysis key:** `cam_pan`/`cam_tilt` show **where the driver looks while driving** (independent of the steering
intent); the `state` lines give the continuous driving profile (turn arc, sonar proximity).

## Stream integration
- The server refreshes `/tmp/robot_active_at` every ~10 s → the **stream STAYS ON** (active mode).
- **Teleop deliberately does NOT use NAV mode** (found in live testing): to the supervisor the NAV flag means
  "pause the stream", but in teleop the stream is **the driver's windscreen** —
  it has to keep running during motion. On shutdown the server cleans up with `nav_off()` (in case a flag from an earlier
  nav session got stuck).

## Battery
Every 5 s the page shows the contents of `/tmp/battery_status` ("V PCT STATE") — the same thing that
`preflight.py`/`battery_guard` writes.

## Shutdown
- **Ctrl-C** (SIGINT) in the terminal → clean exit (motor stop, steering 0, `session_end`).
- Or `kill -TERM <pid>` (SIGTERM is handled the same way).
- For a smoke test on the laptop (Windows), **Ctrl-Break** (SIGBREAK) also gives a clean exit — on Windows
  SIGTERM is a force-kill and the handler would not run. (Irrelevant on the Pi: there SIGTERM/SIGINT is the normal path.)

## Watch out on a live (Pi) start
- `preflight.py` / `respeaker` are not needed, but a **fairly full battery** is recommended (the burst runs at 55).
- The sudo warning from the `Picarx()` init is non-fatal. The init moves the steering (10 → −12 → **0**,
  i.e. it approaches zero **from the left** — see lesson B); the steering zero is **calibrated** on the Pi, the server
  only calls `set_dir_servo_angle` (it does not calibrate).
- The stream (the windscreen) keeps running during motion — teleop does not switch to NAV mode (see above).
