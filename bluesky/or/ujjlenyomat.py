"""ujjlenyomat.py — SHA-256 fingerprint of the files in a folder.

Why it is needed (K''2). The guard's code is copied from the desktop of the
`agent-user` user into the `C:\\BskyGuard\\program\\` folder. According to the
threat model, however, the attacker can fully write the `agent-user` side. If
the owner copies without checking, an attacker could smuggle in their own code
during an update — and from then on THAT runs under the guard's user, with
access to the secrets folder.

This script does not solve that by itself. What it provides: a LIST in which
every file gets its own fingerprint, plus one combined fingerprint. This is
only worth something if the owner receives or records the fingerprint on a
CHANNEL that the `agent-user` side CANNOT rewrite afterwards — for example by
photographing it with their phone. The detailed, honest explanation is in the
"Update" chapter of `INSTALL.md`.

Usage:

    python ujjlenyomat.py                       # the script's own folder
    python ujjlenyomat.py C:\\BskyGuard\\program   # a given folder
    python ujjlenyomat.py <folder> --kiir lenyomat.txt
    python ujjlenyomat.py <folder> --osszevet lenyomat.txt
    python ujjlenyomat.py <folder> --csak-telepitendo --kiir lenyomat.txt

With the `--csak-telepitendo` switch we look at EXACTLY the files that are
copied into the `C:\\BskyGuard\\program\\` folder. Without it, the comparison
would report the documentation and sample files as "MISSING", and the check
could never say that EVERYTHING MATCHES.

`--osszevet` tells WHICH file changed, which is new and which disappeared.
Exit code: 0 = matches, 1 = there is a difference, 2 = the file is not readable.
"""

import argparse
import hashlib
import os
import sys

# We only look at files that ACTUALLY run or carry rules. `__pycache__` and
# `.pyc` are left out: Python generates those.
VIZSGALT_KITERJESZTESEK = (".py", ".json", ".md", ".minta")
KIHAGYOTT_MAPPAK = frozenset(("__pycache__", ".git", "tesztek"))
DARAB_MERET = 1024 * 1024

# M'''0/a: NOT every file is copied into the `program\` folder — documentation
# and sample files stay in the source folder. If the comparison looked at them
# too, every check would report four "MISSING" files, and with the script
# returning an error code every time, the real alarm would be ignored too.
#
# That is why the `--csak-telepitendo` switch exists: it looks at exactly the
# files that are actually copied into the `program\` folder, so the
# post-install comparison correctly reports that EVERYTHING MATCHES.
TELEPITENDO_KITERJESZTESEK = (".py",)
TELEPITENDO_FAJLOK = frozenset(("tilto_mintak.json", "szivesen_latnak.json"))


def _telepitendo_e(nev):
    """Is this file copied into the `C:\\BskyGuard\\program\\` folder?"""
    kis_nev = nev.lower()
    return (kis_nev.endswith(TELEPITENDO_KITERJESZTESEK)
            or kis_nev in TELEPITENDO_FAJLOK)


def fajl_sha256(utvonal):
    """SHA-256 fingerprint of a file, read in chunks."""
    kivonat = hashlib.sha256()
    with open(utvonal, "rb") as f:
        while True:
            darab = f.read(DARAB_MERET)
            if not darab:
                break
            kivonat.update(darab)
    return kivonat.hexdigest()


def mappa_lenyomatai(mappa, tesztek_is=False, csak_telepitendo=False):
    """`{relative_path: sha256}` — the examined files of the folder, sorted.

    The path is always stored with `/` separators and in lower case, so that
    the list can be compared across two machines.

    `csak_telepitendo`: only the files that are actually copied into the
    `program\\` folder (M'''0/a).
    """
    kihagyott = set(KIHAGYOTT_MAPPAK)
    if tesztek_is:
        kihagyott.discard("tesztek")
    lenyomatok = {}
    for gyoker, alkonyvtarak, fajlok in os.walk(mappa):
        alkonyvtarak[:] = sorted(a for a in alkonyvtarak
                                 if a.lower() not in kihagyott)
        for nev in sorted(fajlok):
            if csak_telepitendo:
                if not _telepitendo_e(nev):
                    continue
            elif not nev.lower().endswith(VIZSGALT_KITERJESZTESEK):
                continue
            teljes = os.path.join(gyoker, nev)
            relativ = os.path.relpath(teljes, mappa).replace("\\", "/")
            lenyomatok[relativ] = fajl_sha256(teljes)
    return dict(sorted(lenyomatok.items()))


def osszesitett(lenyomatok):
    """ONE fingerprint of the whole folder.

    The file name is hashed in too, not only the content: so renaming or
    removing a file also changes the combined value.
    """
    kivonat = hashlib.sha256()
    for nev, lenyomat in sorted(lenyomatok.items()):
        kivonat.update(nev.encode("utf-8"))
        kivonat.update(b"\0")
        kivonat.update(lenyomat.encode("ascii"))
        kivonat.update(b"\n")
    return kivonat.hexdigest()


def szoveges_alak(lenyomatok):
    """The written/compared form: `<sha256>  <relative path>` per line."""
    sorok = ["%s  %s" % (lenyomat, nev)
             for nev, lenyomat in sorted(lenyomatok.items())]
    sorok.append("")
    sorok.append("OSSZESITETT  %s" % osszesitett(lenyomatok))
    sorok.append("FAJLOK       %d" % len(lenyomatok))
    return "\n".join(sorok) + "\n"


def beolvas(utvonal):
    """Read back a previously written list. `{relative_path: sha256}`."""
    lenyomatok = {}
    with open(utvonal, "r", encoding="utf-8") as f:
        for sor in f:
            sor = sor.strip()
            # Empty lines, comments and the two summary lines are not fingerprints.
            if not sor or sor.startswith("#") or \
                    sor.startswith("OSSZESITETT") or sor.startswith("FAJLOK"):
                continue
            darabok = sor.split(None, 1)
            if len(darabok) != 2:
                continue
            lenyomatok[darabok[1].strip()] = darabok[0].strip().lower()
    return lenyomatok


def osszevetes(mostani, regi):
    """`(valtozott, uj, eltunt)` — (changed, new, missing): three sorted lists."""
    valtozott = sorted(nev for nev in mostani
                       if nev in regi and mostani[nev] != regi[nev])
    uj = sorted(set(mostani) - set(regi))
    eltunt = sorted(set(regi) - set(mostani))
    return valtozott, uj, eltunt


def main(argv=None, kiir=print):
    ertelmezo = argparse.ArgumentParser(
        description="SHA-256 fingerprint of the files in a folder")
    ertelmezo.add_argument(
        "mappa", nargs="?", default=None,
        help="the folder to examine (default: the script's own folder)")
    ertelmezo.add_argument("--kiir", default=None,
                           help="also write the list to this file")
    ertelmezo.add_argument("--osszevet", default=None,
                           help="compare with a previously written list")
    ertelmezo.add_argument("--tesztek-is", action="store_true",
                           help="also include the tesztek\\ folder")
    ertelmezo.add_argument(
        "--csak-telepitendo", dest="csak_telepitendo", action="store_true",
        help="only the files that are copied into the program\\ folder "
             "(.py + the two rule files)")
    args = ertelmezo.parse_args(argv)

    mappa = os.path.abspath(
        args.mappa or os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(mappa):
        kiir("No such folder: %s" % mappa)
        return 2

    lenyomatok = mappa_lenyomatai(mappa, tesztek_is=args.tesztek_is,
                                  csak_telepitendo=args.csak_telepitendo)
    szoveg = szoveges_alak(lenyomatok)
    kiir("Folder: %s" % mappa)
    if args.csak_telepitendo:
        kiir("(only the files copied into the program folder)")
    kiir("")
    kiir(szoveg.rstrip("\n"))

    if args.kiir:
        with open(args.kiir, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Folder: %s\n" % mappa)
            f.write(szoveg)
        kiir("")
        kiir("The list was written to: %s" % os.path.abspath(args.kiir))

    if not args.osszevet:
        return 0

    try:
        regi = beolvas(args.osszevet)
    except OSError:
        kiir("Cannot read the earlier list: %s" % args.osszevet)
        return 2

    valtozott, uj, eltunt = osszevetes(lenyomatok, regi)
    kiir("")
    kiir("--- COMPARISON with the earlier list ---")
    if not (valtozott or uj or eltunt):
        kiir("EVERYTHING MATCHES. Not a single file has changed.")
        return 0
    for cim, lista in (("CHANGED", valtozott), ("NEW", uj),
                       ("MISSING", eltunt)):
        if lista:
            kiir("%s (%d):" % (cim, len(lista)))
            for nev in lista:
                kiir("  - " + nev)
    kiir("")
    kiir("Review these changes BEFORE you copy them into the program folder.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
