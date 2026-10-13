"""bejovo.py — the INCOMING pipeline: notifications → `kozos\\be\\inbox.jsonl`.

The guard writes, the untrusted side only reads. The content NEVER goes to
the console or the log — only counts; the framing on Claude's side is done by
`mutat.py`.
"""

import os

import beallitasok
import olvaso_mag
import tarolas_or


def meglevo_idk(inbox_utvonal):
    """The identifiers of the already stored items (for dedup).

    The rotated (`.1`, `.2`, …) files are checked too, so that the same items
    do not get in again after a rotation.
    """
    idk = set()
    utvonalak = [inbox_utvonal]
    for i in range(1, beallitasok.INBOX_FORGATAS_DARAB + 1):
        utvonalak.append("%s.%d" % (inbox_utvonal, i))
    for ut in utvonalak:
        if not os.path.isfile(ut):
            continue
        for sor in tarolas_or.jsonl_olvas(ut):
            azon = sor.get("id")
            if azon:
                idk.add(azon)
    return idk


def gyujtes(ertesitesek_valasz, idovonal_valasz=None):
    """Normalized inbox items from the raw network responses (pure)."""
    ki = []
    for e in (ertesitesek_valasz or {}).get("notifications") or []:
        if e.get("reason") not in olvaso_mag.ERTESITES_TIPUSOK:
            continue
        ki.append(olvaso_mag.ertesitesbol(e))
    for t in (idovonal_valasz or {}).get("feed") or []:
        ki.append(olvaso_mag.feed_tetelbol(t, "idovonal"))
    return ki


def irasa(kornyezet, tetelek):
    """Appending the NEW items, with dedup + size-based rotation.

    Returns: `(uj_darab, gyanus_darab, forgattunk)` (new count, suspicious count, rotated).
    """
    if not os.path.isdir(kornyezet.be):
        raise OSError("the kozos\\be folder does not exist.")
    forgattunk = tarolas_or.forgatas(
        kornyezet.inbox, beallitasok.INBOX_MAX_BAJT,
        beallitasok.INBOX_FORGATAS_DARAB)
    idk = meglevo_idk(kornyezet.inbox)
    ujak = olvaso_mag.ujak_szurese(tetelek, idk)
    for t in ujak:
        tarolas_or.jsonl_hozzafuz(kornyezet.inbox, t)
    gyanus = sum(1 for t in ujak if t.get("gyanus"))
    return len(ujak), gyanus, forgattunk
