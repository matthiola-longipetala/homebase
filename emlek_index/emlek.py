# -*- coding: utf-8 -*-
"""Associative recall — the query side. Text → the 3 closest memories, each with its author tag.

Command line (from the venv):
  python emlek.py "the taste of gummy bears"
  python emlek.py --json --k 5 --archiv --ma-is "text"
As a library: from emlek import Emlekezo; e = Emlekezo(); e.keres("…"); e.sor("…")
("Emlékező" = the one who remembers; keres = search; sor = line.)

Rules:
- at most 1 hit per file (otherwise one long journal fills all three slots);
- TODAY's chunks are left out by default: today's journal echoes the conversation happening right
  now, that is not a memory (--ma-is = "today too" overrides);
- below the gate there is no line (empty output = no memory, not an error).
"""
import argparse
import collections
import json
import math
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import emlek_kozos as k  # noqa: E402
from emlek_epito import ALAP_MODELL, modell_slug  # noqa: E402

# Score = cosine + LEX_SULY * (idf weight of the shared rare word stems / idf weight of the query).
# e5 cosines live in a narrow band (noise ~0.83, a good hit ~0.86–0.90), so the gate is not absolute:
# a line only comes when the best hit stands out from the query's own score distribution (GAP50), OR
# shares a rare word stem with it. Tune these on your own memory with --json --kapu-nelkul ("no gate").
LEX_SULY = 0.06            # weight of the lexical part
GAP50_KAPU = 0.03          # gate for a purely meaning-based stand-out (best cosine − 51st best)
# A shared stem with idf above this = lexical evidence (idf = ln(N/df)). NOTE: idf grows with the index
# size N, so a fixed value lets more through as the memory grows (at N≈8300, 4.0 ≈ df ≲ 150). Re-check it
# when your index has grown a lot, or set "rare_idf" in the config.
RITKA_IDF = float(k.CFG.get("rare_idf", 4.0))
ALAP_KUSZOB = 0.0          # lower bound for each hit after the gate (0 = none)
SZO_RE = re.compile(r"[^\W\d_]{4,}")   # words of 4+ letters, any alphabet
# Filler words that carry no memory (English + Hungarian defaults; add your own, e.g. your names,
# with "stopwords" in the config — a name that appears everywhere is not a rare stem).
TOLTELEK = set("""that this with have from they will would there their what when your about which
then them been were said each just like into than only some could other also back after where most
over know really thanks thank okay good morning night evening today tomorrow yesterday please
hogy amit akkor csak volt van lesz nem igen mert most ahol vagy még már majd ezt azt
itt ott neki nekem nagyon sima mint után előtt alatt között minden sokkal kell lehet szerint amikor
aztán ugyanaz holnap tegnap mennyi milyen miért hogyan valami valamit claude
szia köszi köszönöm oké okés rendben reggelt estét éjszakát jól tudod tudom egyébként""".split()) | \
    {w.lower() for w in k.CFG.get("stopwords", [])}


# The ONNX export lives machine-local (not in a cloud-synced folder): see emlek_onnx_export.py.
ONNX_DIR = os.path.expanduser(os.environ.get("EMLEK_ONNX_DIR") or os.path.join("~", ".cache", "emlek_onnx"))
HATTERTAR = os.environ.get("EMLEK_KODOLO", "onnx-int8")   # encoder backend: onnx-int8 (default) | onnx | st


class OnnxKodolo:
    """A light encoder: onnxruntime + tokenizers, without torch. Measured memory: sentence-transformers
    2.1 GB private · onnx 1.2 GB · onnx-int8 0.9 GB. Vector agreement with the original: fp32 1.0000, int8 ~0.99."""
    def __init__(self, fajl):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.tk = Tokenizer.from_file(os.path.join(ONNX_DIR, "tok", "tokenizer.json"))
        self.tk.enable_truncation(512)
        o = ort.SessionOptions()
        o.intra_op_num_threads = 2
        o.inter_op_num_threads = 1
        o.enable_cpu_mem_arena = False
        self.s = ort.InferenceSession(os.path.join(ONNX_DIR, fajl), o, providers=["CPUExecutionProvider"])

    def encode(self, szovegek, **_):
        ki = []
        for t in szovegek:
            e = self.tk.encode(t)
            ki.append(self.s.run(None, {"input_ids": np.array([e.ids], dtype=np.int64),
                                        "attention_mask": np.array([e.attention_mask], dtype=np.int64)})[0][0])
        return np.stack(ki)


def szotovek(szoveg):
    """A crude stem: the first 5 letters of every 4+ letter word (cuts off most suffixes,
    in Hungarian and in English alike)."""
    return {w[:5] for w in SZO_RE.findall(szoveg.lower()) if w not in TOLTELEK}


class Emlekezo:
    def __init__(self, modell=ALAP_MODELL, archiv=False, hattertar=None):
        self.modell_nev = modell
        self.hattertar = hattertar or HATTERTAR
        self.archiv = archiv
        self.mappa = os.path.join(k.INDEX_DIR, modell_slug(modell) + ("_archiv" if archiv else ""))
        self._m = None
        self._mtime = None
        self._betolt()

    def _betolt(self):
        vut = os.path.join(self.mappa, "vektorok.npy")
        mt = os.path.getmtime(vut)
        if mt == self._mtime:
            return
        self.V = np.load(vut).astype(np.float32)
        with open(os.path.join(self.mappa, "darabok.jsonl"), encoding="utf-8") as f:
            self.D = [json.loads(s) for s in f]
        self.T = [szotovek(d["szoveg"]) for d in self.D]
        df = collections.Counter(t for ts in self.T for t in ts)
        n = len(self.T)
        self.idf = {t: math.log(n / (1 + c)) for t, c in df.items()}
        self._max_idf = math.log(n)
        self._mtime = mt

    def _modell(self):
        if self._m is None:
            fajl = {"onnx": "e5_small.onnx", "onnx-int8": "e5_small_int8.onnx"}.get(self.hattertar)
            if fajl and self.modell_nev == ALAP_MODELL and os.path.exists(os.path.join(ONNX_DIR, fajl)):
                self._m = OnnxKodolo(fajl)
            else:
                from sentence_transformers import SentenceTransformer
                self._m = SentenceTransformer(self.modell_nev, device="cpu")
        return self._m

    def keres(self, szoveg, k_db=3, kuszob=ALAP_KUSZOB, ma_is=False, kizart_fajlok=(),
              kapu=True, lex_suly=LEX_SULY):
        """A list of hits (empty = no memory). kapu=False: the noise gate is off (for measuring)."""
        self._betolt()   # reloads if the nightly rebuild replaced the index
        elo = "query: " if "e5" in self.modell_nev.lower() else ""
        q = self._modell().encode([elo + szoveg], normalize_embeddings=True,
                                  convert_to_numpy=True)[0].astype(np.float32)
        cos = self.V @ q
        qt = szotovek(szoveg)
        if kapu and not qt:
            return []            # "Okay, thanks", "Good morning!" — nothing to remember
        nevezo = sum(self.idf.get(t, self._max_idf) for t in qt) or 1.0
        lex = np.fromiter((sum(self.idf.get(t, 0.0) for t in (qt & ts)) for ts in self.T),
                          dtype=np.float32, count=len(self.T))
        pont = cos + lex_suly * lex / nevezo
        sorrend = np.argsort(-pont)
        if kapu:
            felso = sorrend[0]
            gap50 = float(cos.max() - np.partition(cos, -51)[-51]) if len(cos) > 51 else 1.0
            ritka = any(self.idf.get(t, 0) >= RITKA_IDF for t in (qt & self.T[felso]))
            if gap50 < GAP50_KAPU and not ritka:
                return []
        ma = time.strftime("%Y-%m-%d")
        ki, latott = [], set(kizart_fajlok)
        for i in sorrend:
            if pont[i] < kuszob or len(ki) >= k_db:
                break
            d = self.D[i]
            if d["fajl"] in latott or (not ma_is and d["datum"] == ma):
                continue
            latott.add(d["fajl"])
            kozos = sorted(qt & self.T[i], key=lambda t: -self.idf.get(t, 0))[:3]
            ki.append({**d, "pont": round(float(pont[i]), 3), "cos": round(float(cos[i]), 3),
                       "kozos_tovek": kozos})
        return ki

    @staticmethod
    def formaz(talalatok, hossz=90):
        """One line: [MEMORY] date (author) file:line — excerpt | …"""
        if not talalatok:
            return ""
        reszek = []
        for t in talalatok:
            nev = os.path.splitext(os.path.basename(t["fajl"]))[0]
            sz = t["szoveg"]
            sz = sz if len(sz) <= hossz else sz[:hossz - 1].rstrip() + "…"
            reszek.append(f'{t["datum"]} ({t["szerzo"]}) {nev}:{t["sor"]} — {sz}')
        return "[MEMORY] " + " | ".join(reszek)

    def sor(self, szoveg, **kw):
        return self.formaz(self.keres(szoveg, **kw))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("szoveg")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--kuszob", type=float, default=ALAP_KUSZOB)
    ap.add_argument("--archiv", action="store_true")
    ap.add_argument("--ma-is", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--kapu-nelkul", "--no-gate", dest="kapu_nelkul", action="store_true",
                    help="noise gate off (for measuring)")
    ap.add_argument("--modell", default=ALAP_MODELL)
    a = ap.parse_args()
    e = Emlekezo(a.modell, a.archiv)
    t = e.keres(a.szoveg, a.k, a.kuszob, a.ma_is, kapu=not a.kapu_nelkul)
    print(json.dumps(t, ensure_ascii=False, indent=1) if a.json else (e.formaz(t) or "(no memory above the gate)"))
