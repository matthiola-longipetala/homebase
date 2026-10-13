#!/usr/bin/env python3
"""enroll.py — speaker ENROLMENT: name + N x ~10 s recordings from the ReSpeaker -> profiles.json.

Usage:
    python3 enroll.py Alice                 # 3 x 10 s recordings from the mic (per config)
    python3 enroll.py Bob --samples 4       # 4 samples
    python3 enroll.py Alice --wav a.wav b.wav c.wav   # from existing WAVs (test/synthetic)

Between recordings the script waits for ENTER, so the speaker has time to say a different
sentence each time (better generalisation). The voiceprint stays LOCAL.
"""
import sys
import os
import argparse
import subprocess
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import speaker_id_lib as spk


def load_cfg():
    with open(os.path.join(HERE, "speaker_config.json"), encoding="utf-8") as f:
        return json.load(f)


def record(dev, sr, seconds, out):
    print(f"  [recording {seconds}s — keep talking...]", flush=True)
    subprocess.run(["arecord", "-D", dev, "-d", str(seconds),
                    "-f", "S16_LE", "-r", str(sr), "-c", "1", out],
                   check=True)
    print(f"  [done: {out}]", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="Speaker name (e.g. Alice, Bob)")
    ap.add_argument("--samples", type=int, default=None)
    ap.add_argument("--wav", nargs="+", default=None, help="Existing WAV files (instead of recording)")
    args = ap.parse_args()

    cfg = load_cfg()
    profiles_path = os.path.join(HERE, cfg["profiles_path"])
    n = args.samples or int(cfg.get("enroll_samples", 3))

    if args.wav:
        wavs = args.wav
        print(f"[{args.name}] enrolling from {len(wavs)} existing WAV(s)")
    else:
        wavs = []
        print(f"[{args.name}] enrolling — {n} recordings from the ReSpeaker")
        for i in range(n):
            if i > 0:
                try:
                    input(f"  ENTER for recording {i+1} (say something different from last time)...")
                except EOFError:
                    pass
            wav = f"/tmp/enroll_{args.name}_{i}.wav"
            record(cfg["rec_device"], cfg["sample_rate"], cfg["enroll_seconds"], wav)
            wavs.append(wav)

    print("[computing embedding... (first call is slow: loading torch)]", flush=True)
    emb = spk.embed_wavs(wavs)

    profiles = spk.load_profiles(profiles_path)
    profiles = spk.enroll(profiles, args.name, emb, len(wavs))
    spk.save_profiles(profiles, profiles_path)

    rec = profiles["speakers"][args.name]
    print(f"[DONE] {args.name} profile saved: {rec['samples']} samples in total, "
          f"updated {rec['updated']} -> {profiles_path}")
    print("[enrolled speakers]:", ", ".join(profiles["speakers"].keys()))


if __name__ == "__main__":
    main()
