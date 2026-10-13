"""tarolas_or.py — file operations for the guard. Deliberately dumb: it holds
no security rule, only I/O.

It takes two things seriously:
  * every write is ATOMIC (temporary file → fsync → os.replace), because the
    guard's state is the trust anchor: no half-written file may be left behind;
  * a CORRUPTED JSONL line does not vanish silently — a marker item stays in its place.
"""

import contextlib
import json
import os
import tempfile


def mappa_keszit(utvonal):
    """Creates the folder if it does not exist. The PROTECTED tree is not built
    by the guard — this is only called for its own folders (allapot, kozos\\be)."""
    os.makedirs(utvonal, exist_ok=True)


def fsync_ir(utvonal, tartalom):
    """Atomic text write. `tartalom` is a string, always UTF-8."""
    konyvtar = os.path.dirname(os.path.abspath(utvonal))
    mappa_keszit(konyvtar)
    fd, ideiglenes = tempfile.mkstemp(dir=konyvtar, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(tartalom)
            f.flush()
            os.fsync(f.fileno())
        os.replace(ideiglenes, utvonal)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(ideiglenes)
        raise


def uj_fajl_bajtokkal(utvonal, bajtok):
    """Create a NEW file with the given bytes — only if it does not exist yet.

    M''''2: the dropped-in draft is NOT moved into the protected folder; its
    bytes are written to a NEW file instead. On Windows, `os.replace`
    (MoveFileEx) within the same volume PRESERVES the file's access control
    list and owner, so the moved file would have sat in the protected
    `allapot\\varo\\` folder still owned by the untrusted user, with that
    user's rights. A new file created here, by contrast, inherits from the
    TARGET FOLDER, and its owner is the guard.

    `O_EXCL`: if the name were taken after all, nothing is overwritten.
    """
    mappa_keszit(os.path.dirname(os.path.abspath(utvonal)))
    fd = os.open(utvonal,
                 os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(bajtok)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(utvonal)
        raise


def json_ir(utvonal, adat):
    """Atomic write of a JSON object."""
    fsync_ir(utvonal, json.dumps(adat, ensure_ascii=False, indent=2) + "\n")


def json_olvas(utvonal, alap=None):
    """Read JSON. Missing or broken file → `alap` (fail-safe, but the caller
    decides whether that is fail-closed for it)."""
    try:
        with open(utvonal, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return alap


def jsonl_hozzafuz(utvonal, tetel):
    """Append one line, forced to disk."""
    mappa_keszit(os.path.dirname(os.path.abspath(utvonal)))
    with open(utvonal, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(tetel, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def jsonl_olvas(utvonal):
    """List of lines. Missing file → empty list.

    A corrupted line is replaced by `{"_serult": True, "_sorszam": n}`: the
    caller notices it and stops (silently skipping would free up a quota slot).
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
            except ValueError:
                tetelek.append({"_serult": True, "_sorszam": sorszam})
    return tetelek


def serult_sorok(tetelek):
    return [t.get("_sorszam") for t in tetelek
            if isinstance(t, dict) and t.get("_serult")]


def forgatas(utvonal, max_bajt, megtartott=3):
    """If the file is larger than `max_bajt`, rename it to `.1`, `.2`, ….

    Returns: True if rotated. The oldest one (`.megtartott`) is lost.
    """
    try:
        meret = os.path.getsize(utvonal)
    except OSError:
        return False
    if meret <= max_bajt:
        return False
    for i in range(megtartott, 0, -1):
        regi = "%s.%d" % (utvonal, i)
        if i == megtartott:
            with contextlib.suppress(OSError):
                os.remove(regi)
            continue
        uj = "%s.%d" % (utvonal, i + 1)
        if os.path.isfile(regi):
            with contextlib.suppress(OSError):
                os.replace(regi, uj)
    with contextlib.suppress(OSError):
        os.replace(utvonal, utvonal + ".1")
    return True


class ZarHiba(RuntimeError):
    """The lock file is taken — an instance is already running."""


ZAR_ELAVULAS = 30 * 60


def fut_e_a_folyamat(pid, futtato=None):
    """Is that process still alive? When uncertain: YES (the stricter direction).

    On Windows `os.kill(pid, 0)` is NOT a harmless query — it would kill the
    process — so we ask `tasklist`. `futtato` is injectable so that it can
    be tested.

    A''1: we do NOT search for a substring in the output. `tasklist` also
    prints memory usage (for example `12 345 K`), so a PID `12345` would have
    given a "match" even if the process was not running — and a stuck lock
    could thus have paralysed the guard permanently. So we ask for CSV format
    and compare the second field (the PID) exactly.
    """
    if not pid:
        return True
    if futtato is None:
        import subprocess

        def futtato(azonosito):
            folyamat = subprocess.run(
                ["tasklist", "/FI", "PID eq %d" % azonosito, "/FO", "CSV",
                 "/NH"],
                capture_output=True, timeout=20, check=False)
            return (folyamat.stdout or b"").decode("oem", errors="replace")

    try:
        kimenet = futtato(int(pid))
    except Exception:  # noqa: BLE001 — uncertainty → "running"
        return True
    return _csv_pid_talalat(kimenet, int(pid))


def _csv_pid_talalat(kimenet, pid):
    """Does EXACTLY this PID appear in the output of `tasklist /FO CSV /NH`?

    Line shape: `"imagename","PID","session","number","memory"`. If the output
    is not like that (another language, a filter message), we CANNOT decide → "running".
    """
    import csv
    import io

    sorok = [s for s in (kimenet or "").splitlines() if s.strip()]
    if not sorok:
        return False  # the filter found nothing — the process is not running
    talalt_ervenyes_sort = False
    for mezok in csv.reader(io.StringIO("\n".join(sorok))):
        if len(mezok) < 2:
            continue
        talalt_ervenyes_sort = True
        try:
            if int(mezok[1].strip()) == pid:
                return True
        except ValueError:
            continue
    # There was output, but not a CSV line (e.g. "INFO: No tasks are running…") —
    # in that case we do not claim it is running.
    return not talalt_ervenyes_sort and _nem_ures_uzenet(sorok)


def _nem_ures_uzenet(sorok):
    """The "no such task" message of `tasklist` → the process is NOT running."""
    # Hungarian language data: "NINCS" is the keyword of the Hungarian-localised
    # Windows `tasklist` message; for another Windows UI language, add that
    # language's keyword to this check.
    egyben = " ".join(sorok).upper()
    return not ("INFO" in egyben or "NINCS" in egyben or "NO TASKS" in egyben)


@contextlib.contextmanager
def iro_zar(utvonal):
    """Exclusive lock file (`O_EXCL`), so that two instances do not run at once.

    Stuck lock: if the file is old AND no running process belongs to the PID
    in it, we take it over (paralysis itself was an attack goal — M'4).
    """
    mappa_keszit(os.path.dirname(os.path.abspath(utvonal)))
    try:
        fd = os.open(utvonal, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        import time as _ido
        modositva, pid = None, None
        try:
            modositva = os.path.getmtime(utvonal)
            with open(utvonal, "r", encoding="utf-8") as f:
                pid = int((f.read() or "0").strip() or 0)
        except (OSError, ValueError):
            pass
        elavult = modositva is not None and \
            (_ido.time() - modositva) > ZAR_ELAVULAS
        # 2026-09-20: `Stop-ScheduledTask` kills the process hard, `finally`
        # does not run, the lock stays — and the guard did not start for 30
        # minutes (exit code 4). If the PID in the lock is CERTAINLY not alive,
        # we take it over immediately; age only matters if the PID cannot be
        # read. When uncertain, `fut_e_a_folyamat` still says YES, so we never
        # take a live lock.
        if (pid and not fut_e_a_folyamat(pid)) or (not pid and elavult):
            with contextlib.suppress(OSError):
                os.remove(utvonal)
            fd = os.open(utvonal, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        else:
            raise ZarHiba("An instance is already running (lock file taken).") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        yield
    finally:
        with contextlib.suppress(OSError):
            os.remove(utvonal)


def konzol_utf8():
    """The Windows console defaults to cp1250 — it would raise on accented output."""
    import sys
    for folyam in (sys.stdout, sys.stderr):
        try:
            folyam.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def kulcs_ertek_betolt(utvonal, kotelezo_mezok):
    """Read a simple `KEY=VALUE` file from the secrets folder.

    The returned values are SECRETS: they must never reach a log, the console,
    or an exception text. So the error message only shows the FILE NAME, never
    the full path or the content.
    """
    nev = os.path.basename(utvonal)
    if not os.path.isfile(utvonal):
        raise FileNotFoundError(
            "The secrets file is missing: %s (the full path is deliberately not "
            "shown). The owner creates it as administrator." % nev)
    adat = {}
    # `utf-8-sig`: Notepad's "UTF-8 with BOM" variant writes a byte order mark
    # in front. Without this, the first key name would be `<U+FEFF>OR_BOT_TOKEN`,
    # and the guard would have searched the file for a non-existent typo (M'''0/c).
    with open(utvonal, "r", encoding="utf-8-sig") as f:
        for sor in f:
            sor = sor.strip()
            if not sor or sor.startswith("#") or "=" not in sor:
                continue
            kulcs, ertek = sor.split("=", 1)
            adat[kulcs.strip()] = ertek.strip()
    for kotelezo in kotelezo_mezok:
        if not adat.get(kotelezo):
            raise ValueError("Missing field in the %s file: %s" % (nev, kotelezo))
    return adat
