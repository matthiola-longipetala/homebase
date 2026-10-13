#!/usr/bin/env python3
"""posta_figyelo.py ("posta-figyelő" = mailbox watcher) — the wake-up for the two Claudes' shared
mailbox: one message per line, `sender → recipient`, append only.

Line format:
    2026-01-05 20:30 | night → day | REQUEST | free text …
(the role names are yours to choose; a leading "- " is allowed, "->" works as well as "→").

How it works: every 30 s it checks the file size. If the file grew, it picks out the NEW lines
addressed to ME (`→ <me>` in the header; several names separated by commas), prints them, and EXITS
with 0 → one notification. If all new lines are for someone else (e.g. my own message), it carries
on silently. Restart it after every event (like the Telegram watcher: a rare event → background
Bash, not Monitor).

Gap fill: `--allapot <file>` ("állapot" = state) saves the processed offset, and at start the watcher
continues from there, so a message arriving in the gap between two runs (restart after an event, app
restart) is not lost. Default: <mailbox>.offset.<me>. `--allapot -` = no saving.

The mailbox is APPEND ONLY. Never edit an earlier line in place (e.g. with `sed -i`): a rewritten,
shorter file cannot be matched to the saved offset. The watcher then skips to the end and prints
one warning line instead of replaying the whole file.

Usage:
    python posta_figyelo.py --nekem day
    python posta_figyelo.py --nekem night,morning --masodperc 30 --fajl <path>/mailbox.md
Mailbox path: --fajl, or the POSTA_FILE env variable, or kontextus/kollegak_posta.md in the memory folder
(CLAUDE_MEMORY_DIR, else MEMORIA_ROOT, else ../memoria). Create the file first (it may be empty). Exit code: 0 = a new line for me (in the output), 2 = the file does not exist.
"""
import argparse
import io
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_MEM = (os.environ.get("CLAUDE_MEMORY_DIR") or os.environ.get("MEMORIA_ROOT")
        or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memoria"))
ALAP = os.environ.get("POSTA_FILE") or os.path.normpath(os.path.join(_MEM, "kontextus", "kollegak_posta.md"))


def uj_sorok(ut, offset):
    with io.open(ut, "rb") as f:
        f.seek(offset)
        adat = f.read()
    return adat.decode("utf-8", "replace").splitlines(), offset + len(adat)


def nekem_szol(sor, nevek):
    # "2026-01-05 20:30 | night → day | REQUEST | ..."  — the recipient of the second field
    m = re.match(r"^(?:-\s*)?\d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*\|\s*(\S+)\s*(?:→|->)\s*(\S+)\s*\|", sor)
    return bool(m) and m.group(2).lower() in nevek


def offset_olvas(ut):
    try:
        with io.open(ut, encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return None


def offset_ir(ut, ertek):
    if not ut:
        return
    try:
        with io.open(ut, "w", encoding="utf-8") as f:
            f.write(str(ertek))
    except OSError:
        pass


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--nekem", "--me", dest="nekem", required=True,
                   help="my role name(s), comma-separated")
    p.add_argument("--fajl", "--file", dest="fajl", default=ALAP)
    p.add_argument("--masodperc", "--seconds", dest="masodperc", type=float, default=30.0)
    p.add_argument("--allapot", "--state", dest="allapot", default=None,
                   help="saved offset file; default: <mailbox>.offset.<me>; '-' = no saving")
    a = p.parse_args()
    if not os.path.isfile(a.fajl):
        print("MAILBOX WATCHER: no such file:", a.fajl)
        return 2
    nevek = [n.strip().lower() for n in a.nekem.split(",") if n.strip()]
    allapot = None if a.allapot == "-" else (a.allapot or a.fajl + ".offset." + "_".join(nevek))
    meret_most = os.path.getsize(a.fajl)
    offset = offset_olvas(allapot) if allapot else None
    if offset is None or offset > meret_most:
        offset = meret_most
    print("MAILBOX WATCHER started (me=%s): %s (%d s, offset %d/%d)"
          % (",".join(nevek), a.fajl, a.masodperc, offset, meret_most), flush=True)
    offset_ir(allapot, offset)   # at start too: the gap fill counts from here

    def dolgoz():
        nonlocal offset
        sorok, offset = uj_sorok(a.fajl, offset)
        enyem = [s for s in sorok if nekem_szol(s, nevek)]
        if enyem:
            print("MAILBOX (%s) — new message for me:" % time.strftime("%H:%M"), flush=True)
            for s in enyem:
                print(s, flush=True)
        offset_ir(allapot, offset)
        return bool(enyem)

    if offset < meret_most and dolgoz():   # gap fill at start
        return 0
    while True:
        time.sleep(a.masodperc)
        try:
            meret = os.path.getsize(a.fajl)
        except OSError:
            continue
        if meret < offset:          # rewritten/shortened: the offset is meaningless now
            print("mailbox-warning: the file got shorter (edited in place? it is append-only) — "
                  "skipping to the end, check the last lines by hand", flush=True)
            offset = meret
            offset_ir(allapot, offset)
            continue
        if meret > offset:
            time.sleep(1.0)         # let the writer finish
            if dolgoz():
                return 0


if __name__ == "__main__":
    sys.exit(main())
