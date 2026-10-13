#!/usr/bin/env python3
"""kontextus_mero.py ("kontextus-mérő" = context meter) — an external gauge of how FULL the running
Claude Code session's context is.

It does not look at the file size (that is the whole history across compactions). It reads the
`usage` field of the LAST assistant record in the session transcript (.jsonl):
input + cache_read + cache_creation = the context just sent, in tokens. Compactions are marked by
system records carrying `compactMetadata` (preTokens/postTokens/durationMs).

Where the transcripts are: Claude Code keeps them in ~/.claude/projects/<slug>/, where <slug> is the
project folder path with every non-alphanumeric character turned into "-"
(e.g. D:\\work\\home-ai → D--work-home-ai). Resolved in this order:
  --projekt-mappa <dir>  >  CLAUDE_TRANSCRIPT_DIR  >  slug of CLAUDE_PROJECT_DIR  >  slug of the current folder.
With --session <id>, if that folder doesn't hold the transcript, every ~/.claude/projects/* folder is searched.

Usage:
  python kontextus_mero.py                      # one-off measurement of the most recent session
  python kontextus_mero.py --session <id>       # a given session
  python kontextus_mero.py --watch 600000 300   # watcher: threshold (tokens), poll (s) — prints one
                                                #   line when the threshold is crossed (an event)
Set the threshold a little BELOW your auto-compaction window, so there is time to write the handover.
"""
import argparse, glob, json, os, re, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def projekt_mappa(kezi=None):
    if kezi:
        return kezi
    if os.environ.get("CLAUDE_TRANSCRIPT_DIR"):
        return os.environ["CLAUDE_TRANSCRIPT_DIR"]
    gyoker = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(gyoker))
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug)


def keres_session(session):
    """<session>.jsonl in ANY ~/.claude/projects/* folder (so the cwd doesn't matter when --session is given)."""
    talalat = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*", session + ".jsonl"))
    return max(talalat, key=os.path.getmtime) if talalat else None


def legfrissebb_session(proj):
    fajlok = glob.glob(os.path.join(proj, "*.jsonl"))
    if not fajlok:
        return None
    return max(fajlok, key=os.path.getmtime)

def tail_lines(path, max_bytes=8_000_000):
    """Reads the last max_bytes bytes (the file can be 300+ MB), split into lines."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - max_bytes))
        data = f.read()
    lines = data.split(b"\n")
    return lines[1:] if size > max_bytes else lines   # the first line may be cut off

def meres(path):
    usage = None; usage_ts = None; utolso_compact = None
    for raw in reversed(tail_lines(path)):
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except Exception:
            continue
        if usage is None and r.get("type") == "assistant":
            u = (r.get("message") or {}).get("usage")
            if u:
                usage = u; usage_ts = r.get("timestamp")
        if utolso_compact is None and r.get("compactMetadata"):
            utolso_compact = {"ts": r.get("timestamp"), **{k: r["compactMetadata"].get(k) for k in ("trigger", "preTokens", "postTokens", "durationMs")}}
        if usage is not None and utolso_compact is not None:
            break
    if not usage:
        return None
    ctx = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
    return {"session": os.path.basename(path), "context_tokens": ctx, "usage_ts": usage_ts, "last_compaction": utolso_compact}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="session id (the .jsonl name without extension)")
    ap.add_argument("--projekt-mappa", "--transcript-dir", dest="projekt_mappa",
                    help="the ~/.claude/projects/<slug> folder (see the docstring)")
    ap.add_argument("--watch", nargs=2, metavar=("THRESHOLD", "POLL_S"), help="watcher mode")
    a = ap.parse_args()
    if a.session and not re.fullmatch(r"[A-Za-z0-9_-]+", a.session):
        print(f"invalid --session {a.session!r} (letters, digits, - and _ only)", file=sys.stderr); sys.exit(2)
    proj = projekt_mappa(a.projekt_mappa)
    path = os.path.join(proj, a.session + ".jsonl") if a.session else legfrissebb_session(proj)
    if a.session and not os.path.exists(path):
        path = keres_session(a.session)
    if not path or not os.path.exists(path):
        print(f"no session transcript in {proj} (set CLAUDE_TRANSCRIPT_DIR or --projekt-mappa)", file=sys.stderr); sys.exit(2)
    if not a.watch:
        m = meres(path)
        print(json.dumps(m, ensure_ascii=False) if m else "no usage record")
        return
    kuszob, poll = int(a.watch[0]), int(a.watch[1])
    jelzett = False
    print(f"context watcher up: {os.path.basename(path)} | threshold {kuszob:,} | poll {poll}s", flush=True)
    while True:
        m = meres(path)
        if m:
            ctx = m["context_tokens"]
            if ctx >= kuszob and not jelzett:
                print(f"⚠️ CONTEXT {ctx:,} tokens ≥ threshold {kuszob:,} — time to write the handover before compaction (last compaction: {m['last_compaction']})", flush=True)
                jelzett = True
            elif ctx < kuszob * 0.5 and jelzett:
                print(f"✅ compaction happened, context now {ctx:,} tokens", flush=True)
                jelzett = False
        time.sleep(poll)

if __name__ == "__main__":
    main()
