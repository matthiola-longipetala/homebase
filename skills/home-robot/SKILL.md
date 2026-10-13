---
name: home-robot
description: ENTRY INDEX for the household's PiCar-X robot — the agent's physical body in the home. Load it whenever anything robot-related comes up: navigation, vision, hearing, speech, sensors, operations, bridges. This file gives the frame, the MANDATORY safety rules and the session-start protocol, and tells you WHICH capability skill to load next. Triggers: "robot", "PiCar", "home robot", "switch on the robot", "go / turn / navigate", "listen / speak", SSH to the Pi.
---

# Home robot — ENTRY INDEX

**Canonical knowledge: `picarx-mcp/` (laptop mirror of `{PICARX_HOME}` on the Pi) — this skill is navigation only.**
No facts live here: every calibration number, tag datum, room fact and route lives in the picarx-mcp files
or in your own (unshipped) map and route files.

## Frame (the WHY)
The robot is **the agent's physical embodiment**: a PiCar-X (Raspberry Pi 4 + Robot HAT V4) that navigates
by AprilTags + camera, hears (ReSpeaker XVF3800), speaks (ElevenLabs voice) and senses the direction of sound
(DOA). The brain is the resident agent itself — woken by events (Monitor), with memory — not a stateless API
call. The agent drives; nobody remote-controls it.
**Confidence:** the robot is ~16 cm wide, a door ~85 cm — the "too narrow" feeling is a camera illusion. Drive decisively.

## SAFETY (always valid, whichever skill is loaded)
- **Rooms with a threshold trap (e.g. a bathroom): do NOT drive in** — survey their tags from the doorway.
- **Outdoor areas (terrace, balcony): only after agreeing with the owner** (they open the door; edge risk).
- **Under/behind beds:** avoid. **Never turn on a carpet** (you get stuck).
- **Robot on a table / in a hand / on a ledge = NO wheel movement at all.** A human relocation legitimately makes the pose jump.
- **Living-being protocol:** a person or animal lying in the way → wait or go around, never push.
- **`--force` (sonar brake off):** only after an image-based decision, briefly; when in doubt ask the owner.
- **Private rooms:** only with an open door; never disturb sleep or work.
- **Secrets:** the TTS key is in the Pi's `voice_config.json` (from `voice_config.example.json`), the Telegram bot token in your telegram-mcp
  `config.json` — NEVER into chat or a log. A sudo password is transient, only with the owner's explicit
  permission, and never goes into memory.
- **OS system / privacy settings:** do not toggle them — ask the owner.
- **Noisy STT:** read it charitably, but do NOT extract a command from it if it could also be a warning — ask back.

## Session-start protocol (MANDATORY, no exceptions)
1. **Read this index completely** (frame + safety + which skill).
2. **Connect:** `ssh -i {HOME}/.ssh/<robot-key> -l <pi-user> <Pi-IP>` (mDNS is flaky → use the IP; from the chat
   PowerShell you need a null stdin, or use `karbantartas/pi_ssh.py` — see `robot-operations`).
3. **`preflight.py`** — battery, ear, camera, QWIIC chain, orphans. Microphone FAIL → `respeaker_reboot.py`.
   Battery: `/tmp/battery_status`; below WARN tell the owner. **After a boot the manual starters are needed**
   (ear, speaker daemon, MCP server) → `robot-operations`.
4. **Memory context:** memoria-mcp `get_context()` / `search_memory("robot")`.
5. **Read the Pi-side entry point too:** `picarx-mcp/OTTHON_ROBOT_SKILL.md` ("home robot skill") — the canonical
   "what to find where / where to write" INDEX table. This skill family does not replace it; it only puts the
   load decision in front of it.

## Capability skills — what to load when

| Capability | Skill | What it gives you | Canonical file(s) |
|---|---|---|---|
| Driving, self-localization, turning, routes | `robot-navigation` | Knowing where you stand (whereami), turning to a tag, replaying learned routes step by step, called out. Load it whenever a wheel will move. Read the target room's and the on-the-way rooms' cards first. | `tanulsagok/vezetes_alapok.md`, your own room cards and routes (not shipped; templates: `routes.example.json`, `navigacio/tags_template.json`) |
| Hearing, speech, sound direction, speaker ID | `robot-hearing-speech` | The listener turns each recognised sentence into an event, the speaker daemon says who spoke, you answer in your own voice. Long text is always chunked; the mic is reset before recording. | `technika/eszkoztar.md` ("Ear / voice", "Speaker identification", "Soundscape", the robot_say sections) |
| Camera, AprilTags, live stream, look-back | `robot-vision-camera` | Photos, tag reading, a live view, and looking back over the last minutes when something happened. Reading a note held up to the camera. | `tanulsagok/erzekeles.md`, `technika/eszkoztar.md` ("Camera calibration", "Live video stream") |
| Sensors, body sense, sentinel, battery | `robot-sensors-sentinel` | Light, heading, climate, floor distance, battery; the sentinel speaks up by itself when lifted, when it gets dark, when the battery drops. The signal is the sentinel's, the decision is yours. | `technika/eszkoztar.md` ("QWIIC sensor chain"), `tanulsagok/erzekeles.md` |
| SSH/scp, boot, Wi-Fi, backup, shutdown | `robot-operations` | Staying alive: SSH, manual starters after boot, Wi-Fi profiles and hotspot, password change with a device list, backup, gentle shutdown. Passwords are typed by the owner. | `technika/eszkoztar.md` (Hardware, WIFI, `leallit.py`, SD recipe) |
| Monitor, Telegram, MCP server, dead zone | `robot-bridges` | Events from the robot via Monitor, photos via Telegram, chat + voice combo in Wi-Fi dead zones, and what to do when the robot tools are disconnected. | `technika/eszkoztar.md` ("Telegram PHOTO sending", "Servo/camera access…"), `bridge.py` |

## Maintaining knowledge
Every fact has ONE canonical place. Room fact → that room's card; driving lesson → `tanulsagok/vezetes_alapok.md`;
perception lesson → `tanulsagok/erzekeles.md`; script/hardware → `technika/eszkoztar.md`; route experience →
the route's own JSON; story → memoria-mcp. After an update **scp it to the Pi**. At session end `log_session_summary`.
**Do NOT write facts into these skill files** — only command syntax, order, trap lists and "when needed".
