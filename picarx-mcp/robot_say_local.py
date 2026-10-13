#!/usr/bin/env python3
"""robot_say_local.py — LOCAL, NETWORK-INDEPENDENT voice: espeak -> HifiBerry.

WHY IT EXISTS: once the monthly ElevenLabs quota ran out (0 characters left) and the
robot WENT MUTE. The voice chain uses the word "fallback" three times (realtime -> REST,
sentence-by-sentence -> in one go, model -> model), so it LOOKS ROBUST -- but all three
fallbacks live inside the SAME EXTERNAL service. A layered fallback is worth nothing if
every layer depends on the same single thing.

This script is the END of the chain: when ElevenLabs gives no audio for any reason (quota
exhausted, network, 401/429/5xx), the robot STILL speaks -- with a machine voice. It joins
two pieces ALREADY PROVEN on the Pi:
  - espeak -v $ROBOT_ESPEAK_VOICE (default en; also used by leallit.py, perem_menet.py, perem_demo.py)
  - aplay -D plughw:CARD=sndrpihifiberry  + ffmpeg gain/limiter  (robot_say.py's playback branch)

DEGRADATION MUST NOT BE SILENT. When this script speaks, it says so loudly on stdout AND
leaves a /tmp/robot_voice_fallback.flag marker with a timestamp -- otherwise a later
agent instance would believe its own voice had spoken. Same principle as the silent-ceiling
guard of capture(mode="dark"): THE LIMIT MUST SAY THAT IT WAS A LIMIT.

Usage:
    python3 robot_say_local.py "text to say"
    python3 robot_say_local.py --file /tmp/text.txt
    python3 robot_say_local.py --dry "test"      # only prints the command chain, does not speak
Environment:
    SAY_LOCAL_GAIN_DB (default 6)  · SAY_LOCAL_SPEED (default 160) · SAY_LOCAL_AMP (default 190)

Note: the espeak voice comes from ROBOT_ESPEAK_VOICE (default "en"; e.g. "hu" for Hungarian).
"""
import os
import shutil
import subprocess
import sys
import time

TXT = "/tmp/robot_say_local.txt"
RAW = "/tmp/robot_say_local_raw.wav"
WAV = "/tmp/robot_say_local.wav"
LOCK = "/tmp/robot_speaking.lock"
FLAG = "/tmp/robot_voice_fallback.flag"
ALSA_DEV = "plughw:CARD=sndrpihifiberry,DEV=0"

GAIN_DB = int(os.environ.get("SAY_LOCAL_GAIN_DB", 6))   # espeak is already loud; the limiter protects
SPEED = int(os.environ.get("SAY_LOCAL_SPEED", 160))     # 175 is the default; slower is more intelligible for Hungarian
AMP = int(os.environ.get("SAY_LOCAL_AMP", 190))         # 0-200


def espeak_bin():
    """The Pi has `espeak` (proven); espeak-ng is listed only as a precaution."""
    for name in ("espeak", "espeak-ng"):
        p = shutil.which(name)
        if p:
            return p
    return None


def beszelj(text, dry=False):
    """True if it actually spoke. Never raises -- this is the END of the chain."""
    text = (text or "").strip()
    if not text:
        print("  [local voice: empty text, nothing to say]", flush=True)
        return False

    binary = espeak_bin()
    if binary is None:
        if not dry:
            print("  ERROR: neither espeak nor espeak-ng is on the PATH -> the robot STAYS MUTE.", flush=True)
            return False
        # In DRY mode the command chain should be visible even if the machine (e.g. Windows)
        # has no espeak -- otherwise the script could not be tried at all without the Pi.
        print("  [DRY] warning: espeak is NOT on this machine, the name is only a placeholder", flush=True)
        binary = "espeak"

    szint = [binary, "-v", os.environ.get("ROBOT_ESPEAK_VOICE", "en"), "-s", str(SPEED), "-a", str(AMP), "-f", TXT, "-w", RAW]
    konv = ["ffmpeg", "-y", "-loglevel", "error", "-i", RAW,
            # adelay: the amplifier's power-on delay clips the first half second
            "-af", f"adelay=600|600,volume={GAIN_DB}dB,alimiter=limit=0.97",
            "-ar", "44100", "-ac", "2", WAV]
    jatszd = ["aplay", "-D", ALSA_DEV, WAV]

    if dry:
        # The DRY branch writes NOTHING to disk (so it can be tried on any machine without the Pi).
        print(f"  [DRY] text ({len(text)} chars): {text[:80]!r} -> {TXT}")
        print("  [DRY] " + " ".join(szint))
        print("  [DRY] " + " ".join(konv))
        print("  [DRY] " + " ".join(jatszd))
        print(f"  [DRY] lock: {LOCK} · marker: {FLAG}")
        return False

    try:
        with open(TXT, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as e:
        print(f"  ERROR: cannot write the text ({TXT}): {e}", flush=True)
        return False

    try:
        r = subprocess.run(szint, capture_output=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(RAW) or os.path.getsize(RAW) < 1024:
            print(f"  ERROR: espeak rc={r.returncode}, stderr={r.stderr[:200]!r}", flush=True)
            return False
        subprocess.run(konv, check=True, timeout=60)
    except Exception as e:
        print(f"  ERROR while producing the local voice: {e}", flush=True)
        return False

    try:
        from robot_hat.utils import enable_speaker
        enable_speaker()
    except Exception as e:
        print("  speaker enable warn:", e, flush=True)

    # half-duplex: the listener must NOT hear my own voice
    try:
        open(LOCK, "w").close()
    except OSError:
        pass
    try:
        subprocess.run(jatszd, timeout=300)
        time.sleep(0.5)          # let the tail of my own voice die down WHILE locked
    except Exception as e:
        print(f"  ERROR during playback: {e}", flush=True)
        return False
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass

    # DEGRADATION IS NOT SILENT: leave a marker that I spoke with the machine voice.
    try:
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + " | espeak-fallback | " + text[:120] + "\n")
    except OSError:
        pass
    print("  ⚠️ MACHINE VOICE (espeak) spoke, NOT my ElevenLabs voice "
          "-- probably the monthly quota ran out or there is no network. "
          f"Marker: {FLAG}", flush=True)
    return True


def main():
    args = sys.argv[1:]
    dry = False
    if args and args[0] == "--dry":
        dry = True
        args = args[1:]
    if len(args) > 1 and args[0] == "--file":
        try:
            text = open(args[1], encoding="utf-8").read()
        except Exception as e:
            print(f"  ERROR: cannot read the text file: {e}", flush=True)
            sys.exit(1)
    else:
        text = args[0] if args else "Hi. I am speaking with my machine voice because my normal voice is not available right now."
    sys.exit(0 if beszelj(text, dry=dry) else 1)


if __name__ == "__main__":
    main()
