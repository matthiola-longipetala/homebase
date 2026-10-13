#!/usr/bin/env python3
"""robot_hear.py — the robot LISTENS to one sentence and turns it into text.

Recording from the ReSpeaker (card 4, 16 kHz mono) -> ElevenLabs Scribe STT -> text.
The key comes from voice_config.json; it is passed to curl on STDIN (config) so that it
does NOT show up in the process list (ps).
Usage: python3 robot_hear.py [seconds]   (default 6 s)
"""
import os
import sys, json, subprocess, re

CFG = os.path.expanduser("~/picarx-mcp/voice_config.json")
WAV = "/tmp/robot_hear.wav"
REC_DEV = "plughw:4,0"   # ReSpeaker (ear)

cfg = json.load(open(CFG))
KEY = cfg["elevenlabs_api_key"]
STT = cfg.get("stt", {})
MODEL = STT.get("model_id", "scribe_v2")
LANG = STT.get("language_code", "en")
dur = int(sys.argv[1]) if len(sys.argv) > 1 else 6

# 1) record from the ear (mono 16k)
print(f"[recording {dur}s from the ReSpeaker...]", flush=True)
subprocess.run(["arecord", "-D", REC_DEV, "-d", str(dur),
                "-f", "S16_LE", "-r", "16000", "-c", "1", WAV],
               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# diagnostics: volume
try:
    import wave, numpy as np
    _w = wave.open(WAV, "rb")
    _d = np.frombuffer(_w.readframes(_w.getnframes()), dtype=np.int16).astype(float)
    print(f"[volume] RMS={int(np.sqrt(np.mean(_d**2)))} peak={int(np.abs(_d).max())} ({100*np.abs(_d).max()/32768:.0f}%)", flush=True)
except Exception as _e:
    print("[volume] cannot be measured:", _e, flush=True)

# 2) ElevenLabs Scribe STT (the key on STDIN, not in argv)
curl_cfg = f'header = "xi-api-key: {KEY}"\n'
proc = subprocess.run(
    ["curl", "-s", "-X", "POST", "https://api.elevenlabs.io/v1/speech-to-text",
     "-K", "-",
     "-F", f"model_id={MODEL}",
     "-F", f"language_code={LANG}",
     "-F", f"file=@{WAV};type=audio/wav"],
    input=curl_cfg, capture_output=True, text=True)

print(f"[scribe raw] {proc.stdout[:300]!r}", flush=True)
try:
    data = json.loads(proc.stdout)
except Exception:
    print("STT ERROR: non-JSON response:", proc.stdout[:300]); sys.exit(1)

text = (data.get("text") or "").strip()
# When listening on request EVERY sound tag stays ([taps], [zene], [dorombolás]... = applause,
# music, purring) — scribe_v2 tags accurately, and here there is no "wake-up cost":
if text:
    print("TEXT:", text)
else:
    print("TEXT: (no speech recognised)")
