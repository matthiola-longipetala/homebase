# -*- coding: utf-8 -*-
"""szemlelo_laptop.py — THE OBSERVER: unsolicited camera info, the listener's counterpart.

Runs ON THE LAPTOP (as a Monitor); nothing new is needed on the Pi. A picture is taken from
two sources:
  1. timer: every 10 minutes by default;
  2. sentinel trigger: when a tilt/lifted line (see TRIGGER_SZAVAK) appears in the Pi's
     /tmp/orszem_events.log (with a 60 s hold-off so it does not keep clicking).
The picture is taken on the Pi with core.capture.capture() (which handles the stream lease),
copied to the laptop's scratch folder, and ONE line goes to stdout:
    [observer] <reason> → <local path>
The Monitor hands this to the agent as an event, and the agent looks at it with the Read tool —
NO YOLO, the agent is the eye. Pictures are not kept: taking a new picture deletes the old
ones (at most `--tart` pictures are kept). No faces are stored anywhere.

SWITCH (for the household): `touch /tmp/szemlelo_off` on the Pi OR a SZEMLELO_KI file in this
folder on the laptop -> the observer pauses (re-checks every minute). Quiet hours: no
pictures 23:00–07:00.

Start as a Monitor (from the picarx-mcp folder):
    PYTHONUTF8=1 python -u szemlelo_laptop.py [--perc/--minutes 10] [--tart/--keep 3]
One-shot test (one picture, then exit):
    python szemlelo_laptop.py --egyszer      (or --once)
"""
import argparse, io, os, subprocess, sys, threading, time, datetime as dt

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

ITT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ITT)
import laptop_config  # connection settings (env or config.json)
KULCS = laptop_config.ssh_key()
PI = laptop_config.pi_target()
SSH = ["ssh", "-i", KULCS, "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", PI]
SCP = ["scp", "-i", KULCS, "-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]
KI_FAJL = os.path.join(ITT, "SZEMLELO_KI")
KEP_MAPPA = os.environ.get("SZEMLELO_MAPPA") or os.path.join(os.environ.get("TEMP", ITT), "szemlelo")
# Matched against orszem.py event lines; "lifted"/"tilted" match its English tilt event.
TRIGGER_SZAVAK = ("fény-váltás", "feny-valtas", "felemeltek", "padló-ugrás", "padlo-ugras", "ferde", "lifted", "tilted")
CSENDES = (23, 7)          # no pictures from this hour until that hour


def most():
    return dt.datetime.now().strftime("%H:%M:%S")


def kikapcsolva():
    if os.path.exists(KI_FAJL):
        return "laptop: SZEMLELO_KI"
    try:
        r = subprocess.run(SSH + ["test -e /tmp/szemlelo_off && echo OFF || echo ON"], capture_output=True, text=True, timeout=15)
        if "OFF" in r.stdout:
            return "Pi: /tmp/szemlelo_off"
    except Exception:
        pass
    return None


def csendes_ora():
    h = dt.datetime.now().hour
    a, b = CSENDES
    return h >= a or h < b


def kep(ok, tart):
    """One picture from the Pi to the laptop. Returns the local path or None."""
    os.makedirs(KEP_MAPPA, exist_ok=True)
    nev = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".jpg"
    helyi = os.path.join(KEP_MAPPA, nev)
    parancs = ("cd ~/picarx-mcp && python3 -c \""
               "import os, sys; sys.path.insert(0,os.path.expanduser('~/picarx-mcp')); from core.capture import capture; "
               "capture('/tmp/szemlelo.jpg', 1152, 648); print('OK')\" 2>/dev/null")
    try:
        r = subprocess.run(SSH + [parancs], capture_output=True, text=True, timeout=60)
        if "OK" not in r.stdout:
            print(f"[observer] {most()} no picture taken ({ok}): {r.stdout.strip()[-120:]} {r.stderr.strip()[-120:]}")
            return None
        s = subprocess.run(SCP + [f"{PI}:/tmp/szemlelo.jpg", helyi], capture_output=True, text=True, timeout=60)
        if s.returncode != 0 or not os.path.exists(helyi):
            print(f"[observer] {most()} transfer ERROR: {s.stderr.strip()[-120:]}")
            return None
    except subprocess.TimeoutExpired:
        print(f"[observer] {most()} timeout ({ok})")
        return None
    except Exception as e:
        print(f"[observer] {most()} ERROR: {e}")
        return None
    regiek = sorted(f for f in os.listdir(KEP_MAPPA) if f.endswith(".jpg"))
    for f in regiek[:-tart] if tart > 0 else regiek[:-1]:
        try: os.remove(os.path.join(KEP_MAPPA, f))
        except Exception: pass
    print(f"[observer] {ok} → {helyi}")
    return helyi


def orszem_figyelo(jelzes):
    """In a separate thread: follows the Pi's sentinel log and signals on a trigger word."""
    while True:
        try:
            p = subprocess.Popen(SSH + ["tail -n 0 -F /tmp/orszem_events.log"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
            for sor in p.stdout:
                s = sor.strip()
                if any(k in s for k in TRIGGER_SZAVAK):
                    jelzes.append(s[:80])
            p.wait()
        except Exception:
            pass
        time.sleep(20)          # the Pi is not reachable (switched off?) — retry later


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--perc", "--minutes", type=float, default=10.0, help="timed picture every N minutes (default 10)")
    ap.add_argument("--tart", "--keep", type=int, default=3, help="how many pictures to keep in the folder (default 3)")
    ap.add_argument("--egyszer", "--once", action="store_true", help="one picture, then exit")
    ap.add_argument("--nincs-orszem", "--no-sentinel", action="store_true", help="do not follow the sentinel log")
    a = ap.parse_args()
    if a.egyszer:
        kep("test", a.tart); return
    print(f"[observer] {most()} starting: every {a.perc:g} minutes + sentinel trigger; switch: SZEMLELO_KI / Pi:/tmp/szemlelo_off; quiet {CSENDES[0]}–{CSENDES[1]} h; folder {KEP_MAPPA}")
    jelzes = []
    if not a.nincs_orszem:
        threading.Thread(target=orszem_figyelo, args=(jelzes,), daemon=True).start()
    utolso = 0.0; utolso_trigger = 0.0
    while True:
        t = time.time()
        ki = kikapcsolva()
        if ki:
            if t - utolso > 3600: print(f"[observer] {most()} paused ({ki})"); utolso = t
            jelzes.clear(); time.sleep(60); continue
        if csendes_ora():
            jelzes.clear(); time.sleep(60); continue
        if jelzes and t - utolso_trigger > 60:
            ok = "sentinel: " + jelzes[-1]; jelzes.clear()
            kep(ok, a.tart); utolso_trigger = t; utolso = t
        elif t - utolso >= a.perc * 60:
            kep("timer", a.tart); utolso = t
        time.sleep(2)


if __name__ == "__main__":
    main()
