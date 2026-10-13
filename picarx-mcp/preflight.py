"""preflight.py — session pre-check: a quick check of the WHOLE chain in ONE command.

Instead of three separate bug hunts (mute speaker card number, XMOS silent hang,
Scribe timeout freeze) this measures the critical points in about 10 seconds.
Run it at the start of EVERY robot session:

    ssh ... 'cd ~/picarx-mcp && PYTHONUTF8=1 python3 preflight.py'

Exit code: 0 = all OK; 1 = there is a FAIL.
"""
import glob, json, os, re, subprocess, sys, time

OK = []; WARN = []; FAIL = []
def ok(m): OK.append(m); print(f"  [OK]   {m}")
def warn(m): WARN.append(m); print(f"  [WARN] {m}")
def fail(m): FAIL.append(m); print(f"  [FAIL] {m}")
def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""

print("=== PREFLIGHT ===")

# 1) BATTERY
try:
    v, pct, state = open("/tmp/battery_status").read().split()
    v = float(v)
    if v >= 7.2: ok(f"battery {v:.2f} V / {pct}% ({state})")
    elif v >= 6.9: warn(f"battery LOW: {v:.2f} V / {pct}% -> plan charging")
    else: fail(f"battery NEAR CRITICAL: {v:.2f} V -> CHARGE, do not drive")
except Exception as e:
    fail(f"battery status not readable (/tmp/battery_status): {e} -- is battery_guard running?")
bg = sh("systemctl is-active battery_guard").strip()
ok("battery_guard service active") if bg == "active" else fail(f"battery_guard service: {bg or 'cannot be queried'}")

# 2) SPEAKER (HifiBerry) -- BY NAME! (the card number can shift after a reboot)
aplay = sh("aplay -l")
if "sndrpihifiberry" in aplay:
    n = re.search(r"card (\d+): sndrpihifiberry", aplay)
    ok(f"HifiBerry speaker present (now card {n.group(1) if n else '?'} -- but we address it BY NAME)")
else:
    fail("HifiBerry (sndrpihifiberry) is NOT visible in aplay -l")
say = open(os.path.expanduser("~/picarx-mcp/robot_say.py")).read() if os.path.exists(os.path.expanduser("~/picarx-mcp/robot_say.py")) else ""
if 'CARD=sndrpihifiberry' in say: ok("robot_say.py addresses the card by NAME")
else: fail("robot_say.py does NOT address the card by name -> risk of a mute jack")

# 3) MICROPHONE (ReSpeaker Array)
arec = sh("arecord -l")
m = re.search(r"card (\d+): Array", arec)
if not m:
    fail("ReSpeaker (Array) is NOT visible in arecord -l -> USB/cable?")
else:
    card = m.group(1)
    ok(f"ReSpeaker present (card {card})")
    status_f = f"/proc/asound/card{card}/pcm0c/sub0/status"
    st = open(status_f).read() if os.path.exists(status_f) else ""
    if "RUNNING" in st:
        # a recording is already running -> whether it is alive shows in the hw_ptr movement
        def ptr(): return int(re.search(r"hw_ptr\s*:\s*(\d+)", open(status_f).read()).group(1))
        p1 = ptr(); time.sleep(2.0); p2 = ptr()
        ok(f"recording runs and data FLOWS (hw_ptr {p1}->{p2})") if p2 > p1 else \
            fail(f"recording runs, but hw_ptr is STUCK ({p1}) -> XMOS silent hang -> respeaker_reboot.py")
    else:
        # free -> 1 s test recording + RMS
        out = sh(f"timeout 2 arecord -D plughw:CARD=Array,DEV=0 -f S16_LE -r 16000 -c 1 -t raw -q /tmp/pf_mic.raw; "
                 f"wc -c < /tmp/pf_mic.raw")
        nbytes = int(out.strip() or 0)
        if nbytes > 8000:
            ok(f"microphone test recording alive ({nbytes} bytes/~1.5 s)")
        else:
            fail(f"microphone test recording empty/short ({nbytes} bytes) -> XMOS wedge? -> respeaker_reboot.py")

# 4) ORPHANED PROCESSES + LOCK
orph = sh("pgrep -af 'robot_listen_loop|arecord -D|rpicam' | grep -v pgrep").strip()
if orph: warn(f"running audio/camera process(es):\n         {orph}\n         (if not intended: kill -TERM)")
else: ok("no orphaned listener/arecord/rpicam")
if os.path.exists("/tmp/robot_speaking.lock"):
    age = time.time() - os.path.getmtime("/tmp/robot_speaking.lock")
    warn(f"robot_speaking.lock exists ({age:.0f} s old)" + (" -> STUCK, delete it!" if age > 60 else " (fresh, normal during speech)"))
else:
    ok("no stuck speech lock")

# 5) last lines of LISTEN.ERR (known symptoms: 'scribe non-JSON', 'overrun')
if os.path.exists("/tmp/listen.err"):
    tail = sh("tail -5 /tmp/listen.err")
    bad = [l for l in tail.splitlines() if "overrun" in l or "non-JSON" in l]   # matches robot_listen_loop.py's log text
    warn("fresh error traces in listen.err:\n         " + "\n         ".join(bad)) if bad else ok("listen.err clean (last 5 lines)")

# 6) CONFIG FILES + TIMEOUT FIX
for f in ("camera_calib.json", "tags_coords.json", "voice_config.json"):
    p = os.path.expanduser(f"~/picarx-mcp/{f}")
    try:
        json.load(open(p)); ok(f"{f} readable/valid")
    except Exception as e:
        fail(f"{f}: {e}")
lst = open(os.path.expanduser("~/picarx-mcp/robot_listen_loop.py")).read() if os.path.exists(os.path.expanduser("~/picarx-mcp/robot_listen_loop.py")) else ""
ok("robot_listen_loop curl timeout present") if "--max-time" in lst else \
    fail("robot_listen_loop curl is MISSING --max-time -> risk of going deaf for 5 minutes")

# 6b) QWIIC SENSOR CHAIN (light/IMU/ToF on i2c-1, 10 kHz; the BME280 climate sensor is on its OWN i2c-3 bus: GPIO13/GPIO6)
det = sh("/usr/sbin/i2cdetect -y 1")  # full path: /usr/sbin is not in the non-login PATH
det3 = sh("/usr/sbin/i2cdetect -y 3")
if not det.strip():
    warn("i2cdetect cannot be run -- QWIIC chain not checked")
else:
    jelen = set(det.split())
    vart = {"10": "VEML7700 light", "28": "BNO055 IMU", "29": "VL53L1X cliff guard"}
    hianyzik = [f"0x{a} ({n})" for a, n in vart.items() if a not in jelen]
    if hianyzik:
        warn(f"missing from the QWIIC chain (i2c-1): {', '.join(hianyzik)} -> loose cable? (unplug the chain one by one)")
    else:
        ok("QWIIC sensor chain complete (0x10 light, 0x28 IMU, 0x29 ToF on i2c-1)")
    if "77" in set(det3.split()):
        ok("BME280 climate present (0x77 on its own i2c-3 bus)")
    else:
        warn("BME280 climate (0x77) MISSING from the i2c-3 bus -> GPIO13/GPIO6 cable? (config.txt dtoverlay=i2c-gpio,bus=3)")

# 7) DISK
df = sh("df -h / | tail -1").split()
if df and df[4].rstrip("%").isdigit():
    used = int(df[4].rstrip("%"))
    ok(f"disk: {df[4]} used ({df[3]} free)") if used < 85 else warn(f"disk {df[4]} used -- clean up?")

print(f"=== RESULT: {len(OK)} OK, {len(WARN)} WARN, {len(FAIL)} FAIL ===")
sys.exit(1 if FAIL else 0)
