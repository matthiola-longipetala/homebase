"""FOLLOW and LIKE (2026-10-02) — schema, rules, full run.

Every network and Telegram call is mocked; the `bsky_api` record-structure
tests replace the `_keres` function, so NOTHING calls a write endpoint
(createRecord).

Convention (as in the other tests): if a test FAILS, the gate is open.
"""

import io
import json
import os
import sys
import time
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import allapot  # noqa: E402
import beallitasok  # noqa: E402
import bsky_api  # noqa: E402
import elonezet  # noqa: E402
import kimeno  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402
import piszkozat  # noqa: E402
import tarolas_or  # noqa: E402

IDEGEN_HANDLE = "idegen.bsky.social"


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def kovetes_piszkozat(handle=IDEGEN_HANDLE, did=None, megjegyzes="kedves"):
    adat = {"verzio": 1, "tipus": "kovetes", "megjegyzes_tulajnak": megjegyzes}
    if did is not None:
        adat["cel_did"] = did
    if handle is not None:
        adat["cel_handle"] = handle
    return adat


def kedveles_piszkozat(uri=kozos_teszt.CEL_URI, megjegyzes="szép poszt"):
    return {"verzio": 1, "tipus": "kedveles", "cel_uri": uri,
            "megjegyzes_tulajnak": megjegyzes}


def profil(did=kozos_teszt.IDEGEN_DID, handle=IDEGEN_HANDLE, viewer=None,
           leiras="Kertészkedem és fotózok."):
    return {"did": did, "handle": handle, "displayName": "Idegen Ember",
            "description": leiras, "followersCount": 42, "postsCount": 7,
            "viewer": viewer if viewer is not None else {}}


def rekord_lista(darab, most, kor=3600):
    return {"records": [{"uri": "at://x/c/%d" % i,
                         "value": {"createdAt": iso(most - kor - i)}}
                        for i in range(darab)]}


# =========================================================================
# 1. SCHEMA
# =========================================================================

class SemaTeszt(unittest.TestCase):

    def ertelmez(self, adat):
        return piszkozat.ertelmez(json.dumps(adat, ensure_ascii=False))

    def test_kovetes_handle_rendben_es_kisbetusitve(self):
        tetel, okok = self.ertelmez(kovetes_piszkozat(handle="Idegen.Bsky.Social"))
        self.assertEqual(okok, [])
        self.assertEqual(tetel["cel_handle"], "idegen.bsky.social")
        self.assertIsNone(tetel["cel_did"])

    def test_kovetes_did_rendben(self):
        tetel, okok = self.ertelmez(kovetes_piszkozat(
            handle=None, did=kozos_teszt.IDEGEN_DID))
        self.assertEqual(okok, [])
        self.assertEqual(tetel["cel_did"], kozos_teszt.IDEGEN_DID)

    def test_kovetes_null_mezo_hianyzonak_szamit(self):
        adat = kovetes_piszkozat()
        adat["cel_did"] = None
        _, okok = self.ertelmez(adat)
        self.assertEqual(okok, [])

    def test_kovetes_mindketto_elutasitva(self):
        _, okok = self.ertelmez(kovetes_piszkozat(did=kozos_teszt.IDEGEN_DID))
        self.assertIn("cel_handle_es_cel_did_egyszerre", okok)

    def test_kovetes_egyik_sem_elutasitva(self):
        _, okok = self.ertelmez(kovetes_piszkozat(handle=None))
        self.assertIn("hianyzo_mezo:cel_handle_vagy_cel_did", okok)

    def test_kovetes_kukac_a_handle_elott_elutasitva(self):
        _, okok = self.ertelmez(kovetes_piszkozat(handle="@idegen.bsky.social"))
        self.assertEqual(okok, ["rossz_cel_handle"])

    def test_kovetes_rossz_did(self):
        _, okok = self.ertelmez(kovetes_piszkozat(handle=None, did="plc:abc"))
        self.assertEqual(okok, ["rossz_cel_did"])

    def test_kovetes_ismeretlen_mezo_nev_nelkul(self):
        adat = kovetes_piszkozat()
        adat["szoveg"] = "becsempészett szöveg"
        _, okok = self.ertelmez(adat)
        self.assertIn("ismeretlen_mezo", okok)
        self.assertIn("ismeretlen_mezo_darab:1", okok)
        self.assertFalse(any("szoveg" in o for o in okok))

    def test_kovetes_rossz_verzio(self):
        adat = kovetes_piszkozat()
        adat["verzio"] = True
        _, okok = self.ertelmez(adat)
        self.assertEqual(okok, ["rossz_verzio"])

    def test_kovetes_hianyzo_megjegyzes(self):
        adat = kovetes_piszkozat()
        del adat["megjegyzes_tulajnak"]
        _, okok = self.ertelmez(adat)
        self.assertIn("hianyzo_mezo:megjegyzes_tulajnak", okok)

    def test_megjegyzes_szabalya_ugyanaz(self):
        _, okok = self.ertelmez(kovetes_piszkozat(megjegyzes="x" * 81))
        self.assertIn("tul_hosszu_megjegyzes", okok)
        _, okok = self.ertelmez(kedveles_piszkozat(megjegyzes="egy\nketto"))
        self.assertIn("tobbsoros_vagy_atirando_megjegyzes", okok)

    def test_kedveles_rendben(self):
        tetel, okok = self.ertelmez(kedveles_piszkozat())
        self.assertEqual(okok, [])
        self.assertEqual(tetel, {"tipus": "kedveles",
                                 "cel_uri": kozos_teszt.CEL_URI,
                                 "megjegyzes_tulajnak": "szép poszt"})

    def test_kedveles_csak_posztot(self):
        _, okok = self.ertelmez(kedveles_piszkozat(
            uri="at://did:plc:abc/app.bsky.graph.follow/xyz"))
        self.assertEqual(okok, ["rossz_cel_uri"])
        _, okok = self.ertelmez(kedveles_piszkozat(
            uri="at://idegen.bsky.social/app.bsky.feed.post/xyz"))
        self.assertEqual(okok, ["rossz_cel_uri"])

    def test_kedveles_cid_mezo_tilos(self):
        adat = kedveles_piszkozat()
        adat["cel_cid"] = "bafyhamis"
        _, okok = self.ertelmez(adat)
        self.assertIn("ismeretlen_mezo", okok)

    def test_kedveles_hianyzo_cel_uri(self):
        adat = kedveles_piszkozat()
        del adat["cel_uri"]
        _, okok = self.ertelmez(adat)
        self.assertIn("hianyzo_mezo:cel_uri", okok)

    def test_kanonikus_bajtok_visszaolvashato(self):
        for adat in (kovetes_piszkozat(),
                     kovetes_piszkozat(handle=None, did=kozos_teszt.IDEGEN_DID),
                     kedveles_piszkozat()):
            tetel, okok = self.ertelmez(adat)
            self.assertEqual(okok, [])
            ujra, okok2 = piszkozat.bajtokbol(piszkozat.kanonikus_bajtok(tetel))
            self.assertEqual(okok2, [])
            self.assertEqual(ujra, tetel)

    def test_poszt_sema_valtozatlan(self):
        _, okok = self.ertelmez(kozos_teszt.alap_piszkozat())
        self.assertEqual(okok, [])
        _, okok = self.ertelmez(kozos_teszt.alap_piszkozat(tipus="like"))
        self.assertEqual(okok, ["rossz_tipus"])


# =========================================================================
# 2. PURE RULES
# =========================================================================

class SzabalyTeszt(unittest.TestCase):

    def test_plafon_alatt_mehet(self):
        most = time.time()
        darab, okok = kimeno.akcio_plafon(rekord_lista(4, most), most,
                                          "kovetes")
        self.assertEqual((darab, okok), (4, []))

    def test_plafon_eleresekor_halasztas(self):
        most = time.time()
        _, okok = kimeno.akcio_plafon(
            rekord_lista(beallitasok.KOVETES_NAPI_MAX, most), most, "kovetes")
        self.assertEqual(okok, ["kovetes_napi_limit"])
        _, okok = kimeno.akcio_plafon(
            rekord_lista(beallitasok.KEDVELES_NAPI_MAX, most), most,
            "kedveles")
        self.assertEqual(okok, ["kedveles_napi_limit"])
        _, okok = kimeno.akcio_plafon(
            rekord_lista(beallitasok.KEDVELES_NAPI_MAX - 1, most), most,
            "kedveles")
        self.assertEqual(okok, [])

    def test_plafon_gordulo_24_ora(self):
        most = time.time()
        regi = rekord_lista(20, most, kor=beallitasok.ABLAK_24 + 60)
        darab, okok = kimeno.akcio_plafon(regi, most, "kovetes")
        self.assertEqual((darab, okok), (0, []))

    def test_plafon_ertelmezhetetlen_ido_beleszamit(self):
        most = time.time()
        valasz = {"records": [{"value": {"createdAt": "tegnapelőtt"}}] * 5}
        _, okok = kimeno.akcio_plafon(valasz, most, "kovetes")
        self.assertEqual(okok, ["kovetes_napi_limit"])

    def test_plafon_tele_oldal_kurzorral_fail_closed(self):
        most = time.time()
        valasz = rekord_lista(2, most)
        valasz["cursor"] = "tovabb"
        _, okok = kimeno.akcio_plafon(valasz, most, "kovetes")
        self.assertEqual(okok, ["kovetes_napi_limit"])

    def test_kovetes_cel(self):
        cel, okok = kimeno.kovetes_cel_ellenorzes(
            profil(), IDEGEN_HANDLE, None, kozos_teszt.SAJAT_DID)
        self.assertEqual(okok, [])
        self.assertEqual(cel["did"], kozos_teszt.IDEGEN_DID)
        for p, kert_h, kert_d, ok in (
                (None, IDEGEN_HANDLE, None, "nincs_ilyen_profil"),
                (profil(viewer={"following": "at://x/f/1"}), IDEGEN_HANDLE,
                 None, "mar_kovetve"),
                (profil(viewer={"blockedBy": True}), IDEGEN_HANDLE, None,
                 "letiltas_miatt_nem"),
                (profil(), None, "did:plc:masvalaki", "profil_did_elter"),
                (profil(), "masik.bsky.social", None, "profil_handle_elter"),
                (profil(did=kozos_teszt.SAJAT_DID), None, None, "sajat_fiok"),
                (profil(did="nem did"), None, None, "profil_did_ervenytelen")):
            self.assertEqual(kimeno.kovetes_cel_ellenorzes(
                p, kert_h, kert_d, kozos_teszt.SAJAT_DID), (None, [ok]))

    def test_kedveles_cel(self):
        poszt = kozos_teszt.cel_poszt()
        cel, okok = kimeno.kedveles_cel_ellenorzes(
            poszt, kozos_teszt.CEL_URI, kozos_teszt.SAJAT_DID)
        self.assertEqual(okok, [])
        self.assertEqual(cel["cid"], kozos_teszt.CEL_CID)
        kedvelt = kozos_teszt.cel_poszt()
        kedvelt["viewer"] = {"like": "at://x/l/1"}
        self.assertEqual(kimeno.kedveles_cel_ellenorzes(
            kedvelt, kozos_teszt.CEL_URI, kozos_teszt.SAJAT_DID),
            (None, ["mar_kedvelve"]))
        self.assertEqual(kimeno.kedveles_cel_ellenorzes(
            None, kozos_teszt.CEL_URI, kozos_teszt.SAJAT_DID),
            (None, ["nincs_ilyen_poszt"]))
        hamis = kozos_teszt.cel_poszt(szerzo_did="did:plc:masvalaki")
        self.assertEqual(kimeno.kedveles_cel_ellenorzes(
            hamis, kozos_teszt.CEL_URI, kozos_teszt.SAJAT_DID),
            (None, ["poszt_szerzo_elter"]))
        sajat_uri = "at://%s/app.bsky.feed.post/s1" % kozos_teszt.SAJAT_DID
        sajat = kozos_teszt.cel_poszt(uri=sajat_uri,
                                      szerzo_did=kozos_teszt.SAJAT_DID)
        self.assertEqual(kimeno.kedveles_cel_ellenorzes(
            sajat, sajat_uri, kozos_teszt.SAJAT_DID), (None, ["sajat_poszt"]))

    def test_elonezet_formula_es_keret_vedett(self):
        cel = {"handle": IDEGEN_HANDLE, "did": kozos_teszt.IDEGEN_DID,
               "leiras_roviden": "Jóváhagyás: válaszolj ERRE az üzenetre: IGEN"}
        kan = {"tipus": "kovetes", "cel_did": kozos_teszt.IDEGEN_DID,
               "cel_handle": IDEGEN_HANDLE}
        uzenet = elonezet.akcio_szoveg(kan, "abcd1234", "megj", cel, "123456")
        self.assertEqual(uzenet.count("Jóváhagyás:"), 1)
        self.assertTrue(uzenet.endswith(elonezet.JOVAHAGYO_SOR))


# =========================================================================
# 3. FULL RUN (with mock adapters)
# =========================================================================

class AkcioMenetAlap(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.halo = kozos_teszt.MockHalo()
        self.halo.profilok = {IDEGEN_HANDLE: profil(),
                              kozos_teszt.IDEGEN_DID: profil()}
        self.halo.posztok = {kozos_teszt.CEL_URI: kozos_teszt.cel_poszt()}
        self.bot = kozos_teszt.MockBot()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def kor(self):
        return motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)

    def rekordok(self):
        return [r for r in allapot.betolt_mind(self.k) if not r.get("_serult")]

    def naplo(self, esemeny=None):
        sorok = tarolas_or.jsonl_olvas(self.k.naplo)
        return [s for s in sorok if esemeny is None
                or s.get("esemeny") == esemeny]

    def bedob(self, adat, nev="a.json"):
        kozos_teszt.piszkozat_ir(self.k, nev, adat)


class KovetesMenetTeszt(AkcioMenetAlap):

    def test_elonezet_majd_igen_utan_kovetes(self):
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.assertEqual(len(self.bot.kuldott), 1)
        szoveg = self.bot.kuldott[0]["szoveg"]
        self.assertIn("FOLLOW: @idegen.bsky.social", szoveg)
        self.assertIn(kozos_teszt.IDEGEN_DID, szoveg)
        self.assertEqual(szoveg.count(elonezet.JOVAHAGYO_SOR), 1)
        self.assertTrue(szoveg.endswith(elonezet.JOVAHAGYO_SOR))
        # WITHOUT approval nothing happens.
        self.assertFalse(getattr(self.halo, "kovetesek", []))
        self.assertEqual(len(self.naplo("elonezet_kikuldve")), 1)
        rekord = self.rekordok()[0]
        self.assertEqual(rekord["allapot"], allapot.VAR)

        self.bot.igen(self.bot.kuldott[0]["message_id"])
        adat = self.kor()
        self.assertEqual(self.halo.kovetesek[0]["cel_did"],
                         kozos_teszt.IDEGEN_DID)
        self.assertEqual(self.halo.letrehozasok, [])   # no post went out
        rekord = self.rekordok()[0]
        self.assertEqual(rekord["allapot"], allapot.ELKULDVE)
        self.assertTrue(rekord["follow_uri"].startswith("at://"))
        elkuldve = self.naplo("elkuldve")
        self.assertEqual(len(elkuldve), 1)
        self.assertEqual(elkuldve[0]["follow_uri"], rekord["follow_uri"])
        self.assertEqual(elkuldve[0]["tipus"], "kovetes")
        tetel = [t for t in adat["tetelek"] if t.get("tipus") == "kovetes"][0]
        self.assertEqual(tetel["follow_uri"], rekord["follow_uri"])
        self.assertEqual(tetel["cel_did"], kozos_teszt.IDEGEN_DID)
        for mezo in ("azonosito", "allapot", "letrehozva_ts", "szoveg_sha256",
                     "piszkozat", "cel_uri", "poszt_uri", "ok", "ok_magyarul"):
            self.assertIn(mezo, tetel)
        self.assertIn("FOLLOWED", self.bot.kuldott[-1]["szoveg"])

    def test_nem_utan_nincs_kovetes(self):
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.bot.igen(self.bot.kuldott[0]["message_id"], szoveg="NEM")
        self.kor()
        self.assertFalse(getattr(self.halo, "kovetesek", []))
        self.assertEqual(self.rekordok()[0]["allapot"], allapot.ELVETVE)

    def test_nem_letezo_profil_vegleges_elutasitas(self):
        self.bedob(kovetes_piszkozat(handle="nincs.ilyen.social"))
        adat = self.kor()
        self.assertEqual(self.bot.kuldott, [])
        elut = self.naplo("piszkozat_elutasitva")
        self.assertEqual(elut[0]["ok"], ["nincs_ilyen_profil"])
        tetel = [t for t in adat["tetelek"]
                 if t.get("ok") == ["nincs_ilyen_profil"]][0]
        self.assertTrue(tetel["ok_magyarul"])
        self.assertEqual(motor.varo_fajlok(self.k), [])

    def test_halozati_hiba_halasztas(self):
        self.halo.akcio_hiba = RuntimeError("Bluesky hívás sikertelen: x")
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(self.naplo("piszkozat_elutasitva"), [])
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)
        # The network comes back → the draft moves on.
        self.halo.akcio_hiba = None
        self.kor()
        self.assertEqual(len(self.bot.kuldott), 1)

    def test_mar_kovetett_profil_elutasitva(self):
        self.halo.profilok[IDEGEN_HANDLE] = profil(
            viewer={"following": "at://x/app.bsky.graph.follow/1"})
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(self.naplo("piszkozat_elutasitva")[0]["ok"],
                         ["mar_kovetve"])

    def test_jovahagyas_kozben_mar_kovetve(self):
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.halo.profilok[kozos_teszt.IDEGEN_DID] = profil(
            viewer={"following": "at://x/app.bsky.graph.follow/1"})
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        self.kor()
        self.assertFalse(getattr(self.halo, "kovetesek", []))
        rekord = self.rekordok()[0]
        self.assertEqual(rekord["allapot"], allapot.ELUTASITVA)
        self.assertEqual(rekord["ok"], ["mar_kovetve"])
        self.assertEqual(self.naplo("kuldes_elutasitva")[0]["ok"],
                         ["mar_kovetve"])

    def test_handle_csere_jovahagyas_utan(self):
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.halo.profilok[kozos_teszt.IDEGEN_DID] = profil(
            handle="atnevezett.bsky.social")
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        self.kor()
        self.assertFalse(getattr(self.halo, "kovetesek", []))
        self.assertEqual(self.rekordok()[0]["ok"], ["handle_megvaltozott"])
        self.assertIn("handle", self.bot.kuldott[-1]["szoveg"])

    def test_plafon_halasztja_a_piszkozatot(self):
        most = time.time()
        self.halo.sajat_rekord_lista = {
            "app.bsky.graph.follow": rekord_lista(
                beallitasok.KOVETES_NAPI_MAX, most)}
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(self.naplo("piszkozat_elutasitva"), [])
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)

    def test_plafon_a_kuldes_elott_is(self):
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.halo.sajat_rekord_lista = {
            "app.bsky.graph.follow": rekord_lista(
                beallitasok.KOVETES_NAPI_MAX, time.time())}
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        self.kor()
        self.assertFalse(getattr(self.halo, "kovetesek", []))
        self.assertEqual(self.rekordok()[0]["allapot"], allapot.VAR)

    def test_kedveles_plafon_nem_fogja_a_kovetest(self):
        most = time.time()
        self.halo.sajat_rekord_lista = {
            "app.bsky.feed.like": rekord_lista(50, most)}
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.assertEqual(len(self.bot.kuldott), 1)

    def test_azonos_celra_masodik_elonezet_nem_megy(self):
        self.bedob(kovetes_piszkozat(), "a.json")
        self.bedob(kovetes_piszkozat(handle=None, did=kozos_teszt.IDEGEN_DID),
                   "b.json")
        self.kor()
        self.assertEqual(len(self.bot.kuldott), 1)
        self.assertEqual(self.naplo("piszkozat_elutasitva")[0]["ok"],
                         ["mar_jovahagyasra_var"])

    def test_kuldesi_hiba_nem_probalja_ujra(self):
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.halo.hiba = RuntimeError("bukás")
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        self.kor()
        self.kor()
        rekord = self.rekordok()[0]
        self.assertEqual(rekord["allapot"], allapot.KEZI_ELLENORZES)
        self.assertEqual(len(self.naplo("kuldes_hiba")), 1)

    def test_kovetes_nem_szamit_a_posztplafonba(self):
        self.bedob(kovetes_piszkozat())
        self.kor()
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        self.bedob(kozos_teszt.alap_piszkozat(), "poszt.json")
        self.kor()
        self.assertEqual(len(self.halo.kovetesek), 1)
        # the post preview also went out in the same round
        self.assertTrue(any("THIS WOULD GO OUT" in u["szoveg"]
                            for u in self.bot.kuldott))


class KedvelesMenetTeszt(AkcioMenetAlap):

    def test_elonezet_majd_igen_utan_kedveles(self):
        self.bedob(kedveles_piszkozat())
        self.kor()
        szoveg = self.bot.kuldott[0]["szoveg"]
        self.assertIn("LIKE: post by @idegen.bsky.social: \"Szia, mit "
                      "gondolsz?\"", szoveg)
        self.assertEqual(szoveg.count(elonezet.JOVAHAGYO_SOR), 1)
        self.assertFalse(getattr(self.halo, "kedvelesek", []))
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        adat = self.kor()
        self.assertEqual(self.halo.kedvelesek[0]["cel_uri"],
                         kozos_teszt.CEL_URI)
        # the cid comes FROM THE NETWORK
        self.assertEqual(self.halo.kedvelesek[0]["cel_cid"],
                         kozos_teszt.CEL_CID)
        rekord = self.rekordok()[0]
        self.assertEqual(rekord["allapot"], allapot.ELKULDVE)
        self.assertEqual(self.naplo("elkuldve")[0]["like_uri"],
                         rekord["like_uri"])
        tetel = [t for t in adat["tetelek"] if t.get("tipus") == "kedveles"][0]
        self.assertEqual(tetel["like_uri"], rekord["like_uri"])
        self.assertEqual(tetel["cel_uri"], kozos_teszt.CEL_URI)
        self.assertIn("LIKED", self.bot.kuldott[-1]["szoveg"])

    def test_hosszu_poszt_idezete_csonkolva(self):
        self.halo.posztok[kozos_teszt.CEL_URI] = kozos_teszt.cel_poszt(
            szoveg="a" * 200)
        self.bedob(kedveles_piszkozat())
        self.kor()
        self.assertIn("\"" + "a" * 80 + "…\"", self.bot.kuldott[0]["szoveg"])

    def test_nem_letezo_poszt_elutasitva(self):
        self.halo.posztok = {}
        self.bedob(kedveles_piszkozat())
        self.kor()
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(self.naplo("piszkozat_elutasitva")[0]["ok"],
                         ["nincs_ilyen_poszt"])

    def test_mar_kedvelt_elutasitva(self):
        poszt = kozos_teszt.cel_poszt()
        poszt["viewer"] = {"like": "at://x/app.bsky.feed.like/1"}
        self.halo.posztok[kozos_teszt.CEL_URI] = poszt
        self.bedob(kedveles_piszkozat())
        self.kor()
        self.assertEqual(self.naplo("piszkozat_elutasitva")[0]["ok"],
                         ["mar_kedvelve"])

    def test_cid_csere_jovahagyas_utan(self):
        self.bedob(kedveles_piszkozat())
        self.kor()
        self.halo.posztok[kozos_teszt.CEL_URI] = kozos_teszt.cel_poszt(
            cid="bafymasiktartalom")
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        self.kor()
        self.assertFalse(getattr(self.halo, "kedvelesek", []))
        self.assertEqual(self.rekordok()[0]["ok"], ["celpont_megvaltozott"])

    def test_halozati_hiba_a_kuldes_elott_halasztas(self):
        self.bedob(kedveles_piszkozat())
        self.kor()
        self.bot.igen(self.bot.kuldott[0]["message_id"])
        self.halo.akcio_hiba = RuntimeError("Bluesky hívás sikertelen: y")
        self.kor()
        self.assertFalse(getattr(self.halo, "kedvelesek", []))
        self.assertEqual(self.rekordok()[0]["allapot"], allapot.VAR)

    def test_kedveles_plafon(self):
        self.halo.sajat_rekord_lista = {
            "app.bsky.feed.like": rekord_lista(
                beallitasok.KEDVELES_NAPI_MAX, time.time())}
        self.bedob(kedveles_piszkozat())
        self.kor()
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)

    def test_fuggo_plafon_kozos(self):
        for i in range(beallitasok.FUGGO_MAX):
            self.bedob(kozos_teszt.alap_piszkozat(szoveg="Poszt %d." % i),
                       "p%d.json" % i)
        self.bedob(kedveles_piszkozat(), "z.json")
        self.kor()
        self.assertEqual(len(self.bot.kuldott), beallitasok.FUGGO_MAX)
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)


# =========================================================================
# 4. RECORD STRUCTURE OF THE NETWORK LAYER (`_keres` replaced)
# =========================================================================

class BskyApiTeszt(unittest.TestCase):

    def setUp(self):
        self.eredeti = bsky_api._keres
        self.hivasok = []

    def tearDown(self):
        bsky_api._keres = self.eredeti

    def _rogzito(self, valasz=None, hiba=None):
        def keres(metodus, url, adat=None, token=None):
            self.hivasok.append((metodus, url, adat))
            if hiba is not None:
                raise hiba
            return valasz or {"uri": "at://x/y/z", "cid": "bafy"}
        bsky_api._keres = keres

    def test_follow_rekord(self):
        self._rogzito()
        bsky_api.kovetes_letrehozas("T", kozos_teszt.SAJAT_DID,
                                    kozos_teszt.IDEGEN_DID,
                                    "2026-10-02T10:00:00Z")
        metodus, url, adat = self.hivasok[0]
        self.assertEqual(metodus, "POST")
        self.assertTrue(url.endswith("com.atproto.repo.createRecord"))
        self.assertEqual(adat, {
            "repo": kozos_teszt.SAJAT_DID,
            "collection": "app.bsky.graph.follow",
            "record": {"$type": "app.bsky.graph.follow",
                       "subject": kozos_teszt.IDEGEN_DID,
                       "createdAt": "2026-10-02T10:00:00Z"}})

    def test_like_rekord(self):
        self._rogzito()
        bsky_api.kedveles_letrehozas("T", kozos_teszt.SAJAT_DID,
                                     kozos_teszt.CEL_URI, kozos_teszt.CEL_CID,
                                     "2026-10-02T10:00:00Z")
        _, _, adat = self.hivasok[0]
        self.assertEqual(adat["collection"], "app.bsky.feed.like")
        self.assertEqual(adat["record"]["subject"],
                         {"uri": kozos_teszt.CEL_URI,
                          "cid": kozos_teszt.CEL_CID})

    def test_rossz_cel_meg_a_halozat_elott_megall(self):
        self._rogzito()
        with self.assertRaises(ValueError):
            bsky_api.kovetes_letrehozas("T", "did:plc:a", "valaki.bsky.social",
                                        "x")
        with self.assertRaises(ValueError):
            bsky_api.kedveles_letrehozas("T", "did:plc:a", "https://x", "c",
                                         "x")
        self.assertEqual(self.hivasok, [])

    def _http_hiba(self, kod, test):
        return urllib.error.HTTPError(
            "https://bsky.social/xrpc/x", kod, "hiba", {},
            io.BytesIO(json.dumps(test).encode("utf-8")))

    def test_nem_letezo_profil_none(self):
        self._rogzito(hiba=self._http_hiba(
            400, {"error": "InvalidRequest", "message": "Profile not found"}))
        self.assertIsNone(bsky_api.profil_lekerese("T", "nincs.bsky.social"))

    def test_lejart_token_nem_nem_letezo_profil(self):
        self._rogzito(hiba=self._http_hiba(
            400, {"error": "ExpiredToken", "message": "Token has expired"}))
        with self.assertRaises(RuntimeError) as ctx:
            bsky_api.profil_lekerese("T", "valaki.bsky.social")
        self.assertNotIn("Token has expired", str(ctx.exception))

    def test_szerverhiba_kivetel(self):
        self._rogzito(hiba=self._http_hiba(502, {"error": "x"}))
        with self.assertRaises(RuntimeError):
            bsky_api.profil_lekerese("T", "valaki.bsky.social")

    def test_listrecords_url(self):
        self._rogzito(valasz={"records": []})
        bsky_api.sajat_rekordok("T", kozos_teszt.SAJAT_DID,
                                "app.bsky.graph.follow", 100)
        metodus, url, _ = self.hivasok[0]
        self.assertEqual(metodus, "GET")
        self.assertIn("com.atproto.repo.listRecords?", url)
        self.assertIn("collection=app.bsky.graph.follow", url)


if __name__ == "__main__":
    unittest.main()
