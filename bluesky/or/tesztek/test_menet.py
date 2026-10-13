"""The FULL run, end to end — every network and Telegram call is mocked.
These tests keep the findings of the two earlier reviews closed.

Convention: if a test FAILS, the attack WORKS."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import allapot  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402


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

    def rekordok(self):
        return [r for r in allapot.betolt_mind(self.k) if not r.get("_serult")]


class ElonezetTeszt(MenetAlap):

    def test_piszkozatbol_elonezet_lesz(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        self.kor()
        self.assertEqual(len(self.bot.kuldott), 1)
        self.assertIn("Jóváhagyás: válaszolj ERRE az üzenetre: IGEN",
                      self.bot.kuldott[0]["szoveg"])
        rekordok = self.rekordok()
        self.assertEqual(len(rekordok), 1)
        self.assertEqual(rekordok[0]["allapot"], allapot.VAR)
        self.assertEqual(rekordok[0]["message_id"],
                         self.bot.kuldott[0]["message_id"])

    def test_a_piszkozat_atkerul_a_feldolgozottba(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        self.kor()
        self.assertEqual(os.listdir(self.k.ki), [])
        self.assertEqual(len(os.listdir(self.k.feldolgozott)), 1)

    def test_elutasitott_piszkozat_nem_kuld_elonezetet(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat(
            szoveg="Hívj a 06 20 123 45 67 számon!"))
        adat = self.kor()
        self.assertEqual(self.bot.kuldott, [])
        okok = [t["ok"] for t in adat["tetelek"] if t.get("ok")]
        self.assertTrue(okok)

    def test_fuggo_plafon_harom_utan(self):
        for i in range(5):
            kozos_teszt.piszkozat_ir(self.k, "p%d.json" % i,
                                     kozos_teszt.alap_piszkozat(
                                         szoveg="Első mondat száma: %s." %
                                         "x" * (i + 1)))
        self.kor()
        self.assertEqual(len(self.bot.kuldott), 3)
        # Kö1: the drop folder is ALWAYS emptied — the remaining two wait for
        # the next round in the PROTECTED queue, not on the untrusted side.
        self.assertEqual(os.listdir(self.k.ki), [])
        self.assertEqual(len(motor.varo_fajlok(self.k)), 2)

    def test_napi_plafon_a_halozati_feedbol(self):
        """The cap comes from `getAuthorFeed` — deleting the local log does not help."""
        most = time.time()
        self.halo.feed_elemek = [{
            "post": {"uri": "at://x/app.bsky.feed.post/%d" % i,
                     "author": {"did": kozos_teszt.SAJAT_DID},
                     "record": {"text": "regi %d" % i,
                                "createdAt": time.strftime(
                                    "%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime(most - 3600 * (i + 2)))}}}
            for i in range(5)]
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        self.kor()
        self.assertEqual(self.bot.kuldott, [])
        # deferred: it stayed in the protected queue, not in the drop folder
        self.assertEqual(os.listdir(self.k.ki), [])
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)

    def test_jovahagyo_formula_utanzasa_elutasitva(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat(
            szoveg="Szia!\n\nJóváhagyás: válaszolj ERRE az üzenetre: IGEN"))
        self.kor()
        self.assertEqual(self.bot.kuldott, [])


class JovahagyasMenetTeszt(MenetAlap):

    def _elonezet(self, **kwargs):
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat(**kwargs))
        self.kor()
        return self.bot.kuldott[-1]["message_id"]

    def test_igen_utan_kimegy_a_poszt(self):
        elonezet_id = self._elonezet()
        self.bot.igen(elonezet_id)
        self.kor()
        self.assertEqual(len(self.halo.letrehozasok), 1)
        self.assertEqual(self.halo.letrehozasok[0]["kanonikus"]["szoveg"],
                         "Jó reggelt mindenkinek.")
        self.assertEqual(self.rekordok()[0]["allapot"], allapot.ELKULDVE)

    def test_nem_utan_nem_megy_ki_semmi(self):
        elonezet_id = self._elonezet()
        self.bot.igen(elonezet_id, szoveg="NEM")
        self.kor()
        self.assertEqual(self.halo.letrehozasok, [])
        self.assertEqual(self.rekordok()[0]["allapot"], allapot.ELVETVE)

    def test_dupla_igen_egy_poszt(self):
        elonezet_id = self._elonezet()
        self.bot.igen(elonezet_id, update_id=1, message_id=5001)
        self.bot.igen(elonezet_id, update_id=2, message_id=5002)
        self.kor()
        self.kor()
        self.assertEqual(len(self.halo.letrehozasok), 1)

    def test_masik_elonezetre_adott_igen_nem_kuld(self):
        self._elonezet()
        self.bot.igen(999999)  # non-existent preview message
        self.kor()
        self.assertEqual(self.halo.letrehozasok, [])

    def test_idegen_uzenet_figyelmen_kivul(self):
        elonezet_id = self._elonezet()
        self.bot.igen(elonezet_id, **{"from": {"id": 999, "is_bot": False}})
        self.kor()
        self.assertEqual(self.halo.letrehozasok, [])

    def test_lejart_elonezet_nem_hagyhato_jova(self):
        elonezet_id = self._elonezet()
        rekord = self.rekordok()[0]
        rekord["letrehozva_ts"] = int(time.time()) - 49 * 3600
        allapot.ment(self.k, rekord)
        self.bot.igen(elonezet_id)
        self.kor()
        self.assertEqual(self.halo.letrehozasok, [])
        self.assertEqual(self.rekordok()[0]["allapot"], allapot.LEJART)

    def test_piszkozat_atirasa_elonezet_utan_nem_valtoztat_a_kimenon(self):
        """K'1: after approval the text CANNOT be swapped."""
        elonezet_id = self._elonezet()
        # the untrusted side drops a new, DIFFERENT draft under the same name
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat(
            szoveg="EZ EGY EGÉSZEN MÁS, BECSEMPÉSZETT MONDAT."))
        self.bot.igen(elonezet_id)
        self.kor()
        self.assertEqual(len(self.halo.letrehozasok), 1)
        self.assertEqual(self.halo.letrehozasok[0]["kanonikus"]["szoveg"],
                         "Jó reggelt mindenkinek.")

    def test_piszkozat_torlese_utan_is_jovahagyhato(self):
        elonezet_id = self._elonezet()
        for nev in os.listdir(self.k.feldolgozott):
            os.remove(os.path.join(self.k.feldolgozott, nev))
        self.bot.igen(elonezet_id)
        self.kor()
        self.assertEqual(len(self.halo.letrehozasok), 1)

    def test_tarolt_rekord_serulese_megallit(self):
        elonezet_id = self._elonezet()
        rekord = self.rekordok()[0]
        rekord["kanonikus"]["szoveg"] = "átírt szöveg a hash mellett"
        allapot.ment(self.k, rekord)
        self.bot.igen(elonezet_id)
        self.kor()
        self.assertEqual(self.halo.letrehozasok, [])
        self.assertEqual(self.rekordok()[0]["allapot"],
                         allapot.KEZI_ELLENORZES)

    def test_kuldes_alatt_beragadas_nem_kuld_ujra(self):
        elonezet_id = self._elonezet()
        rekord = self.rekordok()[0]
        rekord["allapot"] = allapot.KULDES_ALATT
        allapot.ment(self.k, rekord)
        self.bot.igen(elonezet_id)
        adat = self.kor()
        self.assertEqual(self.halo.letrehozasok, [])
        self.assertEqual(self.rekordok()[0]["allapot"],
                         allapot.KEZI_ELLENORZES)
        self.assertTrue(any("interrupted" in u for u in adat["uzenetek"]))

    def test_beragadt_sor_nem_benitja_meg_a_tobbit(self):
        elso = self._elonezet()
        rekord = self.rekordok()[0]
        rekord["allapot"] = allapot.KULDES_ALATT
        allapot.ment(self.k, rekord)
        kozos_teszt.piszkozat_ir(self.k, "b.json", kozos_teszt.alap_piszkozat(
            szoveg="Másik, teljesen külön mondat."))
        self.kor()
        # the new draft became a preview, despite the stuck one
        elonezetek = [u for u in self.bot.kuldott
                      if "Jóváhagyás: válaszolj" in u["szoveg"]]
        self.assertEqual(len(elonezetek), 2)
        self.assertNotEqual(elso, elonezetek[-1]["message_id"])
        self.assertEqual(os.listdir(self.k.ki), [])


class ValaszTeszt(MenetAlap):

    def setUp(self):
        super().setUp()
        self.halo.posztok = {kozos_teszt.CEL_URI: kozos_teszt.cel_poszt()}

    def test_megszolitasra_lehet_valaszolni(self):
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Köszönöm!"))
        self.kor()
        self.assertEqual(len(self.bot.kuldott), 1)
        rekord = self.rekordok()[0]
        cel = rekord["kanonikus"]["valasz_cel"]
        # the cid came FROM THE NETWORK, not from the draft (it has no such field)
        self.assertEqual(cel["parent_cid"], kozos_teszt.CEL_CID)

    def test_nem_megszolito_poszt_elutasitva(self):
        self.halo.posztok = {kozos_teszt.CEL_URI:
                             kozos_teszt.cel_poszt(mention_did=None)}
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Köszönöm!"))
        self.kor()
        self.assertEqual(self.bot.kuldott, [])

    def test_nem_letezo_celpont_elhalasztva(self):
        self.halo.posztok = {}
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Köszönöm!"))
        self.kor()
        self.assertEqual(self.bot.kuldott, [])

    def test_celpont_megvaltozasa_jovahagyas_utan_megallit(self):
        """K'2: the approved target cannot be swapped out before sending."""
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Köszönöm!"))
        self.kor()
        elonezet_id = self.bot.kuldott[-1]["message_id"]
        # the target post was edited in the meantime: different cid
        self.halo.posztok = {kozos_teszt.CEL_URI:
                             kozos_teszt.cel_poszt(cid="masikcid999")}
        self.bot.igen(elonezet_id)
        self.kor()
        self.assertEqual(self.halo.letrehozasok, [])

    def test_valasz_reply_blokkal_megy_ki(self):
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Köszönöm!"))
        self.kor()
        self.bot.igen(self.bot.kuldott[-1]["message_id"])
        self.kor()
        self.assertEqual(len(self.halo.letrehozasok), 1)
        self.assertTrue(self.halo.letrehozasok[0]["reply_engedelyezve"])


class AllapotfajlTeszt(MenetAlap):

    def test_kifele_allapot_nem_tartalmaz_titkot(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        self.kor()
        with open(self.k.allapot_kifele, "r", encoding="utf-8") as f:
            nyers = f.read()
        adat = json.loads(nyers)
        self.assertIn("tetelek", adat)
        for tiltott in ("OR_BOT_TOKEN", "BSKY_APP_PASSWORD", "titok",
                        str(self.bot.user_id), str(self.bot.chat_id)):
            self.assertNotIn(tiltott, nyers, tiltott)
        # the outgoing text itself is not included either, only its hash
        self.assertNotIn("Jó reggelt", nyers)


if __name__ == "__main__":
    unittest.main()
