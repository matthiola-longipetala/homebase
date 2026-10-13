"""olvaso_mag.py — the PURE part of the INCOMING side.

This is the normalizing core of the existing, tested `olvaso.py`, with
unchanged rules, just without the network and the CLI — so that the `or\\`
folder can be installed and tested on its own.

What we guard here (findings of the two reviews):
  * EVERY metadata field is made single-line + sanitized + frame-neutralized —
    they come from the attacker's own record, and the display prints them
    BEFORE the frame is OPENED (M4);
  * the depth of the embed traversal and the number of alt texts are bounded (A1);
  * the `displayName` field is NEVER stored.
"""

import hashlib

import szures

ERTESITES_TIPUSOK = ("mention", "reply", "follow", "like", "quote", "repost")

EMBED_MAX_MELYSEG = 20
ALT_MAX_DARAB = 5
ALT_MAX_HOSSZ = 400


def tetel_id(uri, tipus):
    """Stable, short identifier. The same uri with another type is a separate item."""
    nyers = (uri or "") + "|" + (tipus or "")
    return hashlib.sha256(nyers.encode("utf-8")).hexdigest()[:16]


def _bot_cimkes(szerzo):
    for cimke in (szerzo or {}).get("labels") or []:
        if (cimke.get("val") or "").lower() == "bot":
            return True
    return False


def _kepek_es_altok(embed):
    """(van_kep, alt_szovegek) — the CONTENT of an image/embed is never downloaded."""
    van = False
    altok = []

    def bejar(objektum, melyseg=0):
        nonlocal van
        if melyseg > EMBED_MAX_MELYSEG or len(altok) >= ALT_MAX_DARAB:
            return
        if isinstance(objektum, dict):
            tipus = objektum.get("$type", "")
            if "images" in tipus or "video" in tipus or "external" in tipus:
                van = True
            if "alt" in objektum and isinstance(objektum["alt"], str):
                van = True
                if len(altok) < ALT_MAX_DARAB:
                    altok.append(objektum["alt"])
            for ertek in objektum.values():
                bejar(ertek, melyseg + 1)
        elif isinstance(objektum, list):
            for ertek in objektum:
                bejar(ertek, melyseg + 1)

    bejar(embed)
    tiszta_alt = " | ".join(
        szures.egysoros(a, 200) for a in altok[:ALT_MAX_DARAB] if a)
    return van, tiszta_alt[:ALT_MAX_HOSSZ]


def normalizal(uri, cid, tipus, szerzo, rekord, ido, embed=None):
    """From a raw item, our own safe inbox form (pure)."""
    nyers_szoveg = (rekord or {}).get("text", "") or ""
    szoveg = szures.szanitalas(nyers_szoveg)
    szoveg = szures.keret_veszelytelenites(szoveg)
    okok = szures.injection_jelolek(nyers_szoveg) + szures.injection_jelolek(szoveg)

    valasz = (rekord or {}).get("reply") or {}
    szulo = valasz.get("parent") or {}
    gyoker = valasz.get("root") or {}

    van_kep, alt = _kepek_es_altok(
        embed if embed is not None else (rekord or {}).get("embed"))
    if alt:
        okok += szures.injection_jelolek(alt)

    return {
        "id": tetel_id(uri, tipus),
        "tipus": szures.egysoros(tipus, 40),
        "uri": szures.uri_veszelytelenites(uri),
        "cid": szures.cid_veszelytelenites(cid),
        "szerzo_handle": szures.egysoros((szerzo or {}).get("handle", ""), 120),
        "szerzo_did": szures.egysoros((szerzo or {}).get("did", ""), 120),
        "szerzo_bot_cimkes": _bot_cimkes(szerzo),
        "ido": szures.egysoros(ido or "", 40),
        "szoveg": szoveg,
        "szulo_uri": szures.uri_veszelytelenites(szulo.get("uri", "")),
        "szulo_cid": szures.cid_veszelytelenites(szulo.get("cid", "")),
        "gyoker_uri": szures.uri_veszelytelenites(gyoker.get("uri", "")),
        "gyoker_cid": szures.cid_veszelytelenites(gyoker.get("cid", "")),
        "van_kep": van_kep,
        "alt": alt,
        "gyanus": bool(okok),
        "gyanu_okok": sorted(set(okok)),
        "olvasott": False,
    }


def ertesitesbol(ertesites):
    return normalizal(
        ertesites.get("uri"), ertesites.get("cid"),
        ertesites.get("reason", "ismeretlen"),
        ertesites.get("author"), ertesites.get("record"),
        ertesites.get("indexedAt"),
    )


def feed_tetelbol(feed_tetel, tipus):
    poszt = feed_tetel.get("post") or {}
    return normalizal(
        poszt.get("uri"), poszt.get("cid"), tipus,
        poszt.get("author"), poszt.get("record"),
        poszt.get("indexedAt"), poszt.get("embed"),
    )


def ujak_szurese(uj_tetelek, meglevo_idk):
    """Dedup by `id` (uri+type). `meglevo_idk` is a set — it is MODIFIED."""
    ujak = []
    for t in uj_tetelek:
        if t["id"] in meglevo_idk:
            continue
        meglevo_idk.add(t["id"])
        ujak.append(t)
    return ujak
