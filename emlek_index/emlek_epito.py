# -*- coding: utf-8 -*-
"""Associative recall — the index builder ("építő" = builder). Idempotent: an unchanged chunk is not embedded again.

Run it from its own venv (that is where torch / sentence-transformers live):
  python emlek_epito.py             # base layer
  python emlek_epito.py --archiv    # + archived journals (a separate index)
  python emlek_epito.py --csendes   # quiet: no progress bar (for a nightly scheduled rebuild)
Output: <index_dir>/<model>[_archiv]/{vektorok.npy, darabok.jsonl, info.json} (vectors, chunks, info);
cache: <index_dir>/cache_<model>.npz
"""
import argparse
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emlek_kozos as k  # noqa: E402

ALAP_MODELL = "intfloat/multilingual-e5-small"


def modell_slug(nev):
    return nev.split("/")[-1]


def passage_elotag(modell):
    return "passage: " if "e5" in modell.lower() else ""


def epit(modell=ALAP_MODELL, archiv=False, csendes=False):
    t0 = time.time()
    slug = modell_slug(modell)
    cel = os.path.join(k.INDEX_DIR, slug + ("_archiv" if archiv else ""))
    os.makedirs(cel, exist_ok=True)
    cache_ut = os.path.join(k.INDEX_DIR, f"cache_{slug}.npz")

    cache = {}
    if os.path.exists(cache_ut):
        # with + an np.array copy: npz keeps the file open lazily, and on Windows the
        # os.replace at the end would then fail with "Access is denied"
        with np.load(cache_ut) as z:
            cache = dict(zip(z["kulcsok"].tolist(), np.array(z["vektorok"])))

    darabok, titok_db, fajl_db = [], 0, 0
    for reteg, ut in k.forras_fajlok(archiv):
        meta, ds, ti = k.darabol(ut, reteg)
        fajl_db += 1
        titok_db += ti
        for d in ds:
            beagy = (d["cimsor"] + ": " + d["szoveg"]) if d["cimsor"] else d["szoveg"]
            darabok.append({**meta, "fajl": k.rovid_ut(meta["fajl"]), "sor": d["sor"],
                            "szoveg": d["szoveg"], "_beagy": beagy,
                            "_kulcs": k.szoveg_hash(modell, beagy)})

    uj = [d for d in darabok if d["_kulcs"] not in cache]
    if uj:
        from sentence_transformers import SentenceTransformer
        m = SentenceTransformer(modell, device="cpu")
        elo = passage_elotag(modell)
        vek = m.encode([elo + d["_beagy"] for d in uj], batch_size=32,
                       normalize_embeddings=True, show_progress_bar=not csendes,
                       convert_to_numpy=True)
        for d, v in zip(uj, vek):
            cache[d["_kulcs"]] = v.astype(np.float16)

    matrix = np.stack([cache[d["_kulcs"]] for d in darabok]).astype(np.float16)
    # atomic write: .tmp first, then replace
    def _csere(ut, ir):
        tmp = ut + ".tmp"
        ir(tmp)
        os.replace(tmp, ut)

    def _npy(p, arr=matrix):
        with open(p, "wb") as f:
            np.save(f, arr)
    _csere(os.path.join(cel, "vektorok.npy"), _npy)
    def _jsonl(p):
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            for d in darabok:
                f.write(k.json_sor({x: y for x, y in d.items() if not x.startswith("_")}) + "\n")
    _csere(os.path.join(cel, "darabok.jsonl"), _jsonl)
    # only live keys stay in the cache (so it doesn't grow forever); both layers share the cache,
    # so building the base layer keeps the archive keys too when the archive index exists
    elo_kulcsok = {d["_kulcs"] for d in darabok}
    masik = os.path.join(k.INDEX_DIR, slug + ("" if archiv else "_archiv"), "darabok.jsonl")
    tartott = {kk: v for kk, v in cache.items() if kk in elo_kulcsok} if not os.path.exists(masik) else cache
    kulcsok = np.array(list(tartott.keys()))
    def _npz(p):
        with open(p, "wb") as f:
            np.savez(f, kulcsok=kulcsok, vektorok=np.stack(list(tartott.values())))
    _csere(cache_ut, _npz)
    info = {"modell": modell, "archiv": archiv, "fajlok": fajl_db, "darabok": len(darabok),
            "uj_beagyazas": len(uj), "titok_kimaradt": titok_db,
            "epult": time.strftime("%Y-%m-%d %H:%M:%S"), "mp": round(time.time() - t0, 1)}
    def _info(p):
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False, indent=1))
    _csere(os.path.join(cel, "info.json"), _info)
    return info


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--modell", default=ALAP_MODELL)
    ap.add_argument("--archiv", action="store_true")
    ap.add_argument("--csendes", "--quiet", dest="csendes", action="store_true")
    a = ap.parse_args()
    print(k.json_sor(epit(a.modell, a.archiv, a.csendes)))
