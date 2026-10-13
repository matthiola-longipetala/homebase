"""The STRICT schema of the draft. The draft is written by the untrusted side,
so every test was written with an attacker's eye: if a test fails, the bypass
works."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kozos_teszt  # noqa: E402,F401  (sys.path setup)
import piszkozat  # noqa: E402


class SemaTeszt(unittest.TestCase):

    def test_ok_ervenyes_poszt(self):
        tetel, okok = piszkozat.ertelmez(
            json.dumps(kozos_teszt.alap_piszkozat()))
        self.assertEqual(okok, [])
        self.assertEqual(tetel["tipus"], "poszt")
        self.assertIsNone(tetel["valasz_cel_uri"])

    def test_ok_ervenyes_valasz(self):
        tetel, okok = piszkozat.ertelmez(json.dumps(
            kozos_teszt.alap_piszkozat(tipus="valasz",
                                       cel_uri=kozos_teszt.CEL_URI)))
        self.assertEqual(okok, [])
        self.assertEqual(tetel["valasz_cel_uri"], kozos_teszt.CEL_URI)

    def test_ismeretlen_mezo_elutasitva(self):
        adat = kozos_teszt.alap_piszkozat()
        adat["cid"] = "sajat_cid_amit_en_adok"
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertTrue(any(o.startswith("ismeretlen_mezo") for o in okok))

    def test_hianyzo_mezo_elutasitva(self):
        adat = kozos_teszt.alap_piszkozat()
        del adat["langs"]
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("hianyzo_mezo:langs", okok)

    def test_nem_szabalyos_json(self):
        _, okok = piszkozat.ertelmez("{ez nem json}")
        self.assertEqual(okok, ["nem_szabalyos_json"])

    def test_nem_objektum(self):
        _, okok = piszkozat.ertelmez("[1, 2, 3]")
        self.assertEqual(okok, ["nem_objektum"])

    def test_verzio_bool_nem_szam(self):
        adat = kozos_teszt.alap_piszkozat()
        adat["verzio"] = True
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("rossz_verzio", okok)

    def test_ismeretlen_tipus(self):
        adat = kozos_teszt.alap_piszkozat(tipus="repost")
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("rossz_tipus", okok)

    def test_poszt_tipus_cellel_elutasitva(self):
        """Finding K1: `tipus: poszt` + a target switched off the opt-in."""
        adat = kozos_teszt.alap_piszkozat(tipus="poszt",
                                          cel_uri=kozos_teszt.CEL_URI)
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("tipus_cel_ellentmondas", okok)

    def test_valasz_cel_nelkul_elutasitva(self):
        adat = kozos_teszt.alap_piszkozat(tipus="valasz", cel_uri=None)
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("valasz_cel_nelkul", okok)

    def test_rossz_uri_alak(self):
        for rossz in ("https://example.invalid/x",
                      "at://nemdid/app.bsky.feed.post/x",
                      "at://did:plc:x/app.bsky.feed.post/x\nkulso adat",
                      "at://did:plc:x"):
            adat = kozos_teszt.alap_piszkozat(tipus="valasz", cel_uri=rossz)
            _, okok = piszkozat.ertelmez(json.dumps(adat))
            self.assertIn("rossz_valasz_cel_uri", okok, rossz)

    def test_rossz_langs(self):
        for rossz in ([], "hu", ["hu", "en", "de", "fr"], [1], ["magyarul"]):
            adat = kozos_teszt.alap_piszkozat()
            adat["langs"] = rossz
            _, okok = piszkozat.ertelmez(json.dumps(adat))
            self.assertIn("rossz_langs", okok, repr(rossz))

    def test_tul_hosszu_megjegyzes(self):
        adat = kozos_teszt.alap_piszkozat(megjegyzes="x" * 201)
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("tul_hosszu_megjegyzes", okok)

    def test_ures_szoveg(self):
        adat = kozos_teszt.alap_piszkozat(szoveg="   \n  ")
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("ures_szoveg", okok)

    def test_atirando_szoveg_elutasitva(self):
        """M''2: with zero-width and right-to-left characters we do NOT silently
        rewrite the text but reject it — otherwise the preview would not be
        byte-for-byte identical to what goes out."""
        adat = kozos_teszt.alap_piszkozat(szoveg="Szia​‮alma")
        tetel, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("szoveg_atirasra_szorulna", okok)
        self.assertIsNone(tetel)

    def test_tobbsoros_megjegyzes_elutasitva(self):
        """Kö2: the note sits directly above the approval line, so it
        must not draw a paragraph."""
        adat = kozos_teszt.alap_piszkozat(
            megjegyzes="első sor\nmásodik sor\nharmadik sor")
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("tobbsoros_vagy_atirando_megjegyzes", okok)

    def test_nyolcvan_karakternel_hosszabb_megjegyzes_elutasitva(self):
        adat = kozos_teszt.alap_piszkozat(megjegyzes="x" * 81)
        _, okok = piszkozat.ertelmez(json.dumps(adat))
        self.assertIn("tul_hosszu_megjegyzes", okok)


class FajlTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def test_tul_nagy_fajl(self):
        utvonal = os.path.join(self.k.ki, "nagy.json")
        with open(utvonal, "wb") as f:
            f.write(b"{\"x\": \"" + b"a" * 9000 + b"\"}")
        _, okok = piszkozat.fajlbol(utvonal)
        self.assertEqual(okok, ["tul_nagy_fajl"])

    def test_nem_utf8(self):
        utvonal = os.path.join(self.k.ki, "rossz.json")
        with open(utvonal, "wb") as f:
            f.write(b"{\"szoveg\": \"\xff\xfe\xfa\"}")
        _, okok = piszkozat.fajlbol(utvonal)
        self.assertEqual(okok, ["nem_utf8"])

    def test_ures_fajl(self):
        utvonal = os.path.join(self.k.ki, "ures.json")
        open(utvonal, "wb").close()
        _, okok = piszkozat.fajlbol(utvonal)
        self.assertEqual(okok, ["ures_fajl"])

    def test_csak_json_fajlokat_nezunk(self):
        kozos_teszt.piszkozat_ir(self.k, "jo.json",
                                 kozos_teszt.alap_piszkozat())
        with open(os.path.join(self.k.ki, "nem.txt"), "w",
                  encoding="utf-8") as f:
            f.write("{}")
        talalt = piszkozat.piszkozat_fajlok(self.k.ki)
        self.assertEqual([os.path.basename(t) for t in talalt], ["jo.json"])


if __name__ == "__main__":
    unittest.main()
