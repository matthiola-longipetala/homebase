#!/usr/bin/env python3
"""robot_fulelo_varo.py ("fülelő-váró" = listener-waiter) — a one-shot waiter on the robot's listening log
that does NOT lose lines between re-arms.

WHY THIS EXISTS. The older pattern — `egyszer.py … ssh … "tail -n0 -F /tmp/fulelo_events.log"` (egyszer.py:
"egyszer" = once) — exits after every event and has to be re-armed. `tail -n0` only sees lines written AFTER the
re-arm, so anything said in between (while the assistant was speaking, or during the `sleep` before the re-arm) was
lost and had to be looked up by hand with `tail -n 4`.

This waiter keeps the number of lines already seen in a state file and continues FROM THERE on the next start
(`tail -n +N+1 -F`). If the log is shorter than the saved number (the Pi rebooted, /tmp was cleared), it starts from
the beginning. On the first start (no state, or state older than 30 minutes) it starts from the current end and does
not replay old lines.

Output: EVERY new line (the reader does the filtering, not the script). After the first line matching the pattern
(plus --var seconds for lines that follow right after it) it exits with 0. If ssh drops: "WATCHER STOPPED (exit N)"
+ exit 3.

Usage (background Bash; run the same line again after every event; "karbantartas" = maintenance):
  PYTHONUTF8=1 python -u karbantartas/robot_fulelo_varo.py
Options: --minta/--pattern REGEX (default: a speech line; "[hang: …]" sound lines do not wake it) · --var/--wait SEC
         · --nullaz/--reset (delete the state) · --elavul/--stale MIN (state older than this = first start)
         · --helyi/--local FILE (for tests: local bash tail instead of ssh)
"""
import argparse, os, re, subprocess, sys, threading, queue, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ITT = os.path.dirname(os.path.abspath(__file__))
ALLAPOT = os.path.join(ITT, "_robot_fulelo_offset.txt")
# The robot's address and key come from the environment (same names as pi_ssh.py).
SSH = ["ssh", "-i", os.environ.get("PI_KEY", os.path.join(os.path.expanduser("~"), ".ssh", "id_ed25519")),
       "-o", "BatchMode=yes", "-o", "ServerAliveInterval=15", "-o", "ConnectTimeout=10",
       f'{os.environ.get("PI_USER", "pi")}@{os.environ.get("PI_HOST", "")}']
NAPLO = "/tmp/fulelo_events.log"


def tavoli_parancs(fajl, n):
    # n = number of lines seen so far, or "" (first start: from the current end)
    return (f'f={fajl}; touch $f; t=$(wc -l < $f); s="{n}"; '
            f'if [ -z "$s" ]; then s=$t; fi; if [ "$t" -lt "$s" ]; then s=0; fi; '
            f'echo "#START $s"; exec tail -n +$((s+1)) -F $f')


def ment(n):
    tmp = ALLAPOT + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(n))
    os.replace(tmp, ALLAPOT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minta", "--pattern", default=r"^\[[^\]]+\] [^\[]")
    ap.add_argument("--var", "--wait", type=float, default=1.5)
    ap.add_argument("--nullaz", "--reset", action="store_true")
    ap.add_argument("--helyi", "--local", default=None)
    ap.add_argument("--elavul", "--stale", type=float, default=30,
                    help="minutes; a state older than this counts as a first start")
    a = ap.parse_args()
    global ALLAPOT
    if a.helyi:
        ALLAPOT = a.helyi + ".offset"
    if a.nullaz and os.path.exists(ALLAPOT):
        os.remove(ALLAPOT)
    n = open(ALLAPOT).read().strip() if os.path.exists(ALLAPOT) else ""
    if n and time.time() - os.path.getmtime(ALLAPOT) > a.elavul * 60:
        n = ""   # old state (e.g. from yesterday; someone else may have used the robot since): start from the current end
    if a.helyi:
        cmd = ["bash", "-c", tavoli_parancs(a.helyi, n)]
    else:
        cmd = SSH + [tavoli_parancs(NAPLO, n)]
    minta = re.compile(a.minta)

    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, bufsize=0)
    q = queue.Queue()

    def olvas():
        for raw in iter(p.stdout.readline, b""):
            q.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
        q.put(None)

    threading.Thread(target=olvas, daemon=True).start()
    szam = None
    hatarido = None
    while True:
        try:
            sor = q.get(timeout=0.2)
        except queue.Empty:
            if hatarido and time.time() >= hatarido:
                break
            continue
        if sor is None:
            if hatarido:
                break
            print(f"WATCHER STOPPED (exit {p.wait()})", flush=True)
            sys.exit(3)
        if szam is None:
            if sor.startswith("#START "):
                szam = int(sor.split()[1]); ment(szam)
            continue
        szam += 1
        ment(szam)
        print(sor, flush=True)
        if hatarido is None and minta.search(sor):
            hatarido = time.time() + a.var
    p.kill()
    # lines still in the pipe (already written but not yet read) are NOT counted: the next start delivers them
    sys.exit(0)


if __name__ == "__main__":
    main()
