"""piszkozat.py — STRICT parsing of the `kozos\\ki\\NAME.json` drop file.

The draft is written by the UNTRUSTED side. Every field is treated as if an
attacker had written it: closed field set, closed types, closed value range.
Unknown field, oversized file, non-UTF-8, malformed JSON → rejection.

The functions are PURE (except `fajlbol`): no network, no state.
"""

import json
import os
import re
import stat
import sys

import beallitasok
import szures

KOTELEZO_MEZOK = ("verzio", "tipus", "szoveg", "valasz_cel_uri", "langs",
                  "megjegyzes_tulajnak")
ENGEDETT_MEZOK = frozenset(KOTELEZO_MEZOK)

TIPUSOK = ("poszt", "valasz")
LANG_MINTA = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
LANGS_MAX = 3
URI_MINTA = re.compile(r"^at://did:[a-z0-9]+:[A-Za-z0-9._:%~-]+/"
                       r"[A-Za-z0-9._-]+/[A-Za-z0-9._~-]+$")

# --- FOLLOW and LIKE (2026-10-02) ----------------------------------------
# Two TEXT-LESS actions, each with its OWN closed field set. The `tipus` field
# decides which schema applies; the post/reply schema is unchanged.
#   kovetes:  verzio, tipus, cel_handle OR cel_did (exactly one of them),
#             megjegyzes_tulajnak
#   kedveles: verzio, tipus, cel_uri, megjegyzes_tulajnak
AKCIO_TIPUSOK = ("kovetes", "kedveles")
AKCIO_KOTELEZO = {
    "kovetes": ("verzio", "tipus", "megjegyzes_tulajnak"),
    "kedveles": ("verzio", "tipus", "cel_uri", "megjegyzes_tulajnak"),
}
AKCIO_ENGEDETT = {
    "kovetes": frozenset(("verzio", "tipus", "megjegyzes_tulajnak",
                          "cel_handle", "cel_did")),
    "kedveles": frozenset(AKCIO_KOTELEZO["kedveles"]),
}
# AT Protocol handle syntax: dot-separated DNS name, WITHOUT `@`.
HANDLE_MINTA = re.compile(
    r"^([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
HANDLE_MAX = 253
DID_MINTA = re.compile(r"^did:[a-z]+:[A-Za-z0-9._:%-]*[A-Za-z0-9._-]$")
DID_MAX = 200
# Only a POST can be liked: the collection is fixed.
POSZT_URI_MINTA = re.compile(r"^at://did:[a-z]+:[A-Za-z0-9._:%-]+"
                             r"/app\.bsky\.feed\.post/[A-Za-z0-9._~:-]{1,200}$")
POSZT_URI_MAX = 300


class PiszkozatHiba(ValueError):
    """Malformed draft. The message NEVER contains content."""


def _egesz_e(ertek):
    # In Python bool is an int — but `verzio: true` is not a version number.
    return isinstance(ertek, int) and not isinstance(ertek, bool)


def ertelmez(nyers_szoveg):
    """`(tetel, okok)` — if `okok` is not empty, the draft is REJECTED.

    The fields of the returned `tetel` are already SANITIZED.
    """
    okok = []
    try:
        adat = json.loads(nyers_szoveg)
    except ValueError:
        return None, ["nem_szabalyos_json"]
    if not isinstance(adat, dict):
        return None, ["nem_objektum"]

    tipus_ertek = adat.get("tipus")
    if isinstance(tipus_ertek, str) and tipus_ertek in AKCIO_TIPUSOK:
        # 2026-10-02: follow / like — own, closed schema.
        return _akcio_ertelmez(adat, tipus_ertek)

    ismeretlen = sorted(set(adat) - ENGEDETT_MEZOK)
    if ismeretlen:
        # K'''''1/b — we no longer write out even the FIELD NAME. The rejection
        # reason goes into `kozos\be\allapot.json`, which the untrusted side
        # reads; the field name, however, is raw text from the DROPPED file. If
        # the dropped file were not what it appears to be, arbitrary content
        # could leak out through such a name. Only the COUNT goes out.
        okok.append("ismeretlen_mezo")
        okok.append("ismeretlen_mezo_darab:%d" % len(ismeretlen))
    hianyzo = [m for m in KOTELEZO_MEZOK if m not in adat]
    if hianyzo:
        okok += ["hianyzo_mezo:" + m for m in hianyzo]
    if okok:
        return None, sorted(set(okok))

    if adat["verzio"] != 1 or not _egesz_e(adat["verzio"]):
        okok.append("rossz_verzio")

    tipus = adat["tipus"]
    if not isinstance(tipus, str) or tipus not in TIPUSOK:
        okok.append("rossz_tipus")
        tipus = None

    szoveg = adat["szoveg"]
    if not isinstance(szoveg, str):
        okok.append("szoveg_nem_szoveg")
        szoveg = ""
    else:
        # M''2 — BYTE-EXACT PREVIEW. We do NOT silently rewrite the text: if
        # sanitization would change anything in it (control, zero-width or
        # right-to-left character, a form that needs normalization), the
        # draft is REJECTED. Only the surrounding whitespace is trimmed —
        # whatever remains is what goes out, exactly as shown in the preview.
        tiszta = szures.szanitalas(szoveg, len(szoveg) + 1)
        if tiszta.strip() != szoveg.strip():
            okok.append("szoveg_atirasra_szorulna")
        szoveg = tiszta.strip()
        if not szoveg:
            okok.append("ures_szoveg")

    cel_uri = adat["valasz_cel_uri"]
    if cel_uri is not None:
        if not isinstance(cel_uri, str) or not URI_MINTA.match(cel_uri.strip()):
            okok.append("rossz_valasz_cel_uri")
            cel_uri = None
        else:
            cel_uri = cel_uri.strip()
    if tipus == "poszt" and cel_uri is not None:
        okok.append("tipus_cel_ellentmondas")
    if tipus == "valasz" and cel_uri is None:
        okok.append("valasz_cel_nelkul")

    langs = adat["langs"]
    if not isinstance(langs, list) or not langs or len(langs) > LANGS_MAX:
        okok.append("rossz_langs")
        langs = []
    else:
        tiszta = []
        for nyelv in langs:
            if not isinstance(nyelv, str) or not LANG_MINTA.match(nyelv):
                okok.append("rossz_langs")
                tiszta = []
                break
            tiszta.append(nyelv)
        langs = tiszta

    megjegyzes = _megjegyzes_ellenorzes(adat["megjegyzes_tulajnak"], okok)

    if okok:
        return None, sorted(set(okok))
    return {
        "tipus": tipus,
        "szoveg": szoveg,
        "valasz_cel_uri": cel_uri,
        "langs": langs,
        "megjegyzes_tulajnak": megjegyzes,
    }, []


def _megjegyzes_ellenorzes(megjegyzes, okok):
    """The rule for the `megjegyzes_tulajnak` field — the SAME for every type.

    Puts the errors found into the `okok` list; returns the sanitized value.
    """
    if megjegyzes is None:
        megjegyzes = ""
    if not isinstance(megjegyzes, str):
        okok.append("rossz_megjegyzes")
        return ""
    # Kö2: the note sits directly ABOVE the fixed approval line. If it could
    # span several lines, it could draw a "paragraph" there and influence the
    # decision situation. So it is ONE line, and short.
    if len(megjegyzes) > beallitasok.MEGJEGYZES_SOR_MAX:
        okok.append("tul_hosszu_megjegyzes")
    egy_sor = szures.egysoros(megjegyzes, beallitasok.MEGJEGYZES_SOR_MAX)
    if egy_sor != megjegyzes.strip():
        okok.append("tobbsoros_vagy_atirando_megjegyzes")
    return egy_sor


def _akcio_ertelmez(adat, tipus):
    """Strict parsing of a FOLLOW / LIKE draft. `(tetel, okok)`.

    Same principles as for a post: closed field set (we write out only the
    count, not the field name), closed types, values bound to a format. Values
    are NOT silently rewritten: anything not in the proper format is rejected.
    The only exception is lowercasing the handle — handles are
    case-insensitive and their canonical form is lowercase.
    """
    okok = []
    ismeretlen = sorted(set(adat) - AKCIO_ENGEDETT[tipus])
    if ismeretlen:
        okok.append("ismeretlen_mezo")
        okok.append("ismeretlen_mezo_darab:%d" % len(ismeretlen))
    okok += ["hianyzo_mezo:" + m for m in AKCIO_KOTELEZO[tipus]
             if m not in adat]
    if tipus == "kovetes":
        # A field with a `null` value counts as MISSING — so a template can
        # contain both keys with only one of them filled in.
        megadott = [m for m in ("cel_handle", "cel_did")
                    if adat.get(m) is not None]
        if not megadott:
            okok.append("hianyzo_mezo:cel_handle_vagy_cel_did")
        elif len(megadott) > 1:
            okok.append("cel_handle_es_cel_did_egyszerre")
    if okok:
        return None, sorted(set(okok))

    if adat["verzio"] != 1 or not _egesz_e(adat["verzio"]):
        okok.append("rossz_verzio")

    tetel = {"tipus": tipus}
    if tipus == "kovetes":
        handle = adat.get("cel_handle")
        did = adat.get("cel_did")
        if handle is not None:
            if (not isinstance(handle, str) or len(handle) > HANDLE_MAX
                    or not HANDLE_MINTA.match(handle)):
                okok.append("rossz_cel_handle")
                handle = None
            else:
                handle = handle.lower()
        if did is not None:
            if (not isinstance(did, str) or len(did) > DID_MAX
                    or not DID_MINTA.match(did)):
                okok.append("rossz_cel_did")
                did = None
        tetel["cel_handle"] = handle
        tetel["cel_did"] = did
    else:
        uri = adat["cel_uri"]
        if (not isinstance(uri, str) or len(uri) > POSZT_URI_MAX
                or not POSZT_URI_MINTA.match(uri)):
            okok.append("rossz_cel_uri")
            uri = None
        tetel["cel_uri"] = uri

    tetel["megjegyzes_tulajnak"] = _megjegyzes_ellenorzes(
        adat["megjegyzes_tulajnak"], okok)
    if okok:
        return None, sorted(set(okok))
    return tetel, []


def fajlbol(utvonal, max_bajt=beallitasok.PISZKOZAT_MAX_BAJT):
    """From a file: size and encoding checks, then `ertelmez`.

    The file is read up to at most `max_bajt + 1` bytes — a dropped 4 GB file
    cannot eat up the memory.
    """
    try:
        with open(utvonal, "rb") as f:
            nyers = f.read(max_bajt + 1)
    except OSError:
        return None, ["nem_olvashato"]
    if len(nyers) > max_bajt:
        return None, ["tul_nagy_fajl"]
    if not nyers.strip():
        return None, ["ures_fajl"]
    try:
        szoveg = nyers.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["nem_utf8"]
    if szoveg.startswith("﻿"):
        szoveg = szoveg[1:]
    return ertelmez(szoveg)


def szabalyos_bedobott_fajl(utvonal, max_bajt=beallitasok.PISZKOZAT_MAX_BAJT):
    """Is the dropped file STRUCTURALLY acceptable? `(rendben, ok_vagy_None)`.

    The untrusted side writes into the drop folder, so looking at the name is
    not enough:
      * `os.lstat` — symbolic links and mount points are NOT followed (A''4).
        In developer mode the untrusted user can create a symbolic link even
        without elevation;
      * no HARD link may come in either: if the file lives under several
        names, we do not know what we are moving into our protected folder
        (Kö1);
      * OVERSIZED FILE: the size is checked BEFORE reading.
    """
    try:
        adat = os.lstat(utvonal)
    except OSError:
        return False, "nem_olvashato"
    if not stat.S_ISREG(adat.st_mode):
        return False, "nem_regularis_fajl"
    if getattr(adat, "st_nlink", 1) > 1:
        return False, "merev_hivatkozas"
    if adat.st_size > max_bajt:
        return False, "tul_nagy_fajl"
    return True, None


# --- OPENING THE DROPPED FILE WITHOUT FOLLOWING LINKS --------------------
#
# K'''''1/a. The earlier flow consisted of two INDEPENDENT system calls:
# first `os.lstat` (which correctly rejected the symbolic link), then
# `os.open`, which on Windows FOLLOWS the link. In the gap between the two the
# untrusted side could replace the regular file with a link pointing to the
# secrets folder — and the guard, who has access to the secret, read the
# secret's bytes. Measurements showed the race to be winnable.
#
# The fix is not to be faster, but to make the open ITSELF not follow the
# link. On Windows this is what `CreateFileW` +
# `FILE_FLAG_OPEN_REPARSE_POINT` is for: the handle then opens the reparse
# point itself, not its target. `FILE_FLAG_BACKUP_SEMANTICS` is
# DELIBERATELY not passed, so opening a directory fails outright.
# On the handle we get, `GetFileInformationByHandle` tells us what was
# actually opened, and `GetFinalPathNameByHandleW` whether the handle's FINAL
# path is inside the drop folder.
_GENERIC_READ = 0x80000000
_FILE_SHARE_MIND = 0x00000001 | 0x00000002 | 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_HANDLE = -1

# Test hook: called with the path BETWEEN the two steps of `bevetel`
# (structural check, then reading). The deterministic simulation of the race
# swaps the file for the link through it. Always `None` in production.
_VERSENY_HOROG = None


def _windows_leiro(utvonal):
    """`(leiro, ok)` — open WITHOUT FOLLOWING links, or `(None, ok)`.

    `ok` is `"nincs_ctypes_ut"` if this path is not available on this machine —
    the caller then switches to the fallback path (lstat/fstat identity).
    """
    if not sys.platform.startswith("win"):
        return None, "nincs_ctypes_ut"
    try:
        import ctypes
        import ctypes.wintypes as wt
    except Exception:  # noqa: BLE001 — missing ctypes is not an error, just the fallback
        return None, "nincs_ctypes_ut"
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wt.HANDLE
        kernel32.CreateFileW.argtypes = (
            wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD,
            wt.DWORD, wt.HANDLE)
    except Exception:  # noqa: BLE001
        return None, "nincs_ctypes_ut"
    leiro = kernel32.CreateFileW(
        str(utvonal), _GENERIC_READ, _FILE_SHARE_MIND, None, _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT, None)
    # On error `CreateFileW` returns INVALID_HANDLE_VALUE, which as a pointer
    # looks like a different number on 32 and 64 bit — we check both.
    if leiro in (None, 0, _INVALID_HANDLE, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
        return None, "nem_nyithato"
    return leiro, None


class _FajlInfo(object):
    """The fields we need from what `GetFileInformationByHandle` returns."""

    def __init__(self, attributumok, hivatkozasok, meret):
        self.attributumok = attributumok
        self.hivatkozasok = hivatkozasok
        self.meret = meret


def _leiro_info(leiro):
    """`GetFileInformationByHandle` on the open handle. `None` if it fails."""
    import ctypes
    import ctypes.wintypes as wt

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wt.DWORD), ("dwHighDateTime", wt.DWORD)]

    class _INFO(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wt.DWORD),
            ("ftCreationTime", _FILETIME),
            ("ftLastAccessTime", _FILETIME),
            ("ftLastWriteTime", _FILETIME),
            ("dwVolumeSerialNumber", wt.DWORD),
            ("nFileSizeHigh", wt.DWORD),
            ("nFileSizeLow", wt.DWORD),
            ("nNumberOfLinks", wt.DWORD),
            ("nFileIndexHigh", wt.DWORD),
            ("nFileIndexLow", wt.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    info = _INFO()
    if not kernel32.GetFileInformationByHandle(wt.HANDLE(leiro),
                                               ctypes.byref(info)):
        return None
    return _FajlInfo(int(info.dwFileAttributes), int(info.nNumberOfLinks),
                     (int(info.nFileSizeHigh) << 32) + int(info.nFileSizeLow))


def _vegso_utvonal(leiro):
    """The FINAL (resolved) path of the handle, without the `\\\\?\\` prefix. `None` if it fails."""
    import ctypes
    import ctypes.wintypes as wt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    puffer = ctypes.create_unicode_buffer(32768)
    hossz = kernel32.GetFinalPathNameByHandleW(
        wt.HANDLE(leiro), puffer, 32767, 0)
    if not hossz or hossz > 32767:
        return None
    nev = puffer.value
    if nev.startswith("\\\\?\\UNC\\"):
        return "\\\\" + nev[8:]
    if nev.startswith("\\\\?\\"):
        return nev[4:]
    return nev


def _azonos_mappa_e(egyik, masik):
    """Do the two paths point to the same directory? (case-insensitive)"""
    try:
        a = os.path.normcase(os.path.realpath(egyik))
        b = os.path.normcase(os.path.realpath(masik))
    except (OSError, ValueError):
        return False
    return bool(a) and a == b


def _bajtok_olvasasa(fd, max_bajt):
    darabok = []
    olvasott = 0
    while olvasott <= max_bajt:
        darab = os.read(fd, 65536)
        if not darab:
            break
        darabok.append(darab)
        olvasott += len(darab)
    return b"".join(darabok)


def _ctypes_beolvasas(utvonal, max_bajt, bedobo):
    """The ctypes path. `(bajtok, ok)`; `ok == "nincs_ctypes_ut"` → fallback path."""
    import msvcrt

    leiro, ok = _windows_leiro(utvonal)
    if leiro is None:
        return None, ok
    fd = None
    try:
        info = _leiro_info(leiro)
        if info is None:
            return None, "leiro_nem_lekerdezheto"
        if info.attributumok & _FILE_ATTRIBUTE_REPARSE_POINT:
            return None, "hivatkozas"
        if info.attributumok & _FILE_ATTRIBUTE_DIRECTORY:
            return None, "nem_regularis_fajl"
        if info.hivatkozasok != 1:
            return None, "merev_hivatkozas"
        if info.meret > max_bajt:
            return None, "tul_nagy_fajl"
        if bedobo:
            vegso = _vegso_utvonal(leiro)
            if not vegso:
                return None, "vegso_utvonal_ismeretlen"
            if not _azonos_mappa_e(os.path.dirname(vegso), bedobo):
                return None, "bedobon_kivuli_fajl"
        # The handle is handed over to the C runtime: from here on closing `fd`
        # also closes the handle, so the `finally` branch only closes `fd`.
        fd = msvcrt.open_osfhandle(leiro, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        leiro = None
        bajtok = _bajtok_olvasasa(fd, max_bajt)
    except OSError as e:
        return None, type(e).__name__
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        elif leiro is not None:
            try:
                import ctypes
                import ctypes.wintypes as wt
                ctypes.WinDLL("kernel32").CloseHandle(wt.HANDLE(leiro))
            except Exception:  # noqa: BLE001
                pass
    if len(bajtok) > max_bajt:
        return None, "tul_nagy_fajl"
    return bajtok, None


def _tartalek_beolvasas(utvonal, max_bajt, bedobo):
    """Fallback path when ctypes is not available: lstat → open → fstat IDENTITY.

    The `(st_dev, st_ino)` pair is the file's identity. If the open did follow
    a link after all, the identity of the opened file DIFFERS from what
    `os.lstat` saw — and we reject. In addition, AFTER opening we also require
    `os.path.realpath` to point inside the drop folder.
    """
    try:
        elozetes = os.lstat(utvonal)
    except OSError as e:
        return None, type(e).__name__
    if not stat.S_ISREG(elozetes.st_mode):
        return None, "nem_regularis_fajl"
    try:
        fd = os.open(utvonal, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError as e:
        return None, type(e).__name__
    try:
        adat = os.fstat(fd)
        if not stat.S_ISREG(adat.st_mode):
            return None, "nem_regularis_fajl"
        if (adat.st_dev, adat.st_ino) != (elozetes.st_dev, elozetes.st_ino):
            return None, "fajl_kicserelve"
        if getattr(adat, "st_nlink", 1) > 1:
            return None, "merev_hivatkozas"
        if adat.st_size > max_bajt:
            return None, "tul_nagy_fajl"
        if bedobo and not _azonos_mappa_e(
                os.path.dirname(os.path.realpath(utvonal)), bedobo):
            return None, "bedobon_kivuli_fajl"
        bajtok = _bajtok_olvasasa(fd, max_bajt)
    except OSError as e:
        return None, type(e).__name__
    finally:
        os.close(fd)
    if len(bajtok) > max_bajt:
        return None, "tul_nagy_fajl"
    return bajtok, None


def biztonsagos_beolvasas(utvonal, max_bajt=beallitasok.PISZKOZAT_MAX_BAJT,
                          bedobo=None):
    """Safe reading of the dropped file's BYTES. `(bajtok, ok)`.

    We do not move the file into the protected queue; we copy its CONTENT into
    a new file (M''''2). Reading must therefore provide the same protections
    the move used to — and, since finding K'''''1, more:

      * the open does NOT follow links (`FILE_FLAG_OPEN_REPARSE_POINT`), so a
        swap between the structural check and the read (TOCTOU) cannot bring
        in a foreign file either. If a link was opened anyway, we reject;
      * directory, hard link and size are checked on the OPENED handle — not
        on the path, which can be swapped in the meantime;
      * if the caller passes the `bedobo` folder, the handle's FINAL path must
        be inside that folder;
      * at most `max_bajt + 1` bytes are read, so an oversized file cannot eat
        up the memory.

    If the ctypes path is not available (not Windows, missing library), the
    fallback path compares the `(st_dev, st_ino)` identity between `os.lstat`
    and `os.fstat` — this catches the same swap, only after the fact.
    """
    if _VERSENY_HOROG is not None:
        # Set exclusively by the test's deterministic race simulation.
        _VERSENY_HOROG(utvonal)
    bajtok, ok = _ctypes_beolvasas(utvonal, max_bajt, bedobo)
    if ok == "nincs_ctypes_ut":
        return _tartalek_beolvasas(utvonal, max_bajt, bedobo)
    return bajtok, ok


def kanonikus_bajtok(tetel):
    """The re-serialized, canonical form of an ALREADY CHECKED item (K'''''1/b).

    PRINCIPLE: raw, unchecked bytes never go anywhere the untrusted side can
    read. The bytes of the dropped file are checked in memory against the
    full, strict schema, and what we write into the protected queue is NOT
    those bytes but canonical JSON rebuilt from the checked fields. Not a
    single byte of anything that fails the schema reaches the disk.

    2026-10-02: a follow / like item is serialized according to its OWN
    schema (for `kovetes` only the target field actually given goes in), and
    when read back from the queue it passes `ertelmez` the same way.
    """
    if tetel.get("tipus") == "kovetes":
        adat = {"verzio": 1, "tipus": "kovetes",
                "megjegyzes_tulajnak": tetel["megjegyzes_tulajnak"]}
        if tetel.get("cel_did") is not None:
            adat["cel_did"] = tetel["cel_did"]
        else:
            adat["cel_handle"] = tetel["cel_handle"]
        return json.dumps(adat, ensure_ascii=False,
                          sort_keys=True).encode("utf-8")
    if tetel.get("tipus") == "kedveles":
        return json.dumps({
            "verzio": 1, "tipus": "kedveles", "cel_uri": tetel["cel_uri"],
            "megjegyzes_tulajnak": tetel["megjegyzes_tulajnak"],
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return json.dumps({
        "verzio": 1,
        "tipus": tetel["tipus"],
        "szoveg": tetel["szoveg"],
        "valasz_cel_uri": tetel["valasz_cel_uri"],
        "langs": list(tetel["langs"]),
        "megjegyzes_tulajnak": tetel["megjegyzes_tulajnak"],
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")


def bajtokbol(bajtok, max_bajt=beallitasok.PISZKOZAT_MAX_BAJT):
    """Full, strict check of bytes held IN MEMORY. `(tetel, okok)`.

    Same flow as in `fajlbol`, just without a file: this way the dropped
    content goes through the full schema BEFORE it is written to disk.
    """
    if bajtok is None:
        return None, ["nem_olvashato"]
    if len(bajtok) > max_bajt:
        return None, ["tul_nagy_fajl"]
    if not bajtok.strip():
        return None, ["ures_fajl"]
    try:
        szoveg = bajtok.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["nem_utf8"]
    if szoveg.startswith("﻿"):
        szoveg = szoveg[1:]
    return ertelmez(szoveg)


def bedobo_valodi_mappa_e(mappa):
    """Is the drop folder a REAL directory, not a mount point? (M'''2)

    The untrusted user could in principle replace the `kozos\\ki` folder with
    a mount point (junction) pointing to the guard's OWN protected folder —
    the guard would then carry its own records awaiting approval into the
    queue and close them as unparseable drafts. Today's installation
    permissions do not allow this, but defense in depth is cheap: `os.lstat`
    flags the link, and then we simply take nothing in.
    """
    try:
        adat = os.lstat(mappa)
    except OSError:
        return False
    if not stat.S_ISDIR(adat.st_mode):
        return False
    if os.path.islink(mappa):
        return False
    # On Windows a mount point is not a symbolic link, but it does have a
    # reparse tag.
    return not (getattr(adat, "st_reparse_tag", 0) or 0)


def piszkozat_fajlok(mappa, max_darab=beallitasok.PISZKOZAT_KORONKENT_MAX,
                     kihagyando=()):
    """The `*.json` files of the drop folder, sorted by NAME.

    We do not descend into subdirectories, and only the `.json` extension is
    considered. At most `max_darab` files are returned per round (A''3): the
    untrusted side may drop ten thousand files, and we do not want every
    round to do needlessly many I/O operations.

    The `kihagyando` names (Kö'''2: a dropped FOLDER, a locked or denied
    file) are not even put on the list, so they do not use up the per-round
    budget either — without this, fifty directories with early-sorting names
    could fill the drop folder permanently, and no real draft would ever get
    in.
    """
    kihagyando = set(kihagyando or ())
    try:
        nevek = os.listdir(mappa)
    except OSError:
        return []
    ki = []
    for nev in sorted(nevek):
        if not nev.lower().endswith(".json"):
            continue
        if nev in kihagyando:
            continue
        ki.append(os.path.join(mappa, nev))
        if len(ki) >= max(1, int(max_darab)):
            break
    return ki
