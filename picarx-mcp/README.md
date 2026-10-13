# picarx-mcp

Control a PiCar-X robot over the MCP protocol. The server runs on the Raspberry Pi and is reachable on the
LAN as an HTTP/SSE server; the **operator is Claude Code** (not Cowork/Desktop) — see below.

> **Identity frame:** the robot is *one of the agent's tools* for the physical world — not a separate
> character. Tool names are function-level, docstrings are written in the first person.

Working name: `picarx-mcp`.

## Where things stand

**This skeleton was phase 1** (only `status` + `move`), built ahead of the kit's arrival, ready to transfer.
Camera/distance came in phase 2, the events queue in phase 3, audio in phase 4, the reflex layer in phase 5,
docking in phase 6 and `discovery` in phase 7.

## Project structure

```
picarx-mcp/
├── server.py                 # FastMCP SSE server (host/port in the constructor!)
├── config.example.json       # → config.json on the Pi (gitignored)
├── voice_config.example.json # → voice_config.json (holds the TTS key; gitignored)
├── camera_config.example.json, routes.example.json, garble_hints.example.json
├── requirements.txt
├── tools/                    # MCP tools: status (the Picarx() singleton), move, look, scan
├── core/                     # shared library: capture, localisation, motion, event queue, no-go zones …
├── navigacio/                # "navigation": calibrate.py, AprilTag / checkerboard generators
├── utvonalak/                # "routes": route_runner.py + ROUTE_RUNNER.md (your routes are not shipped)
├── stream/                   # live video (go2rtc) + replay buffer (visszanez.py = "look back")
├── speaker_id/               # optional speaker identification (own venv, see its README)
├── perception/               # YOLO object detection, runs on the LAPTOP
├── teleop/                   # manual driving helpers
├── tanulsagok/, technika/    # "lessons" (driving, perception) and the tool handbook (eszkoztar.md)
├── *.py                      # standalone scripts: robot_say*.py, robot_listen_loop.py, preflight.py,
│                             #   leallit.py ("shut down"), battery_guard.py, orszem.py ("sentry"), whereami.py …
├── battery_guard.service, picarx-mcp.service   # systemd units (the server unit is a template only)
├── OTTHON_ROBOT_SKILL.md     # "home robot skill": the entry index for Claude
└── SETUP_FAZIS_0.md          # phase 0: headless Pi setup + assembly + smoke test
```

## Installing on the Pi (short version — details: `SETUP_FAZIS_0.md` ("setup, phase 0"))

```bash
# after phase 0 (SunFounder library + i2samp.sh are installed)
cd {PI_HOME}/picarx-mcp
cp config.example.json config.json     # the ElevenLabs fields may stay PLACEHOLDER in phase 1
python3 -m pip install -r requirements.txt
python3 server.py                       # start by hand (see below)
```

The MCP server is started BY HAND after every boot, on purpose: started by systemd during boot, the camera
initialisation could hang. `picarx-mcp.service` is shipped as a template only; enable it at your own risk.
`battery_guard.service` (low-battery shutdown) is the one unit you DO want enabled:
`sudo cp battery_guard.service /etc/systemd/system/ && sudo systemctl enable --now battery_guard.service`
(fill in `{PI_HOME}` and the user first). Also copy the templates once: `voice_config.example.json` →
`voice_config.json`, `camera_config.example.json` → `camera_config.json`, `routes.example.json` → `routes.json`,
`navigacio/camera_calib.json` → `camera_calib.json` (an example calibration — calibrate your own camera with
`navigacio/calibrate.py`), and `navigacio/tags_template.json` → `tags_coords.json` (your own tag positions).
`preflight.py` checks for all of these.

## Connecting — the operator is Claude Code (not Cowork/Desktop)

> **Decision:** the robot's home is Claude Code, because it has the capabilities needed to operate it —
> the **Monitor tool** for event activation, a large context, headless operation. Cowork has no Monitor.
> For the human user the interface is similar.

### A. MCP client (Claude Code)
Claude Code natively supports the remote (SSE/HTTP) MCP transport, so the `mcp-remote` proxy is most likely
**not** needed:

```
claude mcp add --transport sse picarx-mcp http://<robot-host>.local:8765/sse
```

`<robot-host>.local` (mDNS) or a fixed IP (DHCP reservation) — your choice; the laptop helpers read it from
`PI_HOST` / `config.json` (`pi_host`).

### B. Event activation (Monitor) — the key to physical presence
Two complementary channels:
- **in-band:** during active driving, the `events` field of every tool response (low latency).
- **out-of-band:** while the agent is idle, the Pi exposes an event endpoint (HTTP `GET /events?since=` poll
  or SSE) that a **Monitor script** polls; every actionable line **re-activates** the agent as a chat event —
  with a fresh tool frame, but in the SAME session (full context). This is the same pattern as the agent's
  chat-channel **SYNC daemon + WAKE watcher** setup.

Rule (learned from Telegram `getUpdates`): an exclusive/long-poll source should have ONE dedicated consumer.
The robot's event source is on the Pi side (VAD/CV → its own endpoint), so it does not collide with the action
tools.

## Important warnings

- **Picarx() singleton** — a single instance for the lifetime of the server (avoids GPIO conflicts).
- **Time-based movement** — there is no encoder; `move("forward", 50)` may be 45–55 cm. Calibrate it in the
  first days (tape measure) → `config.json` `movement_calibration`.
- **config.json may contain secrets** (the ElevenLabs key from phase 4) — gitignored.
- **The code runs on the Pi** — it does not start on Windows (no `picarx`/`robot-hat`); a laptop copy of this
  folder is only a staging area for transfer.

## Local settings (nothing personal is hard-coded)

- Code paths on the robot are relative to the robot user's home: the repository is expected at `~/picarx-mcp`
  (`{PI_HOME}` in the docs and in the systemd templates = that user's home directory).
- Laptop-side helpers (`robot_mond.py` ("robot says"), `robot_olvaso.py` ("robot reader"), `szemlelo_laptop.py` ("observer, laptop side")) read their connection settings via
  `laptop_config.py`: environment variables `PI_HOST`, `PI_USER`, `PI_KEY`, or the `pi_host`, `pi_user`,
  `ssh_key` keys in `config.json` (see `config.example.json`). Missing values stop the script with a clear message.
- `PI_LAN_IP` (or `pi_host`) is used only to print the live-stream / teleop viewer URLs; go2rtc reads `PI_LAN_IP`
  for its WebRTC candidate.
- `ROBOT_SPEAKERS` (comma-separated) lists the enrolled speaker names that `robot_olvaso.py` treats as speech labels;
  `ROBOT_OWNER_NAME` (optional) is used in the spoken greeting of `jonapot.py` ("good day").
- `bridge.py` needs `PICARX_BRIDGE_TOKEN` in its environment and refuses to start without it.
- **Voice and language:** copy `voice_config.example.json` to `~/picarx-mcp/voice_config.json` ON THE ROBOT (it holds the
  ElevenLabs key; it never goes into the repo or the chat). The speech language is `tts.language_code` /
  `stt.language_code` there (default `en`; e.g. `hu`), and `ROBOT_ESPEAK_VOICE` sets the offline espeak fallback voice
  (default `en`). The sound-label display names (`hangkep_labels.py`) are an optional Hungarian table: the events show
  that name, with the English YAMNet label alongside in the JSON.
