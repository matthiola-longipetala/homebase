#!/usr/bin/env python3
"""speaker_id_lib.py — shared core for local speaker identification (Resemblyzer d-vectors).

The voiceprints (256-dim embeddings) stay LOCAL on the Pi (profiles.json) and are never
sent to any cloud service.

Model choice: Resemblyzer (GE2E d-vector, ~1.4M params, CPU-friendly) instead of
ECAPA-TDNN. Reason: telling a few household voices apart from "unknown" is an easy
separation where Resemblyzer is accurate enough; enroll = mean vector, identify = cosine,
a trivial API; no HuggingFace Hub download on the Pi. Details: README.md.

Dependencies: resemblyzer, torch, librosa, numpy, scipy, webrtcvad (see install.sh).
"""
import os
import json
import datetime
import numpy as np

# Importing Resemblyzer is slow (torch) — load it only when an embedding is actually needed.
_ENCODER = None


def load_encoder():
    """Load the pretrained VoiceEncoder (CPU) once. Idempotent."""
    global _ENCODER
    if _ENCODER is None:
        from resemblyzer import VoiceEncoder
        _ENCODER = VoiceEncoder("cpu")
    return _ENCODER


def embed_wav(path):
    """L2-normalised 256-dim embedding from one WAV file.

    The robot's WAVs are already 16 kHz mono S16_LE (arecord, like robot_hear), so
    preprocess_wav only does VAD trimming + normalisation. Raises on any error (the caller
    decides what to do; the listen loop swallows it SILENTLY)."""
    from resemblyzer import preprocess_wav
    wav = preprocess_wav(path)
    emb = load_encoder().embed_utterance(wav)
    return _l2(np.asarray(emb, dtype=np.float32))


def embed_wavs(paths):
    """Several WAVs -> averaged, L2-normalised embedding (for enrolment)."""
    embs = [embed_wav(p) for p in paths]
    return _l2(np.mean(np.stack(embs), axis=0))


def _l2(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


# ---- profiles.json handling ----

def load_profiles(path):
    if not os.path.exists(path):
        return {"speakers": {}, "meta": {"created": _today()}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_profiles(profiles, path):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)   # atomic (survives a reboot/brownout mid-write)


def enroll(profiles, name, embedding, n_samples):
    """New or updated speaker profile. If it already exists, the new embedding is averaged
    with the old one, WEIGHTED by the old sample count, so a profile can be refined over
    several sessions."""
    sp = profiles.setdefault("speakers", {})
    if name in sp:
        old = np.asarray(sp[name]["vector"], dtype=np.float32)
        old_n = int(sp[name].get("samples", 1))
        merged = _l2((old * old_n + embedding * n_samples) / (old_n + n_samples))
        sp[name] = {"vector": merged.tolist(), "samples": old_n + n_samples, "updated": _today()}
    else:
        sp[name] = {"vector": _l2(embedding).tolist(), "samples": n_samples, "updated": _today()}
    return profiles


def identify(embedding, profiles, thresholds):
    """(label, best_name, best_score). Label policy (same style as the sound labeller):
        score >= name  -> "[Alice]"
        score >= maybe -> "[valoszinuleg Alice]"   (Hungarian: "probably Alice")
        below          -> "[ismeretlen hang]"      (Hungarian: "unknown voice")
    With no enrolled profiles it is always "[ismeretlen hang]".
    The label strings are parsed by ../robot_listen_loop.py and ../robot_olvaso.py —
    keep them exactly as they are."""
    sp = (profiles or {}).get("speakers", {})
    if not sp:
        return "[ismeretlen hang]", None, 0.0
    best_name, best_score = None, -1.0
    for name, rec in sp.items():
        vec = np.asarray(rec["vector"], dtype=np.float32)
        score = float(np.dot(_l2(embedding), _l2(vec)))   # L2-normalised -> cosine
        if score > best_score:
            best_name, best_score = name, score
    name_thr = float(thresholds.get("name", 0.75))
    maybe_thr = float(thresholds.get("maybe", 0.5))
    if best_score >= name_thr:
        label = f"[{best_name}]"
    elif best_score >= maybe_thr:
        label = f"[valoszinuleg {best_name}]"   # "probably <name>" — parsed elsewhere, keep
    else:
        label = "[ismeretlen hang]"              # "unknown voice" — parsed elsewhere, keep
    return label, best_name, best_score


def _today():
    return datetime.date.today().isoformat()
