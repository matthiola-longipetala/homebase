# Home robot (PiCar-X) — ENTRY POINT + INDEX

(`OTTHON_ROBOT_SKILL` = "home robot skill".)

> This file is deliberately SHORT: frame + safety + index (reorganised at the owner's suggestion).
> The knowledge lives in topic files; from here you know WHAT to look for WHERE and WHERE to write.

## Frame (the WHY)
The robot is **the agent's physical embodiment** — the agent navigates, nobody remote-controls it for it. **Big vision:** a dock + autonomous operation while the household is away -> reliable navigation + getting home before the battery runs out. **Confidence:** the robot is ~16 cm wide, a typical door ~85 cm — the "too narrow" feeling is a camera illusion. Drive decisively, not hesitantly.

## ⚠️ SAFETY (always valid, regardless of which files are loaded)
- **Rooms with a step/threshold trap (e.g. a bathroom): do NOT drive in.** **Outdoor areas (terrace/balcony): only after agreeing with the owner** (they open the door; outside there is an EDGE risk: the sonar cannot see downwards, keep away from the edge). **Under/behind beds: avoid** (not forbidden, but hard to navigate; blankets/frames trap the sonar — only deliberately and slowly).
- **Robot on a table or in someone's hand = NO wheel movement at all.** If a human relocates it, the pose jumps legitimately.
- **CARPET:** only with momentum, straight; never turn on it (you get stuck — the sonar delta reveals it).
- **Pet protocol:** for a living being lying in the way, wait or go around, never push; otherwise the sonar brake (22 cm) protects you — do not freeze at every movement.
- **--force (brake off):** only after an image-based decision (no REAL obstacle in the way), briefly; when in doubt, ask the owner.
- **Secrets:** the ElevenLabs key lives in the Pi's voice_config.json — NEVER into the chat; a sudo password is transient and only with the owner's permission.
- **Private rooms:** only with an open door; never disturb sleep or work.
- **Noisy STT:** interpret it charitably, but do NOT read a command out of it if it could also be a warning — ask back.

## Session-start protocol

**ALWAYS MANDATORY (in every robot session, without exception):**
1. **Read THIS file COMPLETELY** (safety + index).
2. `preflight.py` (battery, ear, camera, orphans) — if the microphone FAILs: `respeaker_reboot.py` (needed after every Pi boot).
3. Memory context from the agent's own memory system.

**MANDATORY BEFORE DRIVING (before any wheel movement):**
4. The room cards of the TARGET room and of the rooms on the way (your own private map files).
5. `tanulsagok/vezetes_alapok.md` (`tanulsagok/` = "lessons", "driving basics") — in full.
6. The FULL file of the route concerned — your own recorded routes (not shipped; e.g. `utvonalak/routes/<route>.json` with a `routes.json` index, template: `routes.example.json`) — with the FULL content of the waypoints (act/note/scene/annot), never truncated.
7. **★★ WHILE DRIVING — BEFORE EVERY WAYPOINT: RE-READ the next step's act/note AND WRITE IT INTO THE CHAT (where am I -> next act -> success criterion). THE WRITTEN STEP IS THE STEP THAT GETS FOLLOWED** — writing it out forces re-reading the route text; without it you slide into goal-driven improvisation (lesson learned: with the step written out a test run scored 14/14, without it the robot left the route; saying it aloud is optional). Details: `tanulsagok/vezetes_alapok.md`.
8. **★★ STRAIGHT DRIVING = ALWAYS WITH HEADING HOLD (go_heading) — ON THE "RAILS" TOO!** Whenever you do not turn but drive straight, always use heading hold; plain go_dist for long straights is FORBIDDEN. **AND THE NO-CHAINING RULE: physical manoeuvres in SEPARATE calls, output read each time — chaining only causes trouble in navigation.** The three basics together: WRITTEN STEP + HEADING HOLD + NO CHAINING.

**Per task (from the INDEX):** `tanulsagok/erzekeles.md` ("sensing"; lots of image work / tag-blind zones), `technika/eszkoztar.md` ("toolbox"; script details, ear/voice), your private tag map (tag survey, map work).

**Listener** (for live conversation): Monitor + `robot_listen_loop.py` (recipe: `technika/eszkoztar.md`). While running, the listener also produces a **YAMNet soundscape**: labels of non-speech sounds in `/tmp/hangkep.json` (`hangkep` = "soundscape") ("what do I hear now?") + an autonomous `[hang: …]` event for a new sound (on/off: `/tmp/hangkep_off`; details: `technika/eszkoztar.md`).

**Live video stream** (tied to active mode, off by default): `look`/`scan` get an instant frame from the stream, the **rolling buffer** (`stream/visszanez.py N`, "look back") lets you look back (what happened BEFORE an event), the adaptive `capture.py` stays available on demand as a "special eye". Manual: `stream/stream_ctl.py start|status|on|off|adaptive`. Viewer window on the LAN: `http://<robot-ip>:1984/stream.html?src=robot`. Details: `technika/eszkoztar.md` "Live video stream (go2rtc)".

## 📚 INDEX — what to read where / where to write

| Topic | READ | WRITE NEW KNOWLEDGE HERE |
|---|---|---|
| Room orientation, walls/doors/furniture, room traps | your private room cards | on the room's OWN card |
| Tag list in text | your private tag map | there + `tags_coords.json` (inner "tags" object!) |
| Tag coordinates (numeric truth) | `tags_coords.json` (template: `navigacio/tags_template.json`) | `bemer_tag.py` ("survey tag") / by hand into "tags" |
| Driving rules (turning, steering, thresholds, leaving the route, door checklist) | `tanulsagok/vezetes_alapok.md` | same place |
| Camera/sonar illusions, scanning methods | `tanulsagok/erzekeles.md` | same place |
| Scripts, hardware, SSH/scp, calibrations, ear/voice/DOA | `technika/eszkoztar.md` | same place |
| Live video stream (go2rtc, ring buffer / look-back, look/scan redirection, on-demand adaptive eye) | `technika/eszkoztar.md` "Live video stream (go2rtc)" section | same place |
| Learned routes + per-route lessons | your own routes, not shipped (e.g. `utvonalak/routes/<route>.json`, index+nodes: `routes.json`; template: `routes.example.json`) | into the route's OWN file status; new route = new file + index line |
| Route runner logs | `utvonalak/naplok/` ("logs") (created at runtime, private) | same place |
| Session stories, experiences | the agent's memory system | there |

**Writing rule:** every fact has ONE canonical place — elsewhere write only a pointer. Room fact -> room card; driving lesson -> tanulsagok/; route experience -> the route file (utvonalak/routes/); story -> memory. After an update **scp it to the Pi** (`</dev/null` from the chat!).

## Communication
- **We drive by VOICE** (the human may not see the screen — SAY everything important, `robot_say.py --file`, UTF-8).
- Take what the human says literally; when they are done, they will say so — do not offer exits.
- Route replay = following the recorded steps LITERALLY; the instructions are help, not a cage (they carry the human's local knowledge).

## Quick tool reminder
`whereami` (where am I) · `face_tag <id>` (turn towards a tag — exit 0 on STOP too, run it SEPARATELY!) · `turn_scout` (blind search) · `go_dist <cm> [spd] [--force]` · **`smart_turn left|right [deg]` (★ closed-loop turning with the IMU — PRIMARY instead of turn90 on clear ground)** · **`go_heading <cm> [spd]` (★ straight driving with heading lock — against left drift)** · `turn90`/`fine_turn` (blind fallback) · **`erzet` ("feeling"; well-being: light+climate+heading+battery)** · `pilot` (manual arc+image) · `zero_steer` (from the left!) · `look3` · `rear_sonar` · `robot_say --file` / `robot_hear` · `preflight` (with QWIIC chain check). Details: `technika/eszkoztar.md` (also the "QWIIC sensor chain" section).
