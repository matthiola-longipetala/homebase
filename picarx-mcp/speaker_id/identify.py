#!/usr/bin/env python3
"""identify.py — one WAV -> (name, confidence, label). Testable from the CLI.

Usage:
    python3 identify.py /tmp/utt.wav
    python3 identify.py /tmp/utt.wav --json     # machine-readable: {"label","name","score"}
    python3 identify.py /tmp/utt.wav --pipe     # one line: speaker|<name>|<score>|<label>

Output (human-readable mode):
    [Alice] (0.83)
or
    speaker|Bob|0.79|[Bob]          # --pipe
"""
import sys
import os
import argparse
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import speaker_id_lib as spk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--pipe", action="store_true", help="one line: speaker|<name>|<score>|<label>")
    args = ap.parse_args()

    with open(os.path.join(HERE, "speaker_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    profiles_path = os.path.join(HERE, cfg["profiles_path"])
    thresholds = cfg.get("thresholds", {"name": 0.75, "maybe": 0.5})

    try:
        emb = spk.embed_wav(args.wav)
    except Exception as e:
        # Never let a caller crash because of this — a non-zero exit code means "no label".
        print(f"identify error: {e}", file=sys.stderr)
        sys.exit(2)

    profiles = spk.load_profiles(profiles_path)
    label, name, score = spk.identify(emb, profiles, thresholds)

    if args.json:
        print(json.dumps({"label": label, "name": name, "score": round(score, 4)}, ensure_ascii=False))
    elif args.pipe:
        print(f"speaker|{name or ''}|{score:.4f}|{label}")
    else:
        print(f"{label} ({score:.2f})")


if __name__ == "__main__":
    main()
