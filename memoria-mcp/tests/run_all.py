# -*- coding: utf-8 -*-
"""Run every test_*.py of this folder, each in its own subprocess.

Prints one line per file (PASS / FAIL, with the SKIP and XFAIL counts) and
exits non-zero if any file failed.

Usage:  python -X utf8 run_all.py [-v]      (-v: print the full output of failing files)
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULT_RE = re.compile(r"^RESULT: (\d+) passed, (\d+) failed, (\d+) skipped, (\d+) xfail, (\d+) xpass",
                       re.MULTILINE)


def main():
    verbose = "-v" in sys.argv
    files = sorted(HERE.glob("test_*.py"))
    if not files:
        print("no test_*.py files found")
        return 1
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    failed = []
    totals = [0, 0, 0, 0, 0]
    width = max(len(f.name) for f in files)
    t0 = time.time()
    for f in files:
        t = time.time()
        proc = subprocess.run([sys.executable, "-B", "-X", "utf8", str(f)], cwd=str(HERE), env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=600)
        out = (proc.stdout or "") + (proc.stderr or "")
        m = RESULT_RE.search(out)
        counts = [int(x) for x in m.groups()] if m else None
        if counts:
            totals = [a + b for a, b in zip(totals, counts)]
        ok = proc.returncode == 0 and counts is not None and counts[1] == 0
        extra = []
        if counts:
            extra.append(f"{counts[0]} checks")
            if counts[2]:
                extra.append(f"{counts[2]} SKIP")
            if counts[3]:
                extra.append(f"{counts[3]} XFAIL")
            if counts[4]:
                extra.append(f"{counts[4]} XPASS")
            if counts[1]:
                extra.append(f"{counts[1]} failed")
        else:
            extra.append(f"no RESULT line, exit code {proc.returncode}")
        print(f"{'PASS' if ok else 'FAIL'}  {f.name:<{width}}  ({', '.join(extra)}; {time.time() - t:.1f}s)")
        if not ok:
            failed.append(f.name)
            if verbose:
                print("-" * 70)
                print(out.rstrip())
                print("-" * 70)
    print("=" * 70)
    print(f"{len(files) - len(failed)}/{len(files)} files passed · {totals[0]} checks passed · "
          f"{totals[1]} failed · {totals[2]} skipped · {totals[3]} xfail (known server bugs) · "
          f"{totals[4]} xpass · {time.time() - t0:.1f}s")
    if failed:
        print("FAILED: " + ", ".join(failed) + "   (re-run with -v, or run the file directly)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
