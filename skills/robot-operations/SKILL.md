---
name: robot-operations
description: Keeping the PiCar-X robot running — SSH/scp to the Pi, manual starters after boot, Wi-Fi profiles and hotspot mode, Wi-Fi password change with the DEVICE-LIST rule, backup, SD card, and the GENTLE shutdown. Load it when the robot must be switched on/off, cannot be reached, changes network, needs a backup, or a process must be started/stopped on it. Triggers: "switch off the robot", "put it to sleep", "can't reach the robot", "wifi", "hotspot", "backup", "SD", "boot", "ssh", "scp".
---

# Robot — OPERATIONS

**Canonical knowledge: `picarx-mcp/technika/eszkoztar.md` ("Hardware + access", "WIFI", "Gentle shutdown",
"SD-card rebuild recipe"), `INSTALL.md` (robot section), and your own network notes in auto-memory (not shipped).
This skill is navigation only.**
Wi-Fi profiles, passwords and the network recipe live in YOUR auto-memory — **do not mirror them here and do not
ask for access**; the owner types passwords.

## Connecting
```
ssh -i {HOME}/.ssh/<robot-key> -l <pi-user> <Pi-IP>
```
- mDNS is flaky → **use the IP** (better: a DHCP reservation). In Bash use forward slashes in paths.
- **Chat-side PowerShell:** `ssh`/`scp` without a valid stdin **exit 255 silently** → `cmd /c "scp … < NUL"`;
  from Bash `</dev/null` works. Or use `karbantartas/pi_ssh.py run|put|get|ping` (paramiko, no `ssh.exe`).
  **Verify by exit code** (scp `EXIT=0` = complete transfer).
- **An embedded `sudo` needs `ssh -t`** (without a TTY there is no password prompt and it dies quietly).
- Laptop mirror: `{REPO}/picarx-mcp/` ⇄ `{PICARX_HOME}` on the Pi. The private key stays on the laptop; never copy it.

## After boot — MANUAL starters (this is the boot protocol)
1. `python3 respeaker_reboot.py` — **after every boot** (XMOS wedge).
2. `nohup ~/picarx-mcp/speaker_id/.spk-venv/bin/python ~/picarx-mcp/speaker_id/speaker_daemon.py > /tmp/speaker_daemon.log 2>&1 &`
3. `nohup python3 ~/picarx-mcp/server.py > /tmp/server.log 2>&1 &` (the MCP server — `picarx-mcp.service` is a
   template only, because camera init during boot could hang; see `robot-bridges`).
4. Listener under Monitor, if needed (`robot-hearing-speech`).
5. `python3 preflight.py` — check.

**Runs by itself (systemd):** `battery_guard.service` and your sentinel unit (`robot-sensors-sentinel`). Leave them alone.

## Wi-Fi
- **Profiles:** an indoor 5 GHz profile (higher priority) and a garden 2.4 GHz profile, power-save **OFF** on both.
  **Garden switch: `nmcli con up <garden-profile>`** — that is all.
- **Hotspot mode (travel):** a `travel-hotspot` NM profile with higher autoconnect priority, so without home Wi-Fi it
  connects by itself. Recipe: eszkoztar "WIFI" → "HOTSPOT MODE". Lifeline: **hotspot OFF = the robot switches back home.**
  After a new IP, update the MCP config too + restart the app.
- **Garden dead zone:** where the link is unstable, use the chat + voice combo (`robot-bridges`).
- **Wi-Fi PASSWORD CHANGE — DEVICE-LIST RULE.** BEFORE the change, list everything on the network (robot, phones,
  TV, solar inverter, other IoT) — otherwise something drops off silently and nobody notices for weeks
  (learned the hard way: a monitoring device stayed offline for days).
- **Robot not coming onto Wi-Fi:** suspect the LOCATION first (5 GHz dead zone, not in its usual place), not the
  battery or the SD. The LED is not a witness; the guard log is.

## Backup
- **From the running robot, with ssh + tar.** Exclude the user cache and local-data dot-folders, `__pycache__`,
  and **`*venv*`** (a starred pattern — `venv` alone does NOT match `.spk-venv`).
- **Two copies, two places:** the FULL backup (with secrets) stays on the laptop in a private folder; only a
  key-stripped copy may go to cloud storage. Strip: `voice_config.json` (+ backups), `etc/NetworkManager`, `.ssh`,
  the venvs. **Run a secret scan** on the finished archive.

## SD card
- **Push the microSD in UNTIL IT STOPS** — vibration can work it outward over weeks; on odd boot behaviour this is
  the FIRST check.
- "Works in the laptop, not in the Pi" = a **dying card**. Keep a spare card at home.
- Full rebuild after card death (treasure list + install order): eszkoztar "SD-card rebuild recipe". On the laptop
  the mirror's ext4 partition is read with the `python-ext4` package (7-Zip breaks on it). Raw sector-aligned
  reading on Windows needs an elevated helper of your own (not shipped).

## GENTLE SHUTDOWN — touch the switch only AFTERWARDS
```
python3 leallit.py          # with a spoken goodbye ("shut down")
python3 leallit.py --nema   # silently ("mute")
```
What it does: lamp off → goodbye → gently stops loops/daemons (listener, sentinel, speaker daemon, MCP server,
go2rtc) → double `sync` → `shutdown -h now`.
**The switch may be pressed when the green ACT LED has gone dark FOR GOOD** (~10 farewell blinks, then silence).
**Cutting power with the switch while writing is FORBIDDEN** — a dead card's post-mortem showed dozens of fsck
scars, each one a hard power cut.
Remote reboot needs no interactive login once the owner has added the one-time sudoers rule (see eszkoztar): `ssh … "sudo -n shutdown -r now"`.

## Traps
- **pgrep self-match:** `pkill -f <pattern>` kills your own shell if the pattern is in the running command (silent 255).
  Kill **in a separate ssh call**, with a bracket pattern: `pgrep -f '[r]obot_listen_loop'`.
- **`Picarx()` cannot be instantiated from another process** while `server.py` runs (`GPIO busy`) — workaround:
  `robot_hat.Servo` directly, `core.capture.capture()` for images (`robot-bridges`).
- **Servo stuck in software** → wiggle it (right-left-centre) or reboot; after reboot 0 = straight.
- **`Picarx()` init sudo warning = non-fatal.**
- **Grayscale module removed (optional hardware)** → no line following; init is still OK.
- **Stuck wheel emergency recipe:** fresh `Picarx()` + zeroed calibration + stop → `reset_mcu()` → `leallit.py`.

## Where to write new knowledge
Hardware, access, operating recipe → `technika/eszkoztar.md` · Wi-Fi/network → your auto-memory ·
incident story → the memoria-mcp journal. Then **scp it to the Pi**.
