"""jogok.py — STARTUP SELF-CHECK: are the NTFS permissions right?

The guard's protection does not rest on its good intentions, but on the fact
that the untrusted Windows user (`agent-user`, who runs Claude) has NO access
to the secret, and can NOT write the program, the state or the outgoing folder.

This module CHECKS that; it does not set it. If anything is off, the guard
does not start.

FAIL-CLOSED, ALLOWLIST-BASED: we do not check whether `agent-user` is listed
(it can be renamed, and can come in through groups too: "Users",
"Everyone"), but whether anyone OUTSIDE the listed identities gets a right.
An unknown permission token also counts as "grants a right" — the stricter
reading.

The permission query is INJECTABLE (`lekero` parameter) so that it can be
tested; the production implementation parses the output of `icacls`.
"""

import os
import re
import subprocess

import beallitasok


class JogHiba(RuntimeError):
    """Wrong permissions or an ACL that cannot be queried — the guard does not start."""


# --- 1. Parsing the icacls output ----------------------------------------

# What grants READ. Unknown tokens are NOT listed here; they are handled separately.
OLVASO_JOGOK = {"F", "M", "RX", "R", "RD", "REA", "RA", "GR", "GE", "X", "GA"}
# What grants WRITE (or delete, permission change).
IRO_JOGOK = {"F", "M", "W", "WD", "AD", "WEA", "WA", "WDAC", "WO", "D", "DE",
             "DC", "GW", "GA"}
# Harmless markers that grant NO access at all (Kö4).
# Without these, every unusual but harmless entry would have stopped the
# guard, with a misleading message on top ("received unauthorized read access").
SEMLEGES_JOGOK = {
    "S",    # synchronize — handle synchronization only
    "RC",   # read control — reading the access control list
    "NW",   # no write-up (integrity label)
    "NR",   # no read-up (integrity label)
    "NX",   # no execute-up (integrity label)
    "AS",   # access system security (audit settings, not data access)
}

_SOR_MINTA = re.compile(r"^(?P<azonosito>.+?):(?P<jogok>(?:\([^()]*\))+)\s*$")


def _normalizal_identitas(nyers):
    """`PC\\agent-user` → `agent-user`; `BUILTIN\\Rendszergazdák` → `rendszergazdk`.

    Non-ASCII letters are dropped: Hungarian-language Windows writes
    "Rendszergazdák" (Administrators), and the console code page may mangle
    the accent — it is still the same group.
    """
    nev = (nyers or "").strip().strip('"')
    if "\\" in nev:
        nev = nev.rsplit("\\", 1)[1]
    nev = nev.casefold()
    return re.sub(r"[^a-z0-9_-]", "", nev)


ENGEDETT_NORMALT = frozenset(
    _normalizal_identitas(n) for n in beallitasok.ENGEDETT_IDENTITASOK)


def bejegyzesek_ertelmezese(kimenet, utvonal):
    """From the output of `icacls <folder>`: `[(identitas, [jog, …], tiltas_e)]`.

    Unparseable lines are NOT silently skipped: we raise `JogHiba`, because
    an unrecognized ACE may be exactly what opens the hole.
    """
    bejegyzesek = []
    for nyers_sor in (kimenet or "").splitlines():
        sor = nyers_sor.strip()
        if not sor:
            continue
        # Locale-specific icacls summary lines (English / Hungarian Windows).
        if sor.startswith("Successfully") or sor.startswith("Sikeresen"):
            continue
        if sor.lower().startswith(utvonal.lower()):
            sor = sor[len(utvonal):].strip()
            if not sor:
                continue  # the line held only the path
        egyezes = _SOR_MINTA.match(sor)
        if not egyezes:
            raise JogHiba("Cannot parse a line of the icacls output — "
                          "fail-closed, not starting.")
        azonosito = egyezes.group("azonosito")
        jelolok = re.findall(r"\(([^()]*)\)", egyezes.group("jogok"))
        jogok = []
        tiltas = False
        for jelolo in jelolok:
            for token in jelolo.split(","):
                token = token.strip()
                if not token:
                    continue
                nagy = token.upper()
                if nagy == "DENY":
                    tiltas = True
                elif nagy in ("I", "OI", "CI", "IO", "NP"):
                    continue  # inheritance markers, not rights
                else:
                    jogok.append(nagy)
        bejegyzesek.append((azonosito, jogok, tiltas))
    if not bejegyzesek:
        raise JogHiba("icacls returned not a single permission line — "
                      "fail-closed, not starting.")
    return bejegyzesek


def icacls_lekero(utvonal):
    """PRODUCTION query: `icacls <utvonal>`. Error → JogHiba (fail-closed)."""
    try:
        folyamat = subprocess.run(
            ["icacls", utvonal], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise JogHiba("icacls cannot be run (%s) — not starting."
                      % type(e).__name__) from None
    if folyamat.returncode != 0:
        raise JogHiba("icacls returned an error on the %s folder — "
                      "not starting." % os.path.basename(utvonal))
    nyers = folyamat.stdout or b""
    for kodolas in ("oem", "mbcs", "utf-8"):
        try:
            return nyers.decode(kodolas, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    raise JogHiba("The icacls output cannot be decoded — not starting.")


# --- 2. The rules ---------------------------------------------------------

def _idegen_jogok(bejegyzesek, jog_halmaz, engedett=None):
    """Identities OUTSIDE THE ALLOWLIST that get something from `jog_halmaz`.

    `(idegenek, ismeretlen_tokenesek)`. An unknown permission token still
    stops us (the stricter direction), but it gets a SEPARATE message (Kö4):
    we do not claim "it received a right" when we merely do not recognize
    the marker.
    """
    engedett = ENGEDETT_NORMALT if engedett is None else engedett
    idegenek = []
    ismeretlenek = []
    for azonosito, jogok, tiltas in bejegyzesek:
        if tiltas:
            continue  # DENY grants no right
        if _normalizal_identitas(azonosito) in engedett:
            continue
        ismeretlen = [j for j in jogok
                      if j not in OLVASO_JOGOK and j not in IRO_JOGOK
                      and j not in SEMLEGES_JOGOK]
        if ismeretlen:
            ismeretlenek.append("%s (%s)" % (azonosito.strip(),
                                             ", ".join(sorted(set(ismeretlen)))))
        elif any(j in jog_halmaz for j in jogok):
            idegenek.append(azonosito.strip())
    return sorted(set(idegenek)), sorted(set(ismeretlenek))


# (folder attribute, human-readable name, "what is forbidden") triples.
# M''1: the root and the `kozos` folder are checked too. If the untrusted
# user got Modify on the root, that would also give them FILE_DELETE_CHILD,
# and they could RENAME the `allapot` folder — without having any right on
# the `allapot` folder itself. The subfolders of `allapot` are checked as
# well, because the guard creates them.
#
# The `kozos\ki` (drop) folder follows a SEPARATE rule (M'''2, K'''4): the
# untrusted user is deliberately ALLOWED TO WRITE there, so the usual "no
# outsider may write" rule would be meaningless. What we do require:
#   * the folder's OWNER must be an allowed identity (because of the owner's
#     implicit `WRITE_DAC` right, a change of owner is an attack in itself);
#   * apart from the untrusted user and the allowed identities, nobody else
#     may get write access;
#   * there must be a DENY entry for deletion on the untrusted user —
#     otherwise they can empty and DELETE the folder itself, after which the
#     guard no longer starts, failing with "missing folder: ki" (a silent
#     denial of service that only an administrator can repair).
#
# K'''''1/c — the `varo`, `feldolgozott` and `zart` folders follow the
# "semmi" (nothing) rule: the untrusted user may have NO right at all on
# them, not even read. Everything derived from the content of DROPPED files
# goes here; if a dropped file were not what it appears to be, its content
# still cannot be read back from here by the untrusted side.
# This is the third protection layer, INDEPENDENT of the code's flow.
SZABALYOK = (
    ("gyoker", "C:\\BskyGuard (root)", "ir"),
    ("titok", "titok", "olvas"),
    ("program", "program", "ir"),
    ("allapot", "allapot", "ir"),
    ("elonezetek", "allapot\\elonezetek", "ir"),
    ("feldolgozott", "allapot\\feldolgozott", "semmi"),
    ("varo", "allapot\\varo", "semmi"),
    ("zart", "allapot\\zart", "semmi"),
    ("archiv", "allapot\\archiv", "ir"),
    ("kozos", "kozos", "ir"),
    ("be", "kozos\\be", "ir"),
    ("ki", "kozos\\ki", "bedobo"),
)

# The untrusted user may also appear on the drop folder — so for the
# "outsider" check they are added to the allowed identities.
BEDOBO_ENGEDETT_NORMALT = frozenset(
    ENGEDETT_NORMALT | {_normalizal_identitas(
        beallitasok.NEM_MEGBIZHATO_FELHASZNALO)})

# The permission tokens that grant deletion: the DENY entry on the drop
# folder must contain one of these.
TORLES_JOGOK = frozenset(("DE", "D", "F", "M", "GA"))


def _torles_tiltva_e(bejegyzesek, felhasznalo):
    """Is there an entry DENYING DELETION to the untrusted user?

    The line produced by `icacls "…\\ki" /deny "<felhasznalo>:(DE)"` looks
    like this in the output: `…\\ki <MACHINE>\\<felhasznalo>:(DENY)(DE)`.
    """
    cel = _normalizal_identitas(felhasznalo)
    for azonosito, jogok, tiltas in bejegyzesek:
        if not tiltas:
            continue
        if _normalizal_identitas(azonosito) != cel:
            continue
        if any(j in TORLES_JOGOK for j in jogok):
            return True
    return False

# These folders may be missing even when everything is fine (the guard
# creates them in the first round). But if they DO EXIST, they are checked.
# `varo`, `feldolgozott` and `zart` were LEFT OUT of here (K'''''1/c): if the
# guard created them, they would INHERIT the permissions of the `allapot`
# folder — where the untrusted user has read access. Missing folder → the
# guard does not start, and says in the state file what has to be created.
NEM_KOTELEZO = frozenset(("elonezetek", "archiv"))

# The permission set for the "semmi" (nothing) rule: read OR write, i.e. any
# access at all.
MINDEN_JOG = frozenset(OLVASO_JOGOK | IRO_JOGOK)


def _powershell_tulajdonos(utvonal):
    """PRODUCTION owner query. `icacls` does not print this.

    The path is passed in an ENVIRONMENT VARIABLE, not as a command-line
    argument (K'''1). By the documented behavior of `powershell.exe`, EVERY
    character written after the string form of `-Command` is appended to the
    command TEXT, so the earlier `-args <utvonal>` form was a syntax error for
    PowerShell, and the guard did not complete a single round on the real
    machine. With the environment variable form there is no quoting or
    command-line parsing issue at all.
    """
    kornyezet = dict(os.environ)
    kornyezet["BSKYGUARD_VIZSGALT_UT"] = utvonal
    try:
        folyamat = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; "
             "(Get-Acl -LiteralPath $env:BSKYGUARD_VIZSGALT_UT).Owner"],
            capture_output=True, timeout=60, check=False, env=kornyezet)
    except (OSError, subprocess.SubprocessError) as e:
        raise JogHiba("The owner cannot be queried (%s) — not starting."
                      % type(e).__name__) from None
    if folyamat.returncode != 0:
        raise JogHiba("The owner query returned an error on the %s "
                      "folder — not starting." % os.path.basename(utvonal))
    tulajdonos = (folyamat.stdout or b"").decode("oem", errors="replace").strip()
    if not tulajdonos:
        # `Get-Acl` reports some errors as NON-terminating errors, and then
        # the process returns with exit code zero and empty output. The real
        # smoke test caught this. FAIL-CLOSED: empty answer = we do not know.
        raise JogHiba("The owner query gave an empty answer on the %s "
                      "folder — not starting." % os.path.basename(utvonal))
    return tulajdonos


def jog_ellenorzes(kornyezet, lekero=icacls_lekero, tulajdonos_lekero=None):
    """The full self-check. Returns a list of ERRORS (empty = all good).

    It does not raise for wrong permissions — the caller prints them and
    reports them in the state file. It raises ONLY if the query itself
    fails (FAIL-CLOSED: what we cannot check, we do not accept).
    """
    tulajdonos_lekero = tulajdonos_lekero or _powershell_tulajdonos
    hibak = []
    for mappa in kornyezet.letezo_mappak():
        if not os.path.isdir(mappa):
            hibak.append("missing folder: %s" % os.path.basename(mappa))
    if hibak:
        return hibak

    for mezo, nev, mit in SZABALYOK:
        utvonal = getattr(kornyezet, mezo)
        if not os.path.isdir(utvonal):
            if mezo in NEM_KOTELEZO:
                continue  # the guard has not created it yet — nothing to check
            hibak.append("missing folder: %s" % nev)
            continue

        # 1. The OWNER. The owner has an implicit `WRITE_DAC` right: it can
        #    give itself access back at any time, even if the access control
        #    list looks clean (M''1).
        tulajdonos = tulajdonos_lekero(utvonal)
        if not tulajdonos:
            hibak.append("the owner of the %s folder cannot be determined — "
                         "not starting, as a precaution." % nev)
        elif _normalizal_identitas(tulajdonos) not in ENGEDETT_NORMALT:
            hibak.append(
                "the OWNER of the %s folder is not an allowed identity: %s — "
                "the owner can give itself access back at any time."
                % (nev, tulajdonos.strip()))

        # 2. The access control list.
        bejegyzesek = bejegyzesek_ertelmezese(lekero(utvonal), utvonal)
        if mit == "olvas":
            halmaz = OLVASO_JOGOK
        elif mit == "semmi":
            halmaz = MINDEN_JOG
        else:
            halmaz = IRO_JOGOK
        engedett = (BEDOBO_ENGEDETT_NORMALT if mit == "bedobo"
                    else ENGEDETT_NORMALT)
        idegenek, ismeretlenek = _idegen_jogok(bejegyzesek, halmaz, engedett)
        if idegenek:
            jog_neve = {"olvas": "read", "semmi": "any"}.get(
                mit, "write")
            hibak.append(
                "the %s folder: unauthorized %s access granted to: %s"
                % (nev, jog_neve, ", ".join(idegenek)))

        # 3. On the drop folder the DELETE deny is mandatory too (K'''4).
        #    Without it the untrusted user can delete the folder itself, and
        #    from then on the guard does not start ("missing folder: ki").
        if mit == "bedobo" and not _torles_tiltva_e(
                bejegyzesek, beallitasok.NEM_MEGBIZHATO_FELHASZNALO):
            hibak.append(
                "the %s folder is missing the entry DENYING DELETION to the "
                "untrusted user — without it they can delete the folder "
                "itself, and the guard will no longer start. See "
                "INSTALL.md, chapter 3 (/deny line)." % nev)
        if ismeretlenek:
            hibak.append(
                "found an unknown permission marker on the %s folder (%s), "
                "so not starting, as a precaution — show this line "
                "to Claude." % (nev, "; ".join(ismeretlenek)))
    return hibak


# --- 3. WARNING: is the untrusted user an administrator? ------------------
#
# This is the MOST IMPORTANT residual risk. The NTFS permissions give full
# access to the `Administrators` group (without that the folder would be
# unmanageable). If the untrusted user is a MEMBER of this group, then with
# elevation (UAC) they can read the secret and rewrite the state at any
# time — the protection then only closes the path WITHOUT ELEVATION.
#
# We cannot prevent this, but we make it VISIBLE. Not fail-closed: a group
# query that cannot be checked should not paralyze the guard.

def _powershell_admin_tagok():
    folyamat = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-LocalGroupMember -SID S-1-5-32-544 | "
         "ForEach-Object { $_.Name }"],
        capture_output=True, timeout=60, check=False)
    return (folyamat.stdout or b"").decode("oem", errors="replace")


def admin_tagsag_figyelmeztetes(lekero=None,
                                felhasznalo=beallitasok.NEM_MEGBIZHATO_FELHASZNALO):
    """`None` if all is well; otherwise a warning sentence."""
    try:
        kimenet = (lekero or _powershell_admin_tagok)()
    except Exception:  # noqa: BLE001 — a failed query does not stop us
        return ("membership of the administrators group cannot be checked — "
                "check by hand that the untrusted user is not in it.")
    cel = _normalizal_identitas(felhasznalo)
    for sor in (kimenet or "").splitlines():
        if sor.strip() and _normalizal_identitas(sor) == cel:
            return ("WARNING: the untrusted user is a MEMBER of the "
                    "Administrators group — with elevation they can bypass "
                    "the folder permissions. Remove them from the group (see "
                    "INSTALL.md, step 1/d).")
    return None
