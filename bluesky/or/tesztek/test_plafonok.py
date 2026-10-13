"""Caps from the NETWORK truth, allowlisted (initiated) reply,
and the structural guard against the `reply` block."""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bsky_api  # noqa: E402
import kimeno  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402
import szures  # noqa: E402


def feed_elem(szoveg, kor_masodperc, most, szulo_uri=""):
    rekord = {"text": szoveg,
              "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(most - kor_masodperc))}
    if szulo_uri:
        rekord["reply"] = {"parent": {"uri": szulo_uri},
                           "root": {"uri": szulo_uri}}
    return {"post": {"uri": "at://x/app.bsky.feed.post/%d" % kor_masodperc,
                     "author": {"did": kozos_teszt.SAJAT_DID},
                     "record": rekord}}


class PlafonTeszt(unittest.TestCase):

    def setUp(self):
        self.most = time.time()

    def _posztok(self, elemek):
        return kimeno.sajat_posztok_a_feedbol({"feed": elemek},
                                              kozos_teszt.SAJAT_DID)

    def test_perc_limit(self):
        posztok = self._posztok([feed_elem("friss", 60, self.most)])
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "másik-kulcs")
        self.assertIn("perc_limit", okok)

    def test_nincs_limit_ha_regi(self):
        posztok = self._posztok([feed_elem("regi", 3 * 3600, self.most)])
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "másik-kulcs")
        self.assertEqual(okok, [])

    def test_napi_limit(self):
        posztok = self._posztok([feed_elem("p%d" % i, 3600 * (i + 2), self.most)
                                 for i in range(5)])
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "másik-kulcs")
        self.assertIn("napi_limit", okok)

    def test_dupla_poszt(self):
        posztok = self._posztok([feed_elem("Ugyanaz a mondat.", 3 * 3600,
                                           self.most)])
        okok = kimeno.plafon_ellenorzes(
            posztok, self.most, kozos_teszt.SAJAT_DID,
            szures.dedup_kulcs("Ugyanaz a mondat."))
        self.assertIn("dupla_poszt", okok)

    def test_dedup_nem_kerulheto_meg_nem_toro_szokozzel(self):
        posztok = self._posztok([feed_elem("Ugyanaz a mondat.", 3 * 3600,
                                           self.most)])
        okok = kimeno.plafon_ellenorzes(
            posztok, self.most, kozos_teszt.SAJAT_DID,
            szures.dedup_kulcs("Ugyanaz a mondat."))
        self.assertIn("dupla_poszt", okok)

    def test_kezdemenyezett_napi_limit(self):
        posztok = self._posztok([
            feed_elem("v%d" % i, 3600 * (i + 2), self.most,
                      szulo_uri="at://did:plc:masvalaki/app.bsky.feed.post/x%d" % i)
            for i in range(2)])
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "másik-kulcs",
                                        kezdemenyezett=True)
        self.assertIn("kezdemenyezett_napi_limit", okok)

    def test_ismeretlen_ido_beleszamit(self):
        """Unknown timestamp → counts as inside the 24-hour window (stricter)."""
        elem = {"post": {"uri": "at://x/y/z",
                         "author": {"did": kozos_teszt.SAJAT_DID},
                         "record": {"text": "x"}}}
        posztok = self._posztok([elem] * 5)
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "k")
        self.assertIn("napi_limit", okok)

    def test_repostot_nem_szamolunk(self):
        elem = feed_elem("x", 60, self.most)
        elem["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
        self.assertEqual(self._posztok([elem]), [])


class KezdemenyezettTeszt(unittest.TestCase):
    """The path of the allowlisted (INITIATED) reply."""

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.halo = kozos_teszt.MockHalo()
        self.bot = kozos_teszt.MockBot()
        self.halo.posztok = {kozos_teszt.CEL_URI:
                             kozos_teszt.cel_poszt(mention_did=None)}

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def _engedelylista(self, didek):
        with open(self.k.szivesen_latnak, "w", encoding="utf-8") as f:
            json.dump({"fiokok": [{"did": d} for d in didek]}, f)

    def test_engedelylistas_szerzore_lehet_kezdemenyezni(self):
        self._engedelylista([kozos_teszt.IDEGEN_DID])
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Érdekes!"))
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(len(self.bot.kuldott), 1)
        self.assertIn("UNSOLICITED REPLY", self.bot.kuldott[0]["szoveg"])

    def test_engedelylista_nelkul_nincs_kezdemenyezes(self):
        self._engedelylista([])
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Érdekes!"))
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(self.bot.kuldott, [])

    def test_handle_nem_eleg_csak_did(self):
        with open(self.k.szivesen_latnak, "w", encoding="utf-8") as f:
            json.dump({"fiokok": [{"handle": "idegen.bsky.social", "did": ""}]}, f)
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg="Érdekes!"))
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(self.bot.kuldott, [])


class SzerkezetiVedelemTeszt(unittest.TestCase):

    def test_poszt_tipus_nem_kaphat_reply_blokkot(self):
        kanonikus = szures.kimeno_kanonikus(
            "poszt", "x", {"uri": "at://a/b/c", "cid": "d"}, ["hu"], False)
        with self.assertRaises(ValueError):
            bsky_api.poszt_letrehozas("token", "did", kanonikus, "most",
                                      reply_engedelyezve=True)

    def test_reply_engedely_nelkul_nem_megy_ki_valasz(self):
        kanonikus = szures.kimeno_kanonikus(
            "valasz", "x", {"uri": "at://a/b/c", "cid": "d"}, ["hu"], False)
        with self.assertRaises(ValueError):
            bsky_api.poszt_letrehozas("token", "did", kanonikus, "most",
                                      reply_engedelyezve=False)


class SzarazTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def test_szaraz_mod_semmit_nem_kuld_ki(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        halo = kozos_teszt.MockHalo()
        kiirt = []
        bot = motor.SzarazBot(kiirt.append)
        motor.egy_kor(self.k, halo, bot, bejovo_is=False)
        self.assertEqual(halo.letrehozasok, [])
        self.assertTrue(any("DRY RUN" in s for s in kiirt))
        self.assertTrue(any("Jóváhagyás: válaszolj" in s for s in kiirt))


if __name__ == "__main__":
    unittest.main()
