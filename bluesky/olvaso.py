"""olvaso.py — the INCOMING side. Started by hand; no watcher, no daemon.

Fetches the notifications (and, on request, the timeline / one author's posts),
SANITIZES everything, attaches suspicion flags, and writes it to inbox.jsonl.

NOTHING from the CONTENT is printed to the console — only counts. The content
is shown by mutat.py, inside a frame.

Usage:
    python olvaso.py                      # notifications only
    python olvaso.py --idovonal 20        # + the last 20 posts of followed accounts
    python olvaso.py --szerzo someone.bsky.social 10
"""

import argparse
import hashlib
import sys

import bsky_api
import szures
import tarolas

# The notification types that are let into the inbox at all.
ERTESITES_TIPUSOK = ("mention", "reply", "follow", "like", "quote", "repost")


def tetel_id(uri, tipus):
    """Stable, short identifier. The same uri with a different type is a separate item."""
    nyers = (uri or "") + "|" + (tipus or "")
    return hashlib.sha256(nyers.encode("utf-8")).hexdigest()[:16]


def _bot_cimkes(szerzo):
    for cimke in (szerzo or {}).get("labels") or []:
        if (cimke.get("val") or "").lower() == "bot":
            return True
    return False


# A1: the depth and size of an embed coming from the attacker's record are capped.
EMBED_MAX_MELYSEG = 20
ALT_MAX_DARAB = 5
ALT_MAX_HOSSZ = 400


def _kepek_es_altok(embed):
    """(van_kep, alt_texts) — the CONTENT of an image/embed is never downloaded.
    We only record that it exists, plus the alt text (sanitized).

    The traversal DEPTH and the NUMBER of collected alts are capped (A1):
    otherwise a deeply nested embed would cause a RecursionError, and one with
    many alts would flood the output of mutat.py."""
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
    """Turns a raw item into our own, safe inbox form.

    This function is PURE: no network, no files — so it is testable.
    """
    nyers_szoveg = (rekord or {}).get("text", "") or ""
    szoveg = szures.szanitalas(nyers_szoveg)
    # The frame markers are neutralized already here, so that even the stored
    # text cannot "break out" of any later display.
    szoveg = szures.keret_veszelytelenites(szoveg)
    okok = szures.injection_jelolek(nyers_szoveg) + szures.injection_jelolek(szoveg)

    valasz = (rekord or {}).get("reply") or {}
    szulo = valasz.get("parent") or {}
    gyoker = valasz.get("root") or {}

    van_kep, alt = _kepek_es_altok(embed if embed is not None else (rekord or {}).get("embed"))
    if alt:
        okok += szures.injection_jelolek(alt)

    # M4: EVERY metadata field is made single-line + sanitized + frame-neutralized.
    # These fields come from the attacker's OWN record (record.reply.parent.uri
    # etc.), so they may contain arbitrary text — and mutat.py prints them
    # BEFORE OPENING the frame.
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
    """A timeline or author-feed item (CHANGE 1)."""
    poszt = feed_tetel.get("post") or {}
    return normalizal(
        poszt.get("uri"), poszt.get("cid"), tipus,
        poszt.get("author"), poszt.get("record"),
        poszt.get("indexedAt"), poszt.get("embed"),
    )


def ujak_szurese(uj_tetelek, meglevo_tetelek):
    """Dedup by `id` (uri+type). Existing items are not overwritten."""
    meglevo_idk = {t.get("id") for t in meglevo_tetelek}
    ujak = []
    for t in uj_tetelek:
        if t["id"] in meglevo_idk:
            continue
        meglevo_idk.add(t["id"])
        ujak.append(t)
    return ujak


def main(argv=None):
    tarolas.konzol_utf8()
    ertelmezo = argparse.ArgumentParser(description="Bluesky incoming reader")
    ertelmezo.add_argument("--limit", type=int, default=50,
                           help="how many notifications to fetch (default 50)")
    ertelmezo.add_argument("--idovonal", type=int, default=0,
                           help="the last N posts of followed accounts (default 0 = off)")
    ertelmezo.add_argument("--szerzo", nargs="+", default=None,
                           metavar=("HANDLE", "N"),
                           help="the last N posts of a given account (default N=10)")
    args = ertelmezo.parse_args(argv)

    kulcs = tarolas.bsky_kulcs_betolt()
    session = bsky_api.belepes(kulcs["BSKY_HANDLE"], kulcs["BSKY_APP_PASSWORD"])
    token = session["accessJwt"]

    uj = []
    valasz = bsky_api.ertesitesek(token, limit=args.limit)
    for e in valasz.get("notifications", []):
        if e.get("reason") not in ERTESITES_TIPUSOK:
            continue
        uj.append(ertesitesbol(e))

    if args.idovonal > 0:
        feed = bsky_api.idovonal(token, limit=args.idovonal)
        for t in feed.get("feed", []):
            uj.append(feed_tetelbol(t, "idovonal"))

    if args.szerzo:
        handle = args.szerzo[0]
        darab = int(args.szerzo[1]) if len(args.szerzo) > 1 else 10
        feed = bsky_api.szerzo_posztjai(token, handle, limit=darab)
        for t in feed.get("feed", []):
            uj.append(feed_tetelbol(t, "szerzo_poszt"))

    meglevo = tarolas.jsonl_olvas(tarolas.INBOX)
    ujak = ujak_szurese(uj, meglevo)
    for t in ujak:
        tarolas.jsonl_hozzafuz(tarolas.INBOX, t)

    gyanus_db = sum(1 for t in ujak if t["gyanus"])
    # NO content on the console — only numbers.
    print(f"Fetched: {len(uj)} item(s). New in the inbox: {len(ujak)}. "
          f"Of these with suspicion flags: {gyanus_db}. (Content: python mutat.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
