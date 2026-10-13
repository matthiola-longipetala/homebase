#!/usr/bin/env python3
"""erzet.py — well-being snapshot: all four body senses + battery in ONE command.

"What is it like here now?" — light (VEML7700), climate (BME280), posture+heading (BNO055),
floor distance (VL53L1X), battery. A human summary on stdout + a machine copy in
/tmp/erzet.json (for other tools / the cloud brain).

Does NOT touch Picarx (GPIO-free) — safe next to a running MCP wrapper/listener.
Every sense has its own try/except: if one is asleep, the others still report.

Usage:
    python3 erzet.py          # snapshot
    python3 erzet.py json     # only the raw JSON on stdout
"""
import json
import sys
import time


def _luxszo(lx):
    if lx < 10: return "dark"
    if lx < 100: return "mood lighting"
    if lx < 500: return "room light"
    if lx < 5000: return "daylight"
    return "sunshine"


def gyujt():
    d = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "hiba": []}

    try:
        v, pct, state = open("/tmp/battery_status").read().split()
        d["akku"] = {"V": float(v), "pct": int(pct), "allapot": state}
    except Exception as e:
        d["hiba"].append(f"battery: {e}")

    try:
        import lux
        d["feny_lx"] = lux.read_once()
    except Exception as e:
        d["hiba"].append(f"light: {e}")

    try:
        import climate
        d["klima"] = climate.read_once()
    except Exception as e:
        d["hiba"].append(f"climate: {e}")

    try:
        import imu
        d["imu"] = imu.read_once()
    except Exception as e:
        d["hiba"].append(f"imu: {e}")

    try:
        import cliff
        d["padlo_tav_mm"] = cliff.read_mm(samples=3)
    except Exception as e:
        d["hiba"].append(f"tof: {e}")

    return d


def emberi(d):
    sorok = []
    if "akku" in d:
        a = d["akku"]
        sorok.append(f"battery {a['V']:.2f} V / {a['pct']}% ({a['allapot']})")
    if "feny_lx" in d:
        sorok.append(f"light {d['feny_lx']} lx ({_luxszo(d['feny_lx'])})")
    if "klima" in d:
        k = d["klima"]
        sorok.append(f"{k['homerseklet_C']} °C, humidity {k['para_pct']}%, {k['legnyomas_hPa']} hPa")
    if "imu" in d and d["imu"].get("heading") is not None:
        i = d["imu"]
        ferde = ""
        if i["roll"] is not None and (abs(i["roll"]) > 10 or abs(i["pitch"]) > 10):
            ferde = " ⚠️ I AM TILTED!"
        sorok.append(f"heading {i['heading']}° (roll {i['roll']}° / pitch {i['pitch']}°){ferde}")
    if d.get("padlo_tav_mm") is not None:
        sorok.append(f"floor distance {d['padlo_tav_mm']} mm")
    if d["hiba"]:
        sorok.append("sleeping sense: " + "; ".join(d["hiba"]))
    return "SENSE: " + "  |  ".join(sorok)


if __name__ == "__main__":
    d = gyujt()
    try:
        with open("/tmp/erzet.json", "w") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "json":
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        print(emberi(d))
