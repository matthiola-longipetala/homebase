#!/usr/bin/env python3
"""orszem.py — BODY-SENSE SENTINEL: the automatic, event-driven layer of the senses.

The idea: automatic sensing helps more than polling — e.g. the CPU heating up on a
sunny terrace -> signal -> roll into the shade. It applies the pattern of the
listener/soundscape to the other senses: reads EVERYTHING every few seconds, but
only speaks up on a CHANGE/THRESHOLD.

Two-level split (Brooks): SIGNALLING belongs to the sentinel, the DECISION to the agent.

Event channel: a stdout line = a Monitor event (as with the listener), format:
    [sense: lifted/tilted | pitch -27°]
Side channel: /tmp/erzet_folyam.json — a fresh snapshot every round
(other scripts CAN READ from here without re-instantiating the sensors — this is
also the long-term direction for resolving bus contention).

Pausing (hangkep_off pattern): `touch /tmp/orszem_off` -> the sentinel does not
touch the sensors (recommended while a motion script runs); `rm` -> it resumes.

Events + thresholds (with hysteresis, 60 s cooldown per type):
  lifted/tilted     |pitch| or |roll| > 12° (relative to the resting baseline)
  floor jump        ToF > baseline+80 mm (lifted/edge — suspicious for a stationary robot)
  light BAND CHANGE category bands — the lux falls into 5 bands (dark <2 /
                    dim 2–50 / room light 50–300 / bright light 300–5000 /
                    sunlight 5000+); signal = a BAND CHANGE that is stable for
                    2 consecutive reads. (The old ratio+delta formula swallowed an
                    11->1 lux light switch — that was the bug.)
  temperature step  |T − baseline| >= 1.5 °C (do not report every 0.1 degree)
  humidity jump     humidity >= baseline+10% suddenly (example: entering a steamy
                    bathroom; live steam test: 54->94% jump. The slow rain formula is
                    separate.)
  running hot       CPU > 70 °C (above 75 an urgent repeat even without cooldown)
  battery change    battery_status state transition (OK->WARN etc.)
  rain sign         pressure drop > 2 hPa / 30 min AND rising humidity

TUNING: every threshold is in the TUNING block below — for live tuning sessions
  (a human creates live events: lights on/off, window, etc., and we adjust).

Start (via the Monitor bridge):
    ssh ... "cd ~/picarx-mcp && PYTHONUTF8=1 python3 -u orszem.py 2>/tmp/orszem.err"
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/picarx-mcp"))

POLL_S = 3.0
COOLDOWN_S = 60.0
OFF_FLAG = "/tmp/orszem_off"
FOLYAM = "/tmp/erzet_folyam.json"

# ================= TUNING — the dashboard for sense tuning ==================
# (prepared for joint calibration with a human; one fact = one place)
FENY_SAVOK = [                # (upper limit lx, name) — the last one is the ceiling
    (2, "dark"),
    (50, "dim"),
    (300, "room light"),
    (5000, "bright light"),
    (float("inf"), "sunlight"),
]
FENY_STABIL_KOR = 2           # the new band must hold for this many consecutive rounds before we speak
HO_LEPCSO_C = 1.5             # a temperature shift this large vs. the baseline = signal
PARA_UGRAS_PCT = 10.0         # a sudden humidity rise this large vs. the baseline = signal
FERDE_FOK = 12.0              # roll/pitch deviation from the resting baseline
TOF_UGRAS_MM = 80             # floor-distance jump threshold
CPU_MELEG_C = 70.0            # warning
CPU_SURGOS_C = 75.0           # urgent (no cooldown)
ESO_DP_HPA = -2.0             # pressure drop within 30 minutes
ESO_DH_PCT = 3.0              # humidity rise over the same window
# =============================================================================


def feny_sav(lux):
    """lux -> (band index, band name)"""
    for i, (hatar, nev) in enumerate(FENY_SAVOK):
        if lux < hatar:
            return i, nev
    return len(FENY_SAVOK) - 1, FENY_SAVOK[-1][1]

# --- senses: separate try blocks (worth running even with some missing) ---
_hiany = []
try:
    import lux as _lux
except Exception as e:
    _lux = None; _hiany.append(f"light({e.__class__.__name__})")
try:
    import climate as _climate
except Exception as e:
    _climate = None; _hiany.append(f"climate({e.__class__.__name__})")
try:
    import imu as _imu
except Exception as e:
    _imu = None; _hiany.append(f"imu({e.__class__.__name__})")
try:
    import cliff as _cliff
except Exception as e:
    _cliff = None; _hiany.append(f"tof({e.__class__.__name__})")

if _hiany:
    print(f"[sense: sentinel starting, sleeping senses: {', '.join(_hiany)}]", flush=True)
else:
    print("[sense: sentinel starting, all four senses + CPU temperature + battery alive]", flush=True)


def cpu_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


def akku():
    try:
        v, pct, state = open("/tmp/battery_status").read().split()
        return {"V": float(v), "pct": int(pct), "allapot": state}
    except Exception:
        return None


def olvas():
    """One full sensing round; every field tolerates None."""
    d = {"ts": time.time()}
    if _lux:
        try:
            d["lux"] = _lux.read_once()
        except Exception:
            d["lux"] = None
    if _climate:
        try:
            d["klima"] = _climate.read_once()
        except Exception:
            d["klima"] = None
    if _imu:
        try:
            e = _imu.sensor.euler
            d["heading"], d["roll"], d["pitch"] = (
                (round(e[0], 1), round(e[1], 1), round(e[2], 1)) if e and e[0] is not None
                else (None, None, None))
        except Exception:
            d["heading"] = d["roll"] = d["pitch"] = None
    if _cliff:
        try:
            d["tof_mm"] = _cliff.read_mm(samples=1, timeout_s=1.0)
        except Exception:
            d["tof_mm"] = None
    d["cpu_c"] = cpu_c()
    d["akku"] = akku()
    return d


class Esemeny:
    """Event horn with a per-type cooldown."""
    def __init__(self):
        self.utolso = {}

    def szol(self, tipus, uzenet, surgeto=False):
        t = time.time()
        if not surgeto and t - self.utolso.get(tipus, 0) < COOLDOWN_S:
            return
        self.utolso[tipus] = t
        print(f"[sense: {uzenet}]", flush=True)


kurt = Esemeny()

# rolling baselines
feny_sav_akt = None       # (index, name) — the current light band
feny_sav_jelolt = None    # (index, round count) — change candidate with a stability counter
ho_alap = None            # temperature baseline for step detection
para_alap = None          # humidity baseline for jump detection
tof_alap = None
tilt_alap = None          # (roll, pitch) at rest
nyomas_ablak = []         # (ts, hPa) — 30-minute window
utolso_akku_allapot = None
akku_jelolt = None        # hysteresis — a new battery state is believed only after N consecutive rounds
akku_jelolt_db = 0
AKKU_MEGEROSITES = 12     # under speech load the state flipped OK->WARN->OK between 34%/62%; a single read does not count
kor = 0                   # warm-up — in the first rounds the sensors (BNO055, BME) still fluctuate, no alarms
BEMELEG_KOR = 6
ferde_jelolt = 0          # two consecutive rounds are needed for the tilt signal (a single IMU jump does not count)

_szol_eredeti = kurt.szol
def _szol_bemeleg(kulcs, *a, **k):
    if bemelegedes and kulcs != 'akku':
        return
    return _szol_eredeti(kulcs, *a, **k)
kurt.szol = _szol_bemeleg
bemelegedes = True

while True:
    if os.path.exists(OFF_FLAG):
        time.sleep(POLL_S)
        continue
    d = olvas()
    kor += 1
    bemelegedes = kor <= BEMELEG_KOR

    # --- tilted / lifted ---
    imu_mozgas = False   # gyroscope witness for the ToF branch (against false "lifted" events, e.g. on a box top)
    if d.get("pitch") is not None:
        if tilt_alap is None:
            tilt_alap = (d["roll"], d["pitch"])
        dr = abs(d["roll"] - tilt_alap[0])
        dp = abs(d["pitch"] - tilt_alap[1])
        imu_mozgas = (dr > 1.5 or dp > 1.5)
        if dr > FERDE_FOK or dp > FERDE_FOK:
            ferde_jelolt += 1
            if ferde_jelolt >= 2 and not bemelegedes:
                kurt.szol("ferde", f"lifted/I am tilted | roll {d['roll']}° pitch {d['pitch']}°")
        elif dr < 3 and dp < 3:
            ferde_jelolt = 0
            # relaxation: the baseline slowly follows the resting state
            tilt_alap = (0.8 * tilt_alap[0] + 0.2 * d["roll"],
                         0.8 * tilt_alap[1] + 0.2 * d["pitch"])

    # --- floor-distance jump ---
    if d.get("tof_mm") is not None:
        if tof_alap is None:
            tof_alap = d["tof_mm"]
        elif d["tof_mm"] > tof_alap + TOF_UGRAS_MM:
            if imu_mozgas:
                kurt.szol("tof", f"floor-distance jump | {d['tof_mm']:.0f} mm (baseline {tof_alap:.0f})")
            # without an IMU witness: a hallucination from a dark/distant surface -> silently dropped
        else:
            tof_alap = 0.7 * tof_alap + 0.3 * d["tof_mm"]

    # --- light BAND CHANGE (category bands, 2-round stability) ---
    if d.get("lux") is not None:
        idx, nev = feny_sav(d["lux"])
        if feny_sav_akt is None:
            feny_sav_akt = (idx, nev)
        elif idx != feny_sav_akt[0]:
            if feny_sav_jelolt and feny_sav_jelolt[0] == idx:
                feny_sav_jelolt = (idx, feny_sav_jelolt[1] + 1)
            else:
                feny_sav_jelolt = (idx, 1)
            if feny_sav_jelolt[1] >= FENY_STABIL_KOR:
                irany = "got brighter" if idx > feny_sav_akt[0] else "got darker"
                kurt.szol("feny", f"{irany}: {feny_sav_akt[1]}→{nev} | {d['lux']:.1f} lx")
                feny_sav_akt = (idx, nev)
                feny_sav_jelolt = None
        else:
            feny_sav_jelolt = None

    # --- climate artefact filter: during warm-up, or with an impossible pressure/humidity, the whole climate branch is skipped ---
    if d.get("klima"):
        _P = d["klima"].get("legnyomas_hPa"); _H = d["klima"].get("para_pct")
        if bemelegedes or _P is None or not (850 <= _P <= 1100) or _H is None or not (0 <= _H <= 100):
            d["klima"] = None

    # --- temperature step ---
    if d.get("klima") and d["klima"].get("homerseklet_C") is not None:
        t = d["klima"]["homerseklet_C"]
        # sensor artefact filter (-9 °C was reported several times in a 27 °C room): impossible value -> dropped, baseline untouched
        if t < 0 or t > 60 or (ho_alap is not None and abs(t - ho_alap) > 15):
            pass
        elif ho_alap is None:
            ho_alap = t
        elif abs(t - ho_alap) >= HO_LEPCSO_C:
            irany = "is warming up" if t > ho_alap else "is cooling down"
            kurt.szol("ho", f"the room {irany} | {ho_alap:.1f}→{t:.1f} °C")
            ho_alap = t

    if d.get("klima") and d["klima"].get("para_pct") is not None:
        p = d["klima"]["para_pct"]
        if para_alap is None:
            para_alap = p
        elif p - para_alap >= PARA_UGRAS_PCT:
            kurt.szol("para", f"I reached a humid place | {para_alap:.0f}→{p:.0f}%")
            para_alap = p
        else:
            para_alap = 0.9 * para_alap + 0.1 * p

    # --- running hot (CPU) ---
    if d.get("cpu_c") is not None:
        if d["cpu_c"] >= CPU_SURGOS_C:
            kurt.szol("cpu", f"I AM HOT, urgent | CPU {d['cpu_c']:.0f}°C — need shade/rest", surgeto=True)
            time.sleep(5)  # do not machine-gun even on the urgent branch
        elif d["cpu_c"] >= CPU_MELEG_C:
            kurt.szol("cpu", f"I am getting hot | CPU {d['cpu_c']:.0f}°C")

    # --- battery state transition ---
    if d.get("akku"):
        a = d["akku"]["allapot"]
        if utolso_akku_allapot is None:
            utolso_akku_allapot = a
        elif a != utolso_akku_allapot:
            if a == akku_jelolt:
                akku_jelolt_db += 1
            else:
                akku_jelolt, akku_jelolt_db = a, 1
            if akku_jelolt_db >= AKKU_MEGEROSITES:
                kurt.szol("akku", f"battery state change | {utolso_akku_allapot}→{a} ({d['akku']['V']:.2f} V, {d['akku']['pct']}%, over {AKKU_MEGEROSITES} rounds)", surgeto=True)
                utolso_akku_allapot = a
                akku_jelolt, akku_jelolt_db = None, 0
        else:
            akku_jelolt, akku_jelolt_db = None, 0

    # --- rain sign (pressure drop + humidity rise, 30-minute window) ---
    if d.get("klima"):
        most = time.time()
        nyomas_ablak.append((most, d["klima"]["legnyomas_hPa"], d["klima"]["para_pct"]))
        nyomas_ablak = [x for x in nyomas_ablak if most - x[0] < 1800]
        if len(nyomas_ablak) > 10:
            regi = nyomas_ablak[0]
            dP = d["klima"]["legnyomas_hPa"] - regi[1]
            dH = d["klima"]["para_pct"] - regi[2]
            if dP < ESO_DP_HPA and dH > ESO_DH_PCT:
                kurt.szol("eso", f"rain sign? | pressure {dP:+.1f} hPa / humidity {dH:+.0f}% within 30 min")

    # --- stream snapshot (other scripts may read it) ---
    try:
        with open(FOLYAM, "w") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass

    time.sleep(POLL_S)
