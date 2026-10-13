---
name: robot-bridges
description: The passages between the robot and the chat — Monitor (event-driven wake-up), the picarx-mcp server, the HTTP command bridge, Telegram photo sending, and the chat + voice combo for Wi-Fi dead zones. Load it when you want events from the robot, want to send a photo or message to the owner on the robot's behalf, or your MCP tools are "disconnected". Triggers: "Monitor", "watcher", "bridge", "send a picture", "send a photo on Telegram", "robot tools not working", "MCP disconnected".
---

# Robot — BRIDGES (chat ⇄ robot)

**Canonical knowledge: `picarx-mcp/technika/eszkoztar.md` ("Telegram PHOTO sending", "Servo/camera access while
the MCP server is running"), the header of `picarx-mcp/bridge.py`, `picarx-mcp/README.md` (MCP registration),
`telegram-mcp/README.md`. This skill is navigation only.**

## The four bridges — which for what
| Bridge | Good for | When |
|---|---|---|
| **Monitor** | event-driven WAKE-UP: the Pi's stdout arrives in the chat as events | live listening, sentinel watching, "tell me when…" |
| **picarx-mcp server** | native tools (`move`, `say`, `listen`, `goto`, `sweep`) | normal robot work |
| **`bridge.py` (HTTP) / `karbantartas/pi_ssh.py`** | commands when `ssh.exe` does not work | chat-side PowerShell where `ssh.exe` dies silently |
| **Telegram** | message and **PHOTO** to the owner | reports, event photos, alerts |

## 1. Monitor — the robot "wakes you up"
A Monitor is a long-running command whose **stdout arrives in the chat as events**. Two proven patterns:

**Listener** (each recognised sentence is one event):
```
ssh -i {HOME}/.ssh/<robot-key> -o ServerAliveInterval=15 -l <pi-user> <Pi-IP> \
  "cd ~/picarx-mcp && PYTHONUTF8=1 python3 -u robot_listen_loop.py 2>/tmp/listen.err"
```
**Sentinel log** (body-sense events):
```
ssh -i {HOME}/.ssh/<robot-key> -o ServerAliveInterval=15 -l <pi-user> <Pi-IP> \
  "tail -n 0 -F /tmp/orszem_events.log"
```
Rules:
- **`-u` (unbuffered) and `PYTHONUTF8=1`** are required, otherwise nothing comes out / accents break.
- Send stderr **to a file** (`2>/tmp/…err`) so only the signal arrives as events.
- **Stop BY HAND** in a separate ssh call (`kill -TERM $(pgrep -f '[r]obot_listen_loop')`) — neither TaskStop
  nor `ssh -tt` reliably carries SIGHUP.
- **Never put the stopping `pkill` inside the Monitor command** — it kills itself, exit 255.
- **A Monitor is bound to the session.** When the session ends or the app restarts, the watcher dies — the robot
  keeps running. After a restart, **start your watchers again.**

## 2. picarx-mcp server
- **Started BY HAND** (`nohup python3 ~/picarx-mcp/server.py > /tmp/server.log 2>&1 &`) — `picarx-mcp.service` is
  shipped only as a template, because camera init during boot could hang.
- **While the server runs, `Picarx()` cannot be instantiated from another process** (`GPIO busy`; the server holds
  the MCU reset pin). Workarounds:
  - servos: `robot_hat.Servo` directly (pan = P0, tilt = P1, steering = P2);
  - images: standalone `core.capture.capture()` (handles the stream lease), or plain `rpicam-still`.
- **If your tools are "disconnected"**, or `look` throws `Errno -25` with a healthy camera: restart the server +
  start a **NEW Claude session** (the MCP connection is built at session start). Also check the MCP host
  setting (IP vs mDNS name) in your client config.
- **A new native tool appears only after a client restart** — server-side code alone is not enough.

## 3. bridge.py — HTTP command bridge / pi_ssh.py
A token-protected HTTP bridge on the Pi, **port 8731**, token from the `PICARX_BRIDGE_TOKEN` environment variable.
Needed when `ssh.exe` dies silently from the chat-side PowerShell (exit 255 without valid stdin): send the
command with `Invoke-RestMethod`. The bridge must be started first, so prefer `karbantartas/pi_ssh.py`
(paramiko, `run|put|get|ping`, reads `PI_HOST`, `PI_USER`, `PI_KEY`). If plain ssh works, **stay with ssh**.

## 4. Telegram — message and photo
- **Text:** telegram-mcp `telegram_send` / `telegram_send_and_wait`.
- **Photo:** telegram-mcp `telegram_send_photo` (local file); the raw Bot API `sendPhoto` fallback is in eszkoztar
  "Telegram PHOTO sending" (token and chat id from your own telegram-mcp `config.json`, template:
  `telegram-mcp/config.example.json`).
- **Sending does not collide with the sync daemon's getUpdates** — the 409 trap applies to RECEIVING only.
  Always read from the buffer: `telegram_get_messages(sync=False)`.
- **Never write the bot token into chat or a log.**

## Dead-zone chat + voice combo
Where Wi-Fi is weak (garden, far corner) and the text channel stutters: **the chat carries the command, the robot
acknowledges by VOICE** (`robot_say.py --file`). The human usually is not watching the screen anyway. If the
connection drops completely, switch the robot to the 2.4 GHz profile — see `robot-operations`.

## General traps
- **One consumer per channel.** One process (the sync daemon) holds Telegram getUpdates; everything else reads the
  buffer. Same logic for the speaker (`robot_say` in sequence) and the camera (stream vs. rpicam).
- **The exit code is not always a witness:** `scp` EXIT=0 = complete transfer, but `robot_say`'s "OK" is NOT proof of
  playback, and `face_tag` returns 0 on STOP too. Where there is a trap, read the OUTPUT.
- **Whatever is session-bound dies on restart** (Monitors, in-session crons). After a restart, first: what must be restarted?

## Where to write new knowledge
Bridge technique, API call → `technika/eszkoztar.md` · MCP diagnosis → your auto-memory ·
incident story → the memoria-mcp journal. Then **scp it to the Pi**.
