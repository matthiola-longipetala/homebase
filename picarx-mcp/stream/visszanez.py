"""visszanez.py — replay the rolling buffer (the last few minutes of video).

This is what an on-demand look could NEVER do: see what happened BEFORE an
event. stream_supervisor continuously fills the /tmp/rbuf/ ring buffer (2 s
.ts segments in a ring, see SEG_WRAP in stream_lib.py), via H.264 copy (cheap).

Usage:
  python3 stream/visszanez.py 10                # last 10 s -> /tmp/visszanez.mp4
  python3 stream/visszanez.py 10 --frames 6     # last 10 s -> 6 evenly spaced JPGs (/tmp/vn_XXX.jpg)
  python3 stream/visszanez.py 10 --out /tmp/x.mp4

Segment names wrap around (seg_000, seg_001, ...), so CHRONOLOGY comes from the
mtime, not the file name.
"""
import glob
import os
import subprocess
import sys
import time

RBUF_DIR = "/tmp/rbuf"
SEG_SECONDS = 2


def _recent_segments(seconds):
    """Segments covering the most recent 'seconds' seconds, in chronological order."""
    segs = glob.glob(os.path.join(RBUF_DIR, "seg_*.ts"))
    if not segs:
        return []
    # descending by mtime (newest first)
    segs.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    need = int(seconds / SEG_SECONDS) + 2   # +2 margin for trimming + the partial segment
    chosen = segs[:need]
    # chronological (old -> new) for concatenation
    chosen.sort(key=lambda p: os.path.getmtime(p))
    return chosen


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    seconds = float(sys.argv[1])
    frames = None
    out = "/tmp/visszanez.mp4"
    a = sys.argv[2:]
    i = 0
    while i < len(a):
        if a[i] == "--frames":
            frames = int(a[i + 1]); i += 2
        elif a[i] == "--out":
            out = a[i + 1]; i += 2
        else:
            i += 1

    segs = _recent_segments(seconds)
    if not segs:
        print("NO ring-buffer data (is the stream running? stream_ctl.py status)")
        sys.exit(2)

    # concat list
    listf = "/tmp/vn_concat.txt"
    with open(listf, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")

    # concatenated raw tail
    full = "/tmp/vn_full.ts"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", listf, "-c", "copy", full],
        check=True, timeout=30,
    )
    # full may be longer than 'seconds' (segment granularity) -> cut out the last
    # 'seconds' seconds
    dur = None
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nokey=1", full],
            capture_output=True, text=True, timeout=15)
        dur = float(r.stdout.strip())
    except Exception:
        pass
    ss = max(0.0, (dur - seconds)) if dur else 0.0

    if frames:
        # K evenly spaced JPGs from the last 'seconds'
        for old in glob.glob("/tmp/vn_[0-9]*.jpg"):
            try:
                os.remove(old)
            except OSError:
                pass
        fps = max(1, frames) / seconds
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.2f}", "-i", full,
             "-vf", f"fps={fps:.4f}", "-q:v", "3", "-frames:v", str(frames),
             "/tmp/vn_%03d.jpg"],
            check=True, timeout=30,
        )
        got = sorted(glob.glob("/tmp/vn_[0-9]*.jpg"))
        print(f"OK: {len(got)} frames from the last {seconds:.0f} s:")
        for g in got:
            print("  ", g)
    else:
        # mp4 from the last 'seconds' (H.264 copy, keyframe-aligned start)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{ss:.2f}", "-i", full,
             "-c", "copy", "-movflags", "+faststart", out],
            check=True, timeout=30,
        )
        sz = os.path.getsize(out) if os.path.exists(out) else 0
        print(f"OK: {out} ({sz/1e6:.2f} MB) — the last ~{seconds:.0f} s "
              f"(from {len(segs)} ring-buffer segments, full tail {dur:.1f}s)")


if __name__ == "__main__":
    main()
