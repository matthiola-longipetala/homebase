---
name: robot-sensors-sentinel
description: The PiCar-X robot's body sense — the QWIIC sensor chain (light, IMU/heading, downward laser distance, temperature/humidity/pressure), sonar, battery protection, and the sentinel that reports by itself when something changes. Load it when you read a sensor, are asked how the robot "feels", there is a battery or temperature matter, or sentinel events need interpreting. Triggers: "how do you feel", "it's hot", "how bright", "battery level", "sentinel", "I was lifted", "sensor", "IMU", "compass", "temperature".
---

# Robot — SENSORS AND SENTINEL

**Canonical knowledge: `picarx-mcp/technika/eszkoztar.md` — "QWIIC sensor chain" (addresses, orientation, wiring,
pin map) and `tanulsagok/erzekeles.md` (sonar truths, false readings). This skill is navigation only.**
I2C addresses, GPIO pins and calibration values do NOT go here.

## Readers
| Command | For |
|---|---|
| `python3 erzet.py [json]` | **one-line well-being snapshot** ("feeling"): light + climate + heading + floor distance + battery (+ `/tmp/erzet.json`). GPIO-free → can run any time |
| `python3 lux.py` | light (VEML7700, on top, facing up) |
| `python3 imu.py [loop\|calib]` | heading / roll / pitch (BNO055) |
| `python3 climate.py` | temperature / humidity / pressure (BME280) |
| `python3 cliff.py [guard <base>]` | downward laser distance — cliff guard, edges |
| `python3 rear_sonar.py once\|loop` | rear sonar |
| `python3 preflight.py` | session-start check — its QWIIC section walks the whole chain |
| `python3 orszem.py` | body-sense sentinel ("sentry") — run it as ONE service only (see below), never a second copy by hand |

## The sentinel (`orszem.py`) — the automatic sensing layer
- **Runs from boot as a systemd service** — your own unit file (not shipped; pattern: `battery_guard.service`).
  GPIO/camera-free, so it is boot-safe.
- Pushes events into the chat: `[sense: …]` (lifted / light change / warming / battery / rain sign).
  Log: `/tmp/orszem_events.log` — chat bridge: Monitor `tail -n 0 -F /tmp/orszem_events.log` (`robot-bridges`).
- **Fresh snapshot for other scripts: `/tmp/erzet_folyam.json`** — read here instead of re-instantiating sensors.
- **Pause during motion scripts is recommended** (bus relief): `touch /tmp/orszem_off`; back: `rm /tmp/orszem_off`.
- **The signal is the sentinel's, the decision is yours.** An event is not a command.
- **ONE instance only.** The Monitor command is EXCLUSIVELY `tail -n 0 -F /tmp/orszem_events.log`. A Monitor
  that runs its own `orszem.py` next to the service gives two sentinels, two baselines on one I2C bus, repeated
  false alarms — and a patch that "has no effect" because the extra copy runs old code. Check:
  `pgrep -af '[o]rszem.py'` equals `systemctl show <your-unit> -p MainPID --value`; kill orphans with `kill -TERM`.
  After a restart it needs a few warm-up rounds (no alarms).

## Battery
- `/tmp/battery_status` — format `V PCT STATE`. Below WARN **tell the owner** to charge.
- `battery_guard.service` (systemd, root) shuts down automatically at critical — **leave it running.**
- **The % gauge misleads; voltage sag matters:** at load peaks the peripherals (ReSpeaker/USB) can drop out well
  before WARN. **On long or outdoor sessions ask for the charger early.** Idle draw and discharge curve: eszkoztar.
- If the robot "looks dead" (red lit, green barely blinking): **normal when idle.** The guard log is the witness,
  not the LED. Suspect Wi-Fi first (location-dependent dead zone), only then battery or SD.

## Traps (these sensors can lie)
- **BNO055 instantiation = SOFT RESET → calibration lost.** `imu.py`'s module init reloads the offsets from your
  `bno055_offsets.json` (made in a calibration session, see eszkoztar; not shipped) at every start.
  **In a new script: `import imu` — do NOT instantiate a separate BNO055.**
- **One-GPIO-process rule:** heading reader + motor control in ONE process; a turn subprocess launched next to a
  Picarx wrapper = `GPIO busy`.
- **Metal guard:** near metal the magnetometer is distorted; `smart_turn`/`go_heading` take a median while still and
  warn if it jumps. Next to metal a blind turn + camera may be better.
- **The downward laser SEES THROUGH glass** — do not trust the cliff guard on glass. A dark/distant surface gives
  false lift signals → you need the **IMU as witness**.
- **BME280 first read = reset phantom** (a constant round temperature) — the second reading is the true one.
- **Black doormat = sonar phantom.** If sonar and image disagree, the sonar sees a DIFFERENT surface — geometry lies, not the sensor.
- **Preflight QWIIC gap = loose cable** → open the chain one link at a time, do not guess.
- **Before taking a new pin a 3-LAYER PIN AUDIT is mandatory** (kernel claim + the `robot_hat` NAME table +
  physical parts on the HAT), and **test before soldering**: overlay in software, reboot, Picarx init test —
  solder only after green. Taken/free pins: eszkoztar "PIN MAP".
- **The PWM headers are 5 V, and the 5 V rail has a budget** — load a new consumer preferably while standing still.

## When a new sensor idea comes
Pin map first (eszkoztar "PIN MAP"), then the audit, then the trial, and ONLY then soldering. A pin measured as
"free" can have a hidden tenant (the HAT MCU reset line is claimed only transiently).

## Where to write new knowledge
Sensor fact, wiring, pin map → `technika/eszkoztar.md` ("QWIIC sensor chain") ·
false reading, perception illusion → `tanulsagok/erzekeles.md`. Then **scp it to the Pi**.
