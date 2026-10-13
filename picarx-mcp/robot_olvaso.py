"""
robot_olvaso.py — NON-EXPIRING, LOSSLESS robot event reader (laptop side).

Why: the Monitor tool expires after 30 minutes; a background Bash does not, but it
only notifies on EXIT. So this script:
  1. remembers where it was (BYTE OFFSET) in the three logs:
       Pi:/tmp/fulelo_events.log  (stdout of the detached listener)
       Pi:/tmp/orszem_events.log  (sentinel)
       laptop: %TEMP%/szemlelo/szemlelo_events.log (stdout of the background watcher)
  2. on start it continues from the offset (tail -c +N -F) -> lines that arrived in the
     gap between two runs are NOT lost either; if the file got shorter (Pi reboot,
     truncation) -> from 0.
  3. prints every line (stdout) and exits on the FIRST MEANINGFUL line (exit 0) -> the
     background Bash notification is the event itself; the line(s) are in the task output file.
     Meaningful = speech ([<speaker>]/[valoszinuleg …]/[ismeretlen hang]; speakers from
     ROBOT_SPEAKERS), any sentinel line, a watcher image line, or an "important" sound label
     (cat, scream, breaking glass…). Other sound labels ([hang: kopogás 0.4] etc.) are only
     printed, they do not wake.
  4. if ssh drops (robot off / wifi) -> "ROBOT-READER: ssh ended" + exit 2.

Usage (background Bash, no expiry):
  cd <repo>/picarx-mcp && PYTHONUTF8=1 python -u robot_olvaso.py
After an event simply restart the same command — thanks to the offset file it continues
from there.
Switches: --state <file>  --nincs-kilepes/--no-exit (continuous, for Monitor mode)
          --minden-hang/--all-sounds (sound labels wake too)
"""
import argparse, json, os, re, subprocess, sys, threading, time

# Switch: --nincs-emlek. Without the server it is an empty string, never raises.
NINCS_EMLEK = "--nincs-emlek" in sys.argv
# Optional memory-index client: its folder comes from EMLEK_INDEX_DIR; without it, no [EMLEK] lines.
try:
    if not os.environ.get("EMLEK_INDEX_DIR"):
        raise ImportError("EMLEK_INDEX_DIR not set")
    sys.path.insert(0, os.environ["EMLEK_INDEX_DIR"])
    from emlek_kliens import emlek_sor as _emlek_sor
except Exception:
    def _emlek_sor(_s):
        return ""


def emlek_kiir(szoveg):
    if NINCS_EMLEK:
        return
    try:
        s = _emlek_sor(szoveg)
        if s:
            print(s, flush=True)
    except Exception:
        pass

import laptop_config  # connection settings (env or config.json)
KULCS = laptop_config.ssh_key()
PI = laptop_config.pi_target()
FUL = "/tmp/fulelo_events.log"
ORSZ = "/tmp/orszem_events.log"
SZ_MAPPA = os.path.join(os.environ.get("TEMP") or __import__("tempfile").gettempdir(), "szemlelo")
SZ_LOG = os.path.join(SZ_MAPPA, "szemlelo_events.log")
STATE_ALAP = os.path.join(SZ_MAPPA, "robot_olvaso_state.json")

# Speaker labels written by the speaker-recognition daemon. The enrolled names come from
# ROBOT_SPEAKERS (comma-separated, e.g. "Alice,Bob"); the generic labels are always accepted.
_BESZELOK = [re.escape(n.strip()) for n in os.environ.get("ROBOT_SPEAKERS", "").split(",") if n.strip()]
BESZED = re.compile(r"^\[(" + "|".join(_BESZELOK + [r"valoszinuleg [^\]]+", "ismeretlen hang"]) + r")\]")


def beszed_resz(sor):
    """Text of a speech line (without the label), or None if it is not speech. A line WITHOUT a
    label is speech too (if the speaker daemon is not running, the listener writes the sentence
    without a label; otherwise the [EMLEK] memory lines would be lost)."""
    if BESZED.match(sor):
        return BESZED.sub("", sor, 1)
    if sor.startswith("[hang:") or not sor.strip():
        return None
    return sor
# Sound labels that do NOT wake: machine/household noise only. Everything else wakes (purring,
# meowing, laughter, coughing, crying…). The names are the Hungarian display names from
# hangkep_labels.py (plus untranslated English YAMNet names), so they must match it.
ZAJ_HANG = re.compile(
    r"\[hang: (kopogás|gépelés|írás|Engine[^\]]*|motor|csettintés|csapódás|kattogás|Filing[^\]]*|"
    r"Crumpling[^\]]*|Gobble|Cap gun|Shofar|Beatboxing|mosogató|elektromos szerszám|ajtó|légzés|"
    r"zene|vonyítás|galamb|méh, darázs|haszonállat|Pig|horkolás|Sizzle[^\]]*|háttérzaj)", re.I)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load_state(p):
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        st = {k: int(d.get(k, 0)) for k in ("ful", "orsz", "sz")}
        st["boot"] = str(d.get("boot", ""))
        return st
    except Exception:
        return {"ful": 0, "orsz": 0, "sz": 0, "boot": ""}


def save_state(p, st):
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, p)
    except Exception:
        pass


def erdemi(cimke, sor, minden_hang):
    if cimke == "ORSZEM":
        return True
    if cimke == "SZ":
        return "→" in sor or "ERROR" in sor   # szemlelo_laptop.py image line or error line
    # EAR
    if BESZED.match(sor):
        return True
    if sor.startswith("[hang:"):
        return bool(minden_hang or not ZAJ_HANG.search(sor))
    return False  # start line, empty, other


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=STATE_ALAP)
    ap.add_argument("--nincs-kilepes", "--no-exit", action="store_true")
    ap.add_argument("--minden-hang", "--all-sounds", action="store_true")
    ap.add_argument("--stt-zaj-be", "--stt-noise-on", action="store_true",
                    help="By default the transcriber's wordless [bracketed] noise lines (e.g. [ismeretlen hang] [kopogás]) are NOT passed through: in time they duplicate the sound labeller and are coarser in content; this switch turns them back on")
    ap.add_argument("--min-pont", "--min-score", type=float, default=0.6,
                    help="a [hang: X p] label passes only if p >= min-pont (filters on the confidence score ONLY, never on content); 0 = everything (default tuned from 0.7 to 0.6)")
    ap.add_argument("--nincs-emlek", action="store_true", help="disable the [EMLEK] memory lines")
    ap.add_argument("--csak-beszed", "--speech-only", action="store_true", help="economy mode: ONLY speech passes from the ear, every sound/noise label is dropped; sentinel + watcher stay")
    a = ap.parse_args()

    st = load_state(a.state)
    os.makedirs(SZ_MAPPA, exist_ok=True)
    if not os.path.exists(SZ_LOG):
        open(SZ_LOG, "a", encoding="utf-8").close()
    # local watcher log: if it got shorter, from 0
    try:
        if os.path.getsize(SZ_LOG) < st["sz"]:
            st["sz"] = 0
    except Exception:
        st["sz"] = 0

    # Pi-side tails from the offset; the remote shell checks the file size (reboot/truncation -> 0)
    remote = (
        # self-cleanup: the previous reader's orphaned tails (older than 5 s) — our own shell is fresh, so it is not hit
        "ps -eo pid,etimes,args | awk '/[t]ail -c \\+/ && $2>5 {print $1}' | xargs -r kill -TERM 2>/dev/null; "
        "f=%s; o=%d; s=$(stat -c %%s $f 2>/dev/null || echo 0); [ $o -gt $s ] && o=0; "
        "g=%s; p=%d; t=$(stat -c %%s $g 2>/dev/null || echo 0); [ $p -gt $t ] && p=0; "
        # The saved offset is valid only within the SAME Pi boot. After a new boot
        # the old offset points into a different file (seen once: an old conversation was replayed) ->
        # we start from the END of the log; unknown (old state) -> the old behaviour
        "b=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null); ob='%s'; k=0; "
        "if [ -n \"$ob\" ] && [ \"$ob\" != \"$b\" ]; then k=$s; o=$s; p=$t; fi; "
        "echo \"OFFSET ful=$o orsz=$p boot=$b kihagyva=$k uj=$([ -n \"$ob\" ] && [ \"$ob\" != \"$b\" ] && echo 1 || echo 0)\"; "
        "tail -c +$((o+1)) -F $f | sed -u 's/^/[FÜL] /' & "
        "tail -c +$((p+1)) -F $g | sed -u 's/^/[ŐRSZEM] /' & wait"
    ) % (FUL, st["ful"], ORSZ, st["orsz"], re.sub(r"[^0-9a-f-]", "", st.get("boot", "")))
    ssh = subprocess.Popen(
        ["ssh", "-i", KULCS, "-o", "ServerAliveInterval=15", "-o", "BatchMode=yes",
         "-o", "ConnectTimeout=10", PI, remote],
        stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)

    lock = threading.Lock()
    stop = threading.Event()

    def kiir(cimke, nyers):
        """nyers = the source's own line (without prefix); print + offset + meaningful-or-not decision."""
        sor = nyers.rstrip("\r\n")
        with lock:
            hossz = len(nyers.encode("utf-8"))
            if cimke == "FUL":
                st["ful"] += hossz
                if sor.startswith("[hang:") and ZAJ_HANG.search(sor) and not a.minden_hang:
                    save_state(a.state, st)
                    return
                # Confidence-score filter, independent of content (the rule forbids selecting by CONTENT, not this)
                _m = re.match(r"\[hang: .* ([0-9.]+)\]\s*$", sor)
                if _m and float(_m.group(1)) < a.min_pont:
                    save_state(a.state, st)
                    return
                # Wordless STT noise lines are dropped by default: the YAMNet label is within ±2 lines anyway,
                # and Scribe kept saying just "kopogás" (knocking)
                _b = beszed_resz(sor)
                if not a.stt_zaj_be and _b is not None and not re.sub(r"\[[^\]]*\]", " ", _b).strip():
                    save_state(a.state, st)
                    return
                # economy mode: only speech passes
                if a.csak_beszed and (sor.startswith("[hang:") or re.fullmatch(r"(\[[^\]]+\]\s*)+", sor)):
                    save_state(a.state, st)
                    return
                print(f"[FÜL] {sor}", flush=True)
                if _b is not None:
                    # strip the [labels] (throat clearing, knocking…); if no text remains, no memory lookup
                    _t = re.sub(r"\[[^\]]*\]", " ", _b).strip()
                    if len(_t) >= 4:
                        emlek_kiir(_t)
            elif cimke == "ORSZEM":
                st["orsz"] += hossz
                print(f"[ŐRSZEM] {sor}", flush=True)
            else:
                st["sz"] += hossz
                print(sor, flush=True)
            save_state(a.state, st)
            if not a.nincs_kilepes and erdemi(cimke, sor, a.minden_hang):
                stop.set()

    def pi_olvaso():
        try:
            for raw in iter(ssh.stdout.readline, b""):
                line = raw.decode("utf-8", "replace")
                if line.startswith("OFFSET "):
                    # the remote shell confirmed where it started (0 after a reboot)
                    m = re.match(r"OFFSET ful=(\d+) orsz=(\d+)(?: boot=([0-9a-f-]*) kihagyva=(-?\d+) uj=(\d))?", line)
                    if m:
                        with lock:
                            st["ful"], st["orsz"] = int(m.group(1)), int(m.group(2))
                            if m.group(3):
                                st["boot"] = m.group(3)
                            save_state(a.state, st)
                        if m.group(5) == "1":
                            print(f"[READER] starting after a new Pi boot: from the END of the ear log (skipped {m.group(4)} bytes — if needed, Pi: /tmp/fulelo_events.log)", flush=True)
                    continue
                if line.startswith("[FÜL] "):
                    kiir("FUL", line[6:])
                elif line.startswith("[ŐRSZEM] "):
                    kiir("ORSZEM", line[9:])
                else:
                    print(line.rstrip("\r\n"), flush=True)
                if stop.is_set():
                    break
        finally:
            stop.set()
            print("ROBOT-READER: ssh ended (robot off / wifi?)", flush=True) if ssh.poll() is not None else None

    def sz_olvaso():
        pos = st["sz"]
        with open(SZ_LOG, "rb") as f:
            f.seek(pos)
            while not stop.is_set():
                raw = f.readline()
                if not raw:
                    time.sleep(1.0)
                    continue
                kiir("SZ", raw.decode("utf-8", "replace"))

    t1 = threading.Thread(target=pi_olvaso, daemon=True)
    t2 = threading.Thread(target=sz_olvaso, daemon=True)
    t1.start(); t2.start()
    try:
        while not stop.is_set():
            time.sleep(0.5)
            if ssh.poll() is not None and not stop.is_set():
                stop.set()
                print("ROBOT-READER: ssh ended (robot off / wifi?)", flush=True)
                save_state(a.state, st)
                sys.exit(2)
    finally:
        try:
            ssh.terminate()
        except Exception:
            pass
        save_state(a.state, st)


if __name__ == "__main__":
    main()
