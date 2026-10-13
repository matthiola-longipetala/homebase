#!/usr/bin/env python3
"""listen_to_a_song.py — a file-based "wave ear" for a Claude without ears.

The resident Claude cannot hear. What it can do is MEASURE a sound file and read
the measurement as a story: where the music gets loud, where it goes quiet, which
pitches carry a section, where the structure changes. Combined with a timestamped
transcript from a speech/audio-understanding model (the package's video-mcp can
produce one) and, if you have the robot, the robot microphone's own "sound picture"
(picarx-mcp/hangkep.py), the three views line up on one timeline — and that is as
close to listening as a text model gets. See README.md in this folder.

This is a clean rewrite of an experiment script from 2026-08; the original used
numpy + PIL only. This version needs numpy and soundfile (pip install numpy
soundfile); ffmpeg is used only to convert mp3/other formats to wav.

Usage:
    python listen_to_a_song.py song.wav              # timeline table to stdout
    python listen_to_a_song.py song.mp3 --window 5   # 5-second windows (default 10)
    python listen_to_a_song.py song.wav --json out.json
    python listen_to_a_song.py song.wav --png picture.png   # needs matplotlib

Output per window: start time, loudness (dBFS, RMS), peak-to-RMS crest (how
"spiky" it is), the three strongest pitches as note names with their frequency,
and a marker on windows where loudness jumps by more than --jump dB (section
boundary candidates). Nothing is sent anywhere; everything is local.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

try:
    import soundfile as sf
except ImportError:  # pragma: no cover
    print("missing dependency: pip install soundfile", file=sys.stderr)
    raise

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def hz_to_note(hz):
    """440 Hz → 'A4'. Returns '-' for silence / nonsense."""
    if hz <= 0 or not math.isfinite(hz):
        return "-"
    midi = 69 + 12 * math.log2(hz / 440.0)
    n = int(round(midi))
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def load_audio(path):
    """Returns (mono float32 array, sample_rate). Converts non-wav via ffmpeg."""
    ext = os.path.splitext(path)[1].lower()
    tmp = None
    if ext not in (".wav", ".flac", ".ogg"):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            sys.exit("ffmpeg not found; convert the file to wav first")
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", path, "-ac", "1", tmp.name], check=True)
        path = tmp.name
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    if tmp:
        os.unlink(tmp.name)
    return data.mean(axis=1), sr


def strongest_pitches(frame, sr, n=3, fmin=40.0, fmax=5000.0):
    """The n strongest spectral peaks of one frame as [(hz, note, rel_db)]."""
    if len(frame) < 1024:
        return []
    win = np.hanning(len(frame))
    spec = np.abs(np.fft.rfft(frame * win))
    freqs = np.fft.rfftfreq(len(frame), 1.0 / sr)
    mask = (freqs >= fmin) & (freqs <= fmax)
    spec, freqs = spec[mask], freqs[mask]
    if spec.max() <= 0:
        return []
    ref = spec.max()
    # simple local-maximum peak picking, then the top n by magnitude
    peaks = [i for i in range(1, len(spec) - 1) if spec[i] > spec[i - 1] and spec[i] >= spec[i + 1]]
    peaks.sort(key=lambda i: spec[i], reverse=True)
    out = []
    for i in peaks:
        hz = float(freqs[i])
        if any(abs(hz - h) < 0.03 * h for h, _, _ in out):   # skip near-duplicates
            continue
        out.append((hz, hz_to_note(hz), 20 * math.log10(spec[i] / ref)))
        if len(out) >= n:
            break
    return out


def analyse(audio, sr, window_s=10.0, jump_db=6.0):
    rows = []
    step = int(window_s * sr)
    prev_db = None
    for start in range(0, len(audio), step):
        frame = audio[start:start + step]
        if len(frame) < sr // 2:          # ignore a trailing half-second
            break
        rms = float(np.sqrt(np.mean(frame ** 2)))
        peak = float(np.max(np.abs(frame)))
        db = 20 * math.log10(rms) if rms > 0 else -120.0
        crest = 20 * math.log10(peak / rms) if rms > 0 and peak > 0 else 0.0
        pitches = strongest_pitches(frame[: min(len(frame), sr * 4)], sr)   # first 4 s of the window
        jump = prev_db is not None and abs(db - prev_db) >= jump_db
        rows.append({
            "t": start / sr,
            "loudness_db": round(db, 1),
            "crest_db": round(crest, 1),
            "pitches": [{"hz": round(h, 1), "note": n, "rel_db": round(r, 1)} for h, n, r in pitches],
            "boundary": bool(jump),
            "delta_db": None if prev_db is None else round(db - prev_db, 1),
        })
        prev_db = db
    return rows


def fmt_time(t):
    return f"{int(t) // 60}:{int(t) % 60:02d}"


def print_table(rows):
    print(f"{'time':>6} {'dBFS':>6} {'crest':>6}  {'pitches (note hz rel-dB)':<44} boundary")
    for r in rows:
        p = "  ".join(f"{x['note']}({x['hz']:.0f},{x['rel_db']:+.0f})" for x in r["pitches"]) or "-"
        mark = f"<< {r['delta_db']:+.1f} dB" if r["boundary"] else ""
        print(f"{fmt_time(r['t']):>6} {r['loudness_db']:>6.1f} {r['crest_db']:>6.1f}  {p:<44} {mark}")
    loud = max(rows, key=lambda r: r["loudness_db"])
    quiet = min(rows, key=lambda r: r["loudness_db"])
    print(f"\nloudest window: {fmt_time(loud['t'])} ({loud['loudness_db']} dBFS); "
          f"quietest: {fmt_time(quiet['t'])} ({quiet['loudness_db']} dBFS); "
          f"section-boundary candidates: {sum(r['boundary'] for r in rows)}")


def save_png(audio, sr, rows, path):
    """A 'sound picture': loudness curve over the waveform envelope."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t = np.arange(len(audio)) / sr
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(t[::max(1, len(audio) // 20000)], audio[::max(1, len(audio) // 20000)], lw=0.3, color="#888")
    ax2 = ax.twinx()
    ax2.plot([r["t"] for r in rows], [r["loudness_db"] for r in rows], color="#d2691e", lw=2)
    for r in rows:
        if r["boundary"]:
            ax.axvline(r["t"], color="#c33", lw=0.8, ls="--")
    ax.set_xlabel("seconds"); ax.set_ylabel("waveform"); ax2.set_ylabel("loudness dBFS")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("--window", type=float, default=10.0, help="window length in seconds (default 10)")
    ap.add_argument("--jump", type=float, default=6.0, help="loudness jump (dB) that marks a boundary")
    ap.add_argument("--json", help="write the rows to this JSON file")
    ap.add_argument("--png", help="write a loudness/waveform picture (needs matplotlib)")
    a = ap.parse_args(argv)
    audio, sr = load_audio(a.file)
    rows = analyse(audio, sr, a.window, a.jump)
    print(f"{os.path.basename(a.file)}: {fmt_time(len(audio) / sr)} long, {sr} Hz, {len(rows)} windows of {a.window:g} s\n")
    print_table(rows)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1)
        print(f"json: {a.json}")
    if a.png:
        save_png(audio, sr, rows, a.png)
        print(f"png: {a.png}")


if __name__ == "__main__":
    main()
