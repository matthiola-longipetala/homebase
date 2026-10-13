# Phase 0 — kit-arrival checklist (assembly + Pi setup + smoke test)

> Goal: from unboxing the kit to the point where the robot is mechanically complete, the headless Pi is on the
> network, the SunFounder library is installed and a smoke test moves the robot.
> **The MCP server (phase 1) comes only AFTER this** — the skeleton is ready, it only has to be copied over.

**Realistic expectation:** the PiCar-X is a *build-it-yourself* kit (screws, servos, wiring). One evening is
realistically: unboxing + mechanical assembly + flashing the Pi + library + servo calibration + smoke test.
The phase 1 MCP test will most likely be finished the next day. Promise nothing in advance — only what you can
actually demonstrate.

---

## A. Headless Pi preparation (can even be done BEFORE unboxing the kit)

With Raspberry Pi Imager (the cleanest way — everything can be preset, no file editing needed):

1. microSD card into the card reader.
   - **Starter sets:** even if the card comes PRE-imaged (it often does), it is worth flashing a fresh image to
     get a clean, known state.
2. Raspberry Pi Imager → **Raspberry Pi OS (64-bit), Bookworm** (NOT Lite, because `vilib`/Picamera2 plus
   occasional desktop debugging is more comfortable — but Lite is enough if you want to save space; the robot
   stack runs on both).
3. ⚙️ (gear / "Advanced options") before flashing:
   - **hostname:** e.g. `picar` → later `<robot-host>.local` on the LAN
   - **SSH enabled**, password (or key) authentication
   - **user:** `pi` + password (write it down — NOT in this file)
   - **Wi-Fi:** SSID + password + country. **You type the password here yourself** — the agent does not store
     it. Band: **2.4 GHz** is recommended for the robot (longer range indoors); if the 5 GHz SSID has a
     separate name, you can choose that too.
   - **Locale:** your time zone
4. Flash → card into the Pi → power on.
5. A few minutes later, from the laptop:
   ```
   ssh pi@<robot-host>.local
   ```
   (If `<robot-host>.local` does not resolve on Windows: router admin → the Pi's IP, and `ssh pi@<IP>`.
   This is where you decide between mDNS and a fixed IP.)

## B. Pi base configuration (over SSH)

```bash
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config        # Interface: I2C ON, SPI ON (for the Robot HAT), Camera usually auto
sudo reboot
```

## C. SunFounder library + audio (the robot stack)

The official SunFounder PiCar-X installer (Bookworm branch):

```bash
cd ~
git clone -b v2.0 https://github.com/sunfounder/picar-x.git picar-x
git clone https://github.com/sunfounder/robot-hat.git
git clone https://github.com/sunfounder/vilib.git

cd ~/robot-hat && sudo python3 setup.py install
cd ~/vilib && sudo python3 install.py
cd ~/picar-x && sudo python3 setup.py install

# I2S amplifier (speaker) — REQUIRED for speech (phase 4), but can be run now:
cd ~/picar-x
sudo bash i2samp.sh        # asks for a reboot at the end
```

> Note: the SunFounder installer sometimes asks for Picamera2 / other apt packages — the scripts usually
> install them themselves. If it gets stuck, look at the error before continuing.

## D. Servo zeroing and mechanical assembly

Assemble the PiCar-X in the order of the SunFounder guide. CRITICAL step: set the servos to **0° BEFORE
mounting them**, otherwise the steering/camera will be crooked:

```bash
cd ~/picar-x/example
sudo python3 servo_zeroing.py   # the servo goes to middle → fix the arm at 0° now
```

(The kit uses 3 servos: steering + camera pan + camera tilt.)

## E. Smoke test (the phase is "done" when these work)

```bash
cd ~/picar-x/example
sudo python3 move.py        # or the official example — the robot moves
# camera test:
python3 -c "from vilib import Vilib; import time; Vilib.camera_start(); time.sleep(2); Vilib.take_photo('t','{PI_HOME}/'); print('kep ok')"
```

✅ The robot moves forward/backward/turns on command
✅ The camera delivers an image
✅ (optional) sound comes from the speaker after `i2samp`

---

## F. Moving on to phase 1 (the skeleton is ready)

```bash
# copy the staging folder (picarx-mcp/) to the Pi, e.g. with scp from the laptop:
scp -r picarx-mcp pi@<robot-host>.local:{PI_HOME}/
ssh pi@<robot-host>.local
cd {PI_HOME}/picarx-mcp
cp config.example.json config.json
python3 -m pip install -r requirements.txt --break-system-packages   # Bookworm: PEP 668
python3 server.py    # manual test: "picarx-mcp on <host>:8765/sse" (host from config.json)
```

Then add the MCP server on the **Claude Code** side (`claude mcp add --transport sse
picarx-mcp http://<robot-host>.local:8765/sse` — Claude Code supports SSE natively, no proxy needed),
and run the phase 1 test protocol (status → move → passive mode).
The operator is Claude Code, not Cowork (see `README.md`).

## Decisions to make before you start (none of them blocking — pick a default and adjust later)

1. Is the starter set's SD card pre-imaged, or do you flash from scratch?
2. `<robot-host>.local` (mDNS) or a fixed IP on the router? (mDNS is cleaner, a fixed IP is more robust.)
3. Wi-Fi band for the robot: 2.4 or 5 GHz? (2.4 GHz is the suggested default.)

**Never stored in the repository or the chat:** the Wi-Fi password and the ElevenLabs key — type them in
yourself in the right place.
