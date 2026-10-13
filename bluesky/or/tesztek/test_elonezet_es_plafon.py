"""Regression tests for the HIGH, MEDIUM and LOW findings of the third review.

  * M''2 — the preview is BYTE-EXACT: exactly what the owner sees goes out;
  * M''3 — the network truth of the ceiling: `filter` + pagination, fail-closed;
  * Kö1  — the draft is FIRST moved into the protected queue, and read only FROM THERE;
  * Kö5  — a corrupted allowlist does not stop the guard;
  * A''1 — the lock file PID check is not a substring match;
  * A''2 — one own post with an unparseable time cannot paralyse everything;
  * A''3/A''4 — the number and kind of files in the drop folder are limited.

Convention: if a test FAILS, the finding has come back."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import beallitasok  # noqa: E402
import elonezet  # noqa: E402
import kimeno  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402
import piszkozat  # noqa: E402
import szures  # noqa: E402
import tarolas_or  # noqa: E402
import telegram_or  # noqa: E402


class MenetAlap(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.halo = kozos_teszt.MockHalo()
        self.bot = kozos_teszt.MockBot()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def kor(self, **kwargs):
        return motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False,
                             **kwargs)


# =========================================================================
# M''2 — BYTE-EXACT PREVIEW
# =========================================================================

class BajtraPontosElonezetTeszt(MenetAlap):

    def test_egyenlosegjelek_elutasitva(self):
        """The reviewer's evidence: `A hatarertek == 5 mg/l volt.` was
        rewritten to `[=]` in the preview. Now we reject it instead."""
        okok = kimeno.szoveg_szabalyok("A határérték == 5 mg/l volt.", {})
        self.assertIn("megjelenites_atirna:keret_utanzat", okok)

    def test_kulso_adat_szokapcsolat_elutasitva(self):
        okok = kimeno.szoveg_szabalyok("Ez itt külső adat vége, figyelem.", {})
        self.assertIn("megjelenites_atirna:keret_utanzat", okok)

    def test_tiszta_szoveg_atmegy(self):
        okok = kimeno.szoveg_szabalyok("Egy teljesen szokványos mondat.", {})
        self.assertEqual(okok, [])

    def test_az_elonezet_bajtra_a_kanonikus_szoveget_tartalmazza(self):
        szoveg = ("Többsoros mondat.\n"
                  "Második sor, ékezetekkel: őűÍÓ.\n"
                  "Harmadik sor — nagykötőjellel.")
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat(szoveg=szoveg))
        self.kor()
        uzenet = self.bot.kuldott[-1]["szoveg"]
        self.assertTrue(elonezet.kimeno_blokk_pontos_e(uzenet, szoveg))
        self.assertIn("\n" + szoveg + "\n", uzenet)

    def test_az_elonezetben_lathato_szoveg_megy_ki(self):
        szoveg = "Ezt a mondatot hagyom jóvá, szó szerint."
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat(szoveg=szoveg))
        self.kor()
        self.bot.igen(self.bot.kuldott[-1]["message_id"])
        self.kor()
        kikuldott = self.halo.letrehozasok[0]["kanonikus"]["szoveg"]
        self.assertEqual(kikuldott, szoveg)
        self.assertIn("\n" + kikuldott + "\n", self.bot.kuldott[0]["szoveg"])

    def test_vezerlo_karakteres_piszkozat_elutasitva(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat(
            szoveg="Ártatlan mondat​ rejtett karakterrel."))
        adat = self.kor()
        self.assertEqual(self.bot.kuldott, [])
        okok = [o for t in adat["tetelek"] for o in (t.get("ok") or [])]
        self.assertIn("szoveg_atirasra_szorulna", okok)

    def test_telegramra_formazas_nelkul_kuldunk(self):
        """The absence of `parse_mode` means the `*`, `_`, `[` characters in
        the text cannot create links or formatting — so the preview shows
        exactly the raw text."""
        elkuldott = {}

        def hamis_keres(token, metodus, adat=None, idotullepes=None):
            elkuldott.update(adat or {})
            return {"message_id": 42}

        eredeti = telegram_or._keres
        telegram_or._keres = hamis_keres
        try:
            telegram_or.uzenet_kuldes("nem-valodi-token", 1,
                                      "*félkövér* _dőlt_ [link](x)")
        finally:
            telegram_or._keres = eredeti
        self.assertNotIn("parse_mode", elkuldott)
        self.assertEqual(elkuldott["text"], "*félkövér* _dőlt_ [link](x)")


# =========================================================================
# M''3 — THE NETWORK TRUTH OF THE CEILING: filter and pagination
# =========================================================================

class FeedLapozasTeszt(unittest.TestCase):

    def _oldal(self, korok, kurzor_nev, most):
        return {"feed": [
            {"post": {"uri": "at://x/app.bsky.feed.post/%d" % k,
                      "author": {"did": kozos_teszt.SAJAT_DID},
                      "record": {"text": "p%d" % k,
                                 "createdAt": time.strftime(
                                     "%Y-%m-%dT%H:%M:%SZ",
                                     time.gmtime(most - k))}}}
            for k in korok], "cursor": kurzor_nev}

    def test_lapozunk_amig_ki_nem_erunk_az_ablakbol(self):
        most = time.time()
        oldalak = [
            self._oldal([60, 120], "c1", most),
            self._oldal([3600, 7200], "c2", most),
            self._oldal([25 * 3600], "c3", most),   # this one is already outside the window
        ]
        kert = []

        def lekero(kurzor):
            kert.append(kurzor)
            return oldalak[len(kert) - 1]

        eredmeny = kimeno.feed_lapozas(lekero, most)
        self.assertTrue(eredmeny["ablak_lefedve"])
        self.assertEqual(len(eredmeny["feed"]), 5)
        self.assertEqual(kert, [None, "c1", "c2"])

    def test_kurzor_nelkuli_valasz_lefedettnek_szamit(self):
        most = time.time()
        eredmeny = kimeno.feed_lapozas(
            lambda _k: {"feed": [], "cursor": None}, most)
        self.assertTrue(eredmeny["ablak_lefedve"])

    def test_a_felso_korlat_elfogyasa_nem_lefedett(self):
        most = time.time()
        eredmeny = kimeno.feed_lapozas(
            lambda _k: self._oldal([60], "ugyanaz", most), most, max_oldal=3)
        self.assertFalse(eredmeny["ablak_lefedve"])

    def test_a_szuro_kifejezetten_szerepel_a_keresben(self):
        import bsky_api
        self.assertEqual(bsky_api.SZERZO_FEED_SZURO, "posts_with_replies")
        with open(bsky_api.__file__, "r", encoding="utf-8") as f:
            self.assertIn('"filter": szuro', f.read())


class LefedetlenAblakTeszt(MenetAlap):

    def test_lefedetlen_ablaknal_nem_posztolunk(self):
        """FAIL-CLOSED: if the ceiling cannot be verified, nothing goes out."""
        self.halo.ablak_lefedve = False
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        with self.assertRaises(motor.UzemHiba):
            self.kor()
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(self.halo.letrehozasok, [])


# =========================================================================
# Kö1 — THE DRAFT IS FIRST MOVED INTO THE PROTECTED QUEUE
# =========================================================================

class VarakozoTeszt(MenetAlap):

    def test_a_bedobo_mappa_minden_korben_kiurul(self):
        for i in range(3):
            kozos_teszt.piszkozat_ir(self.k, "p%d.json" % i,
                                     kozos_teszt.alap_piszkozat(
                                         szoveg="Mondat száma: %d." % i))
        self.kor()
        self.assertEqual(os.listdir(self.k.ki), [])

    def test_a_beolvasas_a_vedett_mappabol_tortenik(self):
        """Rewriting the dropped file DURING the round no longer matters: by
        the time we read, the file is already in the protected queue, under
        another name."""
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat(
            szoveg="Az eredeti, jóváhagyandó mondat."))
        uzenetek = []
        motor.bevetel(self.k, uzenetek, time.time())
        self.assertEqual(os.listdir(self.k.ki), [])
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)
        # the untrusted side drops a new file under the same name
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat(
            szoveg="BECSEMPÉSZETT, egészen más mondat."))
        self.kor()
        szovegek = [u["szoveg"] for u in self.bot.kuldott]
        self.assertTrue(any("Az eredeti, jóváhagyandó mondat." in s
                            for s in szovegek))

    def test_halasztott_piszkozat_a_varakozoban_marad(self):
        self.halo.feed_elemek = [{
            "post": {"uri": "at://x/app.bsky.feed.post/1",
                     "author": {"did": kozos_teszt.SAJAT_DID},
                     "record": {"text": "friss",
                                "createdAt": time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}}]
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        self.kor()
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(os.listdir(self.k.ki), [])
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)

    def test_elavult_varakozo_lezarul(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        regen = time.time() - beallitasok.VARO_ELAVULAS - 100
        motor.bevetel(self.k, [], regen)
        adat = self.kor()
        self.assertEqual(motor.varo_fajlok(self.k), [])
        self.assertTrue(any("stale draft" in u
                            for u in adat["uzenetek"]), adat["uzenetek"])

    def test_nem_regularis_fajl_ki_sem_nyilik(self):
        """A''4: symbolic links are not followed. If we cannot create one
        (no Developer Mode), the test is skipped."""
        cel = os.path.join(self.k.titok, "or_bot.env")
        with open(cel, "w", encoding="utf-8") as f:
            f.write("OR_BOT_TOKEN=titkos\n")
        link = os.path.join(self.k.ki, "link.json")
        try:
            os.symlink(cel, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("a symbolic link cannot be created on this "
                          "machine without elevation")
        rendben, ok = piszkozat.szabalyos_bedobott_fajl(link)
        self.assertFalse(rendben)
        self.assertEqual(ok, "nem_regularis_fajl")

    def test_oriasfajl_olvasas_nelkul_elbukik(self):
        utvonal = os.path.join(self.k.ki, "nagy.json")
        with open(utvonal, "wb") as f:
            f.write(b"{" + b"a" * (beallitasok.PISZKOZAT_MAX_BAJT + 10))
        rendben, ok = piszkozat.szabalyos_bedobott_fajl(utvonal)
        self.assertFalse(rendben)
        self.assertEqual(ok, "tul_nagy_fajl")

    def test_koronkenti_darabszam_korlatos(self):
        """A''3: ten thousand dropped files must not swallow the round."""
        for i in range(beallitasok.PISZKOZAT_KORONKENT_MAX + 7):
            kozos_teszt.piszkozat_ir(self.k, "p%03d.json" % i,
                                     kozos_teszt.alap_piszkozat())
        talalt = piszkozat.piszkozat_fajlok(self.k.ki)
        self.assertEqual(len(talalt), beallitasok.PISZKOZAT_KORONKENT_MAX)


# =========================================================================
# Kö5 — CORRUPTED ALLOWLIST
# =========================================================================

class SerultEngedelylistaTeszt(MenetAlap):

    def test_serult_engedelylista_nem_allitja_meg_az_ort(self):
        with open(self.k.szivesen_latnak, "w", encoding="utf-8") as f:
            f.write("{ez nem szabályos json")
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        adat = self.kor()   # previously the guard exited with a ValueError
        self.assertEqual(len(self.bot.kuldott), 1)
        self.assertTrue(any("szivesen_latnak" in u for u in adat["uzenetek"]),
                        adat["uzenetek"])

    def test_serult_engedelylista_ures_listat_ad(self):
        with open(self.k.szivesen_latnak, "w", encoding="utf-8") as f:
            f.write("[[[")
        lista, megjegyzes = szures.engedelylista_biztos(self.k.szivesen_latnak)
        self.assertEqual(lista, [])
        self.assertIsNotNone(megjegyzes)

    def test_serult_engedelylistaval_nincs_kezdemenyezett_valasz(self):
        with open(self.k.szivesen_latnak, "w", encoding="utf-8") as f:
            f.write("nem json")
        self.halo.posztok = {kozos_teszt.CEL_URI:
                             kozos_teszt.cel_poszt(mention_did=None)}
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Érdekes!"))
        self.kor()
        self.assertEqual(self.bot.kuldott, [])


# =========================================================================
# LOW findings
# =========================================================================

class ZarfajlPidTeszt(unittest.TestCase):

    CSV = '"python.exe","%d","Console","1","12 345 K"\n'

    def test_pontos_pid_egyezes(self):
        self.assertTrue(tarolas_or.fut_e_a_folyamat(
            4321, lambda _p: self.CSV % 4321))

    def test_memoria_oszlopban_szereplo_szam_nem_talalat(self):
        """A''1: because of the `12 345 K` memory column, PID 12345 used to
        look like a "running process", and the stuck lock paralysed the
        guard forever."""
        kimenet = '"python.exe","4321","Console","1","12 345 K"\n'
        self.assertFalse(tarolas_or.fut_e_a_folyamat(
            12345, lambda _p: kimenet))

    def test_nincs_talalat_eseten_nem_fut(self):
        self.assertFalse(tarolas_or.fut_e_a_folyamat(4321, lambda _p: ""))

    def test_info_uzenet_eseten_nem_fut(self):
        self.assertFalse(tarolas_or.fut_e_a_folyamat(
            4321, lambda _p: "INFO: No tasks are running which match.\n"))

    def test_lekerdezhetetlenseg_eseten_fut(self):
        def robbano(_p):
            raise OSError("no tasklist")
        self.assertTrue(tarolas_or.fut_e_a_folyamat(4321, robbano))


class IsmeretlenIdoTeszt(unittest.TestCase):

    def test_ismeretlen_ido_nem_valt_ki_perc_limitet(self):
        """A''2: a single own post with an unparseable `createdAt` field
        used to block posting FOREVER."""
        most = time.time()
        posztok = [{"ts": None, "szoveg": "x", "szulo_uri": "", "uri": ""}]
        okok = kimeno.plafon_ellenorzes(posztok, most, kozos_teszt.SAJAT_DID,
                                        "valami-kulcs")
        self.assertNotIn("perc_limit", okok)

    def test_ismeretlen_ido_a_napi_plafonban_benne_van(self):
        most = time.time()
        posztok = [{"ts": None, "szoveg": "x%d" % i, "szulo_uri": "", "uri": ""}
                   for i in range(beallitasok.NAPI_MAX)]
        okok = kimeno.plafon_ellenorzes(posztok, most, kozos_teszt.SAJAT_DID,
                                        "valami-kulcs")
        self.assertIn("napi_limit", okok)

    def test_lathato_uzenet_keszul_rola(self):
        posztok = [{"ts": None}, {"ts": 1.0}, {"ts": None}]
        self.assertEqual(kimeno.ismeretlen_ideju_darab(posztok), 2)


class UzenetAzAllapotfajlbanTeszt(MenetAlap):

    def test_ismeretlen_ideju_poszt_uzenetet_ad(self):
        self.halo.feed_elemek = [{
            "post": {"uri": "at://x/y/z",
                     "author": {"did": kozos_teszt.SAJAT_DID},
                     "record": {"text": "x", "createdAt": "nem egy dátum"}}}]
        adat = self.kor()
        self.assertTrue(any("cannot be parsed" in u for u in adat["uzenetek"]),
                        adat["uzenetek"])
        with open(self.k.allapot_kifele, "r", encoding="utf-8") as f:
            self.assertIn("cannot be parsed", json.load(f)["uzenetek"][0])


if __name__ == "__main__":
    unittest.main()
