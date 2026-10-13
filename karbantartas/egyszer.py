#!/usr/bin/env python3
"""egyszer.py ("egyszer" = once) — "wake me IMMEDIATELY on the first relevant line" launcher for
background-Bash watchers.

Why it exists: the obvious `python watcher | while read … exit 0` wrapper is broken. The while-subshell
exits, but the PIPELINE only ends (= the notification only fires) when the python process dies too,
and that only happens on its NEXT write (broken pipe) plus an error cycle. Result: you learn about
every event only when the next one arrives.

This launcher runs the watcher as a CHILD PROCESS, reads it line by line, and on the first line that
matches the pattern it:
  1. prints the line, 2. waits --var seconds (so lines arriving together are printed too),
  3. STOPS the child, 4. drains every line still in the pipe and prints the matching ones
  (→ whatever the child printed and booked as "seen" shows up here too: nothing is lost), 5. exits 0.
If the child exits on its own: "WATCHER STOPPED (exit N)" + exit 3 (so you hear about that as well), followed by
the child's last stderr line, so you can see WHY it stopped (bot tokens and key=/token= values redacted).
Exit 2 = usage error (no command given, or bad arguments).

Usage:
  PYTHONUTF8=1 python -u egyszer.py --minta "^(TELEGRAM|monitor-error)" -- python -u monitor_wake_v3_db.py
"""
import argparse, re, subprocess, sys, threading, time, queue

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minta", "--pattern", dest="minta", required=True,
                    help="regex; a line matching it wakes you")
    ap.add_argument("--var", "--wait", dest="var", type=float, default=1.0,
                    help="seconds to wait after the first match")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("parancs", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.parancs[1:] if a.parancs and a.parancs[0] == "--" else a.parancs
    if not cmd:
        print("egyszer.py: no command given", flush=True); sys.exit(2)
    minta = re.compile(a.minta)
    titok = re.compile(r"bot\d+:[A-Za-z0-9_-]+|((?:token|key|secret|password)=)[^\s&]+", re.I)

    def kitakar(t):   # ("kitakar" = mask) never print a bot token or a key into the transcript
        return titok.sub(lambda m: (m.group(1) or "bot") + "<redacted>", t)

    p = subprocess.Popen(cmd, cwd=a.cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, bufsize=0)
    q = queue.Queue()
    utolso_hiba = [""]   # the child's last non-empty stderr line ("utolsó hiba" = last error)

    def hiba_olvas():
        for raw in iter(p.stderr.readline, b""):
            t = raw.decode("utf-8", "replace").strip()
            if t:
                utolso_hiba[0] = t

    threading.Thread(target=hiba_olvas, daemon=True).start()

    def olvas():
        for raw in iter(p.stdout.readline, b""):
            q.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        q.put(None)

    threading.Thread(target=olvas, daemon=True).start()

    talalat = False
    hatarido = None
    while True:
        try:
            sor = q.get(timeout=0.2)
        except queue.Empty:
            sor = ""
            if hatarido and time.time() >= hatarido:
                break
            continue
        if sor is None:                      # the child exited on its own
            if talalat:
                sys.exit(0)
            rc = p.wait()
            time.sleep(0.2)   # let the stderr reader catch the last line
            print(f"WATCHER STOPPED (exit {rc}): {kitakar(' '.join(cmd))[:120]}", flush=True)
            if utolso_hiba[0]:
                print(f"  last stderr line: {kitakar(utolso_hiba[0])[:300]}", flush=True)
            sys.exit(3)
        if minta.search(sor):
            print(sor, flush=True)
            if not talalat:
                talalat = True
                hatarido = time.time() + a.var
        if hatarido and time.time() >= hatarido:
            break

    try:
        p.terminate()
    except Exception:
        pass
    # lines still in the pipe (whatever the child printed before it was stopped)
    veg = time.time() + 5
    while time.time() < veg:
        try:
            sor = q.get(timeout=0.3)
        except queue.Empty:
            if p.poll() is not None:
                break
            continue
        if sor is None:
            break
        if minta.search(sor):
            print(sor, flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
