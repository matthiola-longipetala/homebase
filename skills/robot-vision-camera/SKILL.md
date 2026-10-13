---
name: robot-vision-camera
description: The PiCar-X robot's eye — camera commands, AprilTag detection, photos, live video stream (go2rtc) and looking back through the rolling buffer, reading text or a note from an image. Load it when something must be LOOKED AT, a picture taken, or you must look back at what happened before an event. Triggers: "look at", "what do you see", "take a photo", "camera", "stream", "look back", "what just happened", "read this" (from paper).
---

# Robot — VISION AND CAMERA

**Canonical knowledge: `picarx-mcp/tanulsagok/erzekeles.md` (camera illusions, distance distortion, night sky)
and `technika/eszkoztar.md` ("Camera calibration + exposure", "Live video stream (go2rtc)").
This skill is navigation only.** Intrinsics, calibration numbers, FOV and tag-distance formulas belong THERE.

## Basics
- The camera is an **ACTIVE sensor**: ±70° pan × wide FOV → look around by panning, not by turning the robot.
  Scanning methods and step sizes: `tanulsagok/erzekeles.md` "Methods".
- **The canonical camera config is your `camera_config.json`** (template: `camera_config.example.json`; resolution +
  LOCKED focus). **Autofocus during navigation is FORBIDDEN** (focus drift → silent distance error). If the focus
  changes, recalibrate (`navigacio/calibrate.py`).
- **Image distance distortion depends on distance** — far away, tag distance + sonar are the truth, not the eye.
  A sonar/image contradiction is always GEOMETRY, not a sensor fault (see `robot-navigation`).

## Commands
| Command | For |
|---|---|
| `python3 look3.py <prefix>` | 3 reference images (pan −30 / 0 / +30) — quick overview |
| `python3 pilot.py 0 0 0 [camtilt]` | fresh image without moving (`/tmp/view.jpg`) |
| `python3 egbolt.py <tilt −30..60> <out.jpg> [pan]` | night-sky image ("sky"; robot must stand still) |
| `python3 lampa.py on\|off\|villanas\|proba` | LED flash/lamp ("lamp"; `villanas` = flash, `proba` = test) |
| `python3 stream/stream_ctl.py start\|stop\|status\|on\|off\|auto\|url` | live stream control |
| `python3 stream/stream_ctl.py nav on\|off` | NAV MODE (see below) |
| `python3 stream/stream_ctl.py frame [path]` | latest stream frame |
| `python3 stream/visszanez.py <s>` | rolling buffer → `/tmp/visszanez.mp4` ("look back") |
| `python3 stream/visszanez.py <s> --frames <n>` | n evenly spaced JPGs (`/tmp/vn_XXX.jpg`) |

**Tag work** (detection, survey, localization): `robot-navigation` + your own tag map (not shipped).

## Live stream (go2rtc) — when and why
- **Off by default**; tied to active mode (releases the camera when idle).
- What it gives and on-demand `look` NEVER can: **recent-past look-back** (~5 min rolling buffer) — seeing what
  happened BEFORE an event. Plus warm-up-free frames and a live viewer window (`stream_ctl.py url`).
- **NAV MODE:** during navigation the stream and tag photos would "pump" (slower fixes). So **`nav on` at the
  start of a nav burst, `nav off` at the end** — the stream pauses throughout.
- **Standalone tag/nav scripts do not break** (self-expiring camera lease). **Exception: diagnostics that call
  `rpicam-still` directly** are NOT auto-protected: run `stream_ctl.py off` first.
- Buffer segments are overwritten in a ring → **chronology comes from `mtime`, not the file name.**

## SOUND → PAST → MEANING chain (validated recipe)
To find out WHAT caused a sound you heard:
1. **The stream must be RUNNING** (`stream_ctl.py on`) — otherwise there is no past.
2. A sound event arrives (`[hang: …]` or an STT line) → **note the TIME**.
3. `python3 stream/visszanez.py 90` — a WIDE window: reaction time is 20-40 s, a narrow one misses it.
4. scp to the laptop → video-mcp `understand_video(source=<mp4>, prompt=…)`.

**Do not hunt fast movements with sparse sampling** — use motion search: `visszanez.py`, then ffmpeg
`select='gt(scene,…)'` + `showinfo` for the motion timestamps, then targeted frame extraction
(exact command: eszkoztar "Live video stream").

## Reading text / a note
Robot standing still, good light (`lampa.py villanas` if needed), **body turned to face the paper**, then
`pilot.py 0 0 0 <tilt>` or `look3.py` — and READ the image. Blurry? Focus is LOCKED, so move the robot/paper,
not the focus. For detail, a direct capture beats a stream frame.

## Perception (object recognition)
YOLO-based recognition **runs on the LAPTOP**, not the Pi: `picarx-mcp/perception/README.md`.
**Face recognition is opt-in per person** — build nothing for anyone who has not said yes.

## Where photos go
Your own photo folders **on the laptop only**, not on the Pi (not shipped; e.g. memories / events / nav debug
images). To send an image on Telegram: `robot-bridges`.

## Traps
- **`pilot.py`'s camtilt applies only to its own photo and RESETS the tilt afterwards.** For a lasting tilt:
  `Picarx().set_cam_tilt_angle(N)` + a stream frame — or `robot_hat.Servo('P1')` while the MCP server runs (`robot-bridges`).
- **Camera ribbon (CSI) gotcha:** "Camera frontend has timed out" while `--list-cameras` sees it → the ribbon sits
  crooked. Reseat on the camera side + REBOOT; before that `pkill -9 rpicam` (a zombie may hold it).
- **MCP `look` `Errno -25`** with a healthy camera (`rpicam-still` works) → suspect the server's camera layer:
  server restart + new Claude session (`robot-bridges`).
- **The downward laser sees through glass** — do not trust the cliff guard there (`robot-sensors-sentinel`).
- **The camera sits low** → lens tricks regularly fool you; the "too narrow" feeling is an illusion.
  Illusion catalogue: `tanulsagok/erzekeles.md`.

## Where to write new knowledge
Camera/sonar illusion, scanning method, night-sky photo → `tanulsagok/erzekeles.md` ·
calibration, stream technique → `technika/eszkoztar.md`. Then **scp it to the Pi**.
