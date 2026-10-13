#!/usr/bin/env python3
"""speaker_daemon.py — persistent speaker-identification server.

WHY: computing an embedding is fast (~0.1-1 s on CPU), but loading torch + the model is
slow (~5-10 s). Restarting identify.py for every sentence in the listen loop would be far
too slow. This daemon runs in its own venv (.spk-venv), loads the model once, accepts WAV
paths on a UNIX socket (/tmp/speaker.sock) and answers with "name|name|score|[label]".
(Alternative: load the VoiceEncoder in-process in the listen loop — only possible if
resemblyzer is installed in the loop's own Python; see LISTEN_LOOP_PATCH.md.)

The loop ONLY connects to the socket; if the daemon is not running -> no label, and
listening carries on.

Start (with the venv's python):
    ~/picarx-mcp/speaker_id/.spk-venv/bin/python speaker_daemon.py
"""
import os
import sys
import socket
import json
import time
import shutil
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import speaker_id_lib as spk

SOCK_PATH = "/tmp/speaker.sock"
SCORE_LOG = "/tmp/speaker_scores.jsonl"   # log of every identification (for threshold tuning)
RING_DIR = "/tmp/spk_ring"                # archive of the last N WAVs (re-check suspicious cases)
RING_KEEP = 20


def ring_archive(wav):
    """Copy the examined WAV under a timestamped name; delete the oldest ones (spare tmpfs)."""
    try:
        os.makedirs(RING_DIR, exist_ok=True)
        dst = os.path.join(RING_DIR, f"{time.strftime('%H%M%S')}_{os.path.basename(wav)}")
        shutil.copy(wav, dst)
        old = sorted(glob.glob(os.path.join(RING_DIR, "*.wav")), key=os.path.getmtime)
        for f in old[:-RING_KEEP]:
            os.unlink(f)
        return dst
    except Exception:
        return ""


def main():
    with open(os.path.join(HERE, "speaker_config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    profiles_path = os.path.join(HERE, cfg["profiles_path"])
    thresholds = cfg.get("thresholds", {"name": 0.75, "maybe": 0.5})

    print("[daemon] loading VoiceEncoder...", flush=True)
    spk.load_encoder()
    print("[daemon] ready. Socket:", SOCK_PATH, flush=True)

    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o666)
    srv.listen(4)

    while True:
        conn, _ = srv.accept()
        try:
            wav = conn.recv(4096).decode("utf-8", "replace").strip()
            if not wav:
                # "[ismeretlen hang]" = "unknown voice"; parsed by the listen loop, keep as is
                conn.sendall(b"||0.0|[ismeretlen hang]\n")
                continue
            # Profiles are re-read on EVERY request -> an enroll.py update takes effect
            # immediately (no daemon restart needed).
            profiles = spk.load_profiles(profiles_path)
            try:
                emb = spk.embed_wav(wav)
                label, name, score = spk.identify(emb, profiles, thresholds)
            except Exception as e:
                print("[daemon] embed error:", e, flush=True)
                label, name, score = "[ismeretlen hang]", "", 0.0
            # Reply format: "name|name|score|[label]" (the listen loop takes the last field)
            conn.sendall(f"{name or ''}|{name or ''}|{score:.4f}|{label}\n".encode("utf-8"))
            # Score log + WAV archive (threshold tuning / re-checking suspicious cases)
            try:
                arch = ring_archive(wav)
                with open(SCORE_LOG, "a", encoding="utf-8") as lf:
                    lf.write(json.dumps({"t": time.strftime("%H:%M:%S"), "wav": wav,
                                         "name": name, "score": round(score, 4),
                                         "label": label, "arch": arch},
                                        ensure_ascii=False) + "\n")
            except Exception as e:
                print("[daemon] log error:", e, flush=True)
        except Exception as e:
            print("[daemon] conn error:", e, flush=True)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
