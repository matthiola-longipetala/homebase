#!/usr/bin/env python3
"""stt_ab_test.py — Scribe v1 vs v2 A/B test on THE SAME recording.

Records once from the ReSpeaker (like robot_hear.py), then sends the WAV to BOTH
models (scribe_v1, scribe_v2) and prints the two transcripts + response times.
So the difference depends only on the model.

Usage: python3 stt_ab_test.py [seconds]   (default 6 s)
       python3 stt_ab_test.py --wav /tmp/x.wav   (re-send an existing recording)
"""
import os
import sys, json, subprocess, re, time

CFG = os.path.expanduser("~/picarx-mcp/voice_config.json")
WAV = "/tmp/stt_ab.wav"
REC_DEV = "plughw:4,0"
MODELS = ["scribe_v1", "scribe_v2"]

cfg = json.load(open(CFG))
KEY = cfg["elevenlabs_api_key"]
LANG = cfg.get("stt", {}).get("language_code", "en")

if len(sys.argv) > 2 and sys.argv[1] == "--wav":
    WAV = sys.argv[2]
    print(f"[existing recording: {WAV}]", flush=True)
else:
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    print(f"[recording {dur}s from the ReSpeaker...]", flush=True)
    subprocess.run(["arecord", "-D", REC_DEV, "-d", str(dur),
                    "-f", "S16_LE", "-r", "16000", "-c", "1", WAV],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

try:
    import wave, numpy as np
    _w = wave.open(WAV, "rb")
    _d = np.frombuffer(_w.readframes(_w.getnframes()), dtype=np.int16).astype(float)
    print(f"[volume] RMS={int(np.sqrt(np.mean(_d**2)))} peak={100*np.abs(_d).max()/32768:.0f}%", flush=True)
except Exception as _e:
    print("[volume] cannot be measured:", _e, flush=True)

curl_cfg = f'header = "xi-api-key: {KEY}"\n'
for model in MODELS:
    t0 = time.time()
    proc = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-X", "POST",
         "https://api.elevenlabs.io/v1/speech-to-text",
         "-K", "-",
         "-F", f"model_id={model}",
         "-F", f"language_code={LANG}",
         "-F", f"file=@{WAV};type=audio/wav"],
        input=curl_cfg, capture_output=True, text=True)
    dt = time.time() - t0
    try:
        data = json.loads(proc.stdout)
        text = re.sub(r"\([^)]*\)", "", (data.get("text") or "")).strip()
        if not text:
            text = f"(empty/error: {proc.stdout[:120]})"
    except Exception:
        text = f"(non-JSON: {proc.stdout[:120]})"
    print(f"{model} [{dt:.1f}s]: {text}", flush=True)
