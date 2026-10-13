"""tarolas.py — file operations: JSONL read/write, locking, key loading.

Deliberately dumb: no business rules, only I/O — but the files are our only
state, so locking and atomic replacement live here too (M1, Kö7).
"""

import contextlib
import json
import os
import tempfile
import time

MAPPA = os.path.dirname(os.path.abspath(__file__))

INBOX = os.path.join(MAPPA, "inbox.jsonl")
VAROLISTA = os.path.join(MAPPA, "varolista.jsonl")
NAPLO = os.path.join(MAPPA, "naplo.jsonl")
TILTO_MINTAK = os.path.join(MAPPA, "tilto_mintak.json")
SZIVESEN_LATNAK = os.path.join(MAPPA, "szivesen_latnak.json")
# Key file: BSKY_KEY_FILE env var wins (keep it OUTSIDE the repository); default next to the scripts (git-ignored).
KULCS_FAJL = os.environ.get("BSKY_KEY_FILE") or os.path.join(MAPPA, "bsky_kulcs.env")
PISZKOZATOK = os.path.join(MAPPA, "piszkozatok")
ZAR_FAJL = os.path.join(MAPPA, "varolista.lock")

# If a lock file is older than this, it is probably left over from a crashed run
ZAR_ELAVULAS = 30 * 60


class ZarHiba(RuntimeError):
    """The lock file is taken — another instance is running, or a run crashed."""


@contextlib.contextmanager
def iro_zar(utvonal=ZAR_FAJL):
    """EXCLUSIVE lock file (`O_EXCL`) around every WRITING script.

    This way two instances running at the same time cannot pull the queue
    out from under each other. The lock is always released, even on exception.
    """
    try:
        fd = os.open(utvonal, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        kor = None
        try:
            kor = time.time() - os.path.getmtime(utvonal)
        except OSError:
            pass
        uzenet = ("The lock file is taken (varolista.lock): either another "
                  "instance is running, or a run crashed.")
        if kor is not None and kor > ZAR_ELAVULAS:
            uzenet += (" The lock has been there for %d minutes — it needs a MANUAL "
                       "check, after which it can be deleted." % int(kor // 60))
        raise ZarHiba(uzenet) from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(utvonal)


def konzol_utf8():
    """The Windows console defaults to a legacy code page (e.g. cp1250) — accented
    or symbol output would then raise an exception. So output is switched to
    UTF-8, and whatever still does not fit is replaced instead of aborting the run."""
    import sys
    for folyam in (sys.stdout, sys.stderr):
        try:
            folyam.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def jsonl_olvas(utvonal):
    """List of lines. Missing file → empty list.

    A CORRUPT line does NOT disappear silently (Kö7): a marker item stays in
    its place (`{"_serult": True, "_sorszam": n}`), which the caller notices
    via `serult_sorok()`, and which stops sending.
    """
    tetelek = []
    if not os.path.isfile(utvonal):
        return tetelek
    with open(utvonal, "r", encoding="utf-8") as f:
        for sorszam, sor in enumerate(f, start=1):
            sor = sor.strip()
            if not sor:
                continue
            try:
                tetelek.append(json.loads(sor))
            except json.JSONDecodeError:
                tetelek.append({"_serult": True, "_sorszam": sorszam,
                                "_hossz": len(sor)})
    return tetelek


def serult_sorok(tetelek):
    """Line numbers of the corrupt lines (empty list = the file is intact)."""
    return [t.get("_sorszam") for t in tetelek
            if isinstance(t, dict) and t.get("_serult")]


def _fsync_ir(utvonal, sorok):
    """Atomic write: temporary file → fsync → os.replace."""
    konyvtar = os.path.dirname(os.path.abspath(utvonal))
    fd, ideiglenes = tempfile.mkstemp(dir=konyvtar, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for sor in sorok:
                f.write(sor + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(ideiglenes, utvonal)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(ideiglenes)
        raise


def jsonl_hozzafuz(utvonal, tetel):
    """Append one item. Always explicit UTF-8, forced to disk."""
    with open(utvonal, "a", encoding="utf-8") as f:
        f.write(json.dumps(tetel, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def jsonl_ujrair(utvonal, tetelek, kulcs="vid"):
    """State update by MERGING, with atomic replacement (M1).

    The earlier version overwrote the whole file with what it had read at the
    start of the run — lines appended in the meantime were silently lost. Now
    we start from the CURRENT content of the file and replace only the lines
    matching on `kulcs`. Whatever someone else appended meanwhile is kept.
    """
    jelenlegi = jsonl_olvas(utvonal)
    uj_szerint = {}
    sorrend = []
    for t in tetelek:
        k = t.get(kulcs) if isinstance(t, dict) else None
        if k:
            uj_szerint[k] = t
            sorrend.append(k)

    eredmeny = []
    for sor in jelenlegi:
        k = sor.get(kulcs) if isinstance(sor, dict) else None
        if k and k in uj_szerint:
            eredmeny.append(uj_szerint.pop(k))
        else:
            eredmeny.append(sor)
    for k in sorrend:  # whatever was not yet in the file goes to the end
        if k in uj_szerint:
            eredmeny.append(uj_szerint.pop(k))

    _fsync_ir(utvonal, [json.dumps(t, ensure_ascii=False) for t in eredmeny])


def tetel_allapot_ir(utvonal, tetel, kulcs="vid"):
    """Write the state of ONE item, immediately to disk (write-ahead, M1)."""
    jsonl_ujrair(utvonal, [tetel], kulcs=kulcs)


def naploz(utvonal, esemeny):
    """Log entry. It is the caller's responsibility NOT to put secrets into it."""
    jsonl_hozzafuz(utvonal, esemeny)


def piszkozat_utvonal(megadott):
    """Resolution of `--szoveg-fajl` (Kö4).

    We read ONLY from the `bluesky/piszkozatok/` folder, after REAL path
    resolution (against symlinks and `..`), and only with a harmless extension.
    This way the key file, the log, the memory files and the source code
    structurally cannot be proposed as a post.
    """
    valos = os.path.realpath(os.path.join(PISZKOZATOK, megadott)) \
        if not os.path.isabs(megadott) else os.path.realpath(megadott)
    mappa = os.path.realpath(PISZKOZATOK)
    try:
        belul = os.path.commonpath([valos, mappa]) == mappa
    except ValueError:  # different drive
        belul = False
    if not belul or valos == mappa:
        raise ValueError(
            "--szoveg-fajl can only be read from the piszkozatok/ folder "
            "(the given path is deliberately not printed).")
    kiterjesztes = os.path.splitext(valos)[1].lower()
    if kiterjesztes in (".env", ".json", ".db", ".py", ".jsonl", ".sqlite",
                        ".sqlite3", ".log", ".pem", ".key"):
        raise ValueError("This extension is forbidden for --szoveg-fajl: "
                         + kiterjesztes)
    if not os.path.isfile(valos):
        raise FileNotFoundError(
            "No such draft in the piszkozatok/ folder.")
    return valos


def bsky_kulcs_betolt(utvonal=KULCS_FAJL):
    """Loads the Bluesky app password from a simple KEY=VALUE file.

    The values of the returned dict must NEVER be printed, logged or put into
    an error message. A missing file gives a descriptive error — but shows
    neither its content nor its FULL PATH (A3: the path contains the Windows
    user name, which our own deny filter treats as personal data).
    """
    if not os.path.isfile(utvonal):
        raise FileNotFoundError(
            "The Bluesky key file is missing. The owner creates it by hand "
            "from the sample file, with an app login, not the main login. "
            "The full path is deliberately not printed.")
    adat = {}
    with open(utvonal, "r", encoding="utf-8") as f:
        for sor in f:
            sor = sor.strip()
            if not sor or sor.startswith("#") or "=" not in sor:
                continue
            kulcs, ertek = sor.split("=", 1)
            adat[kulcs.strip()] = ertek.strip()
    for kotelezo in ("BSKY_HANDLE", "BSKY_APP_PASSWORD"):
        if not adat.get(kotelezo):
            raise ValueError("Missing field in the key file: " + kotelezo)
    return adat
