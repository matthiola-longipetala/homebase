"""test_szaraz_homokozo.py — the `--szaraz` run does NOT touch the live state.

Finding of 2026-09-22: the dry run used the live folders, so it actually took
over a dropped draft (deleted it from the drop folder, recorded a preview with
an invented Telegram id into the live state) and overwrote
`kozos/be/allapot.json`. The live guard then waited 48 hours for approval of a
preview that was never sent. The fix: after the permission gate the dry run
runs in a separate sandbox (`allapot_szaraz/`); only a COPY of the drop folder
is made.
"""

import importlib
import json
import os
import unittest

import kozos_teszt
import motor


class SzarazHomokozoTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.or_modul = importlib.import_module("or")
        # Live state: one dropped draft + one live status report.
        kozos_teszt.piszkozat_ir(self.k, "eles.json",
                                 kozos_teszt.alap_piszkozat())
        with open(self.k.allapot_kifele, "w", encoding="utf-8") as f:
            json.dump({"or_allapot": "eles-jelzo"}, f)
        self.eles_piszkozat = os.path.join(self.k.ki, "eles.json")

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def _szaraz_kor(self):
        kiirt = []
        h = self.or_modul.szaraz_homokozo_elokeszit(self.k, kiirt.append)
        halo = kozos_teszt.MockHalo()
        bot = motor.SzarazBot(kiirt.append)
        motor.egy_kor(h, halo, bot, bejovo_is=False)
        return h, halo, kiirt

    def test_homokozo_kulon_mappaban_van(self):
        h = self.k.szaraz_homokozo()
        self.assertNotEqual(h.allapot, self.k.allapot)
        self.assertTrue(h.allapot.endswith("allapot_szaraz"))
        for mezo in ("elonezetek", "feldolgozott", "varo", "archiv", "zart",
                     "naplo", "offset", "felhasznalt", "kihagyando",
                     "bedobott_kulcs", "bedobott_hashek", "inbox",
                     "allapot_kifele", "be", "ki"):
            self.assertTrue(getattr(h, mezo).startswith(h.allapot),
                            "%s is not inside the sandbox" % mezo)
        # The secret and program paths are UNCHANGED — the trial tests those.
        self.assertEqual(h.titok, self.k.titok)
        self.assertEqual(h.program, self.k.program)
        self.assertEqual(h.szivesen_latnak, self.k.szivesen_latnak)
        self.assertEqual(h.tilto_mintak, self.k.tilto_mintak)

    def test_szaraz_kor_nem_nyul_az_eles_bedobohoz(self):
        h, halo, kiirt = self._szaraz_kor()
        # In the sandbox the COPY was processed: a preview was made…
        self.assertTrue(any("Jóváhagyás: válaszolj" in s for s in kiirt))
        self.assertTrue(os.listdir(h.elonezetek))
        # …but the draft is still untouched in the LIVE drop folder.
        self.assertTrue(os.path.isfile(self.eles_piszkozat))
        # The live preview folder stayed empty, the live status report is the old one.
        self.assertEqual(
            [n for n in os.listdir(self.k.elonezetek) if n.endswith(".json")],
            [])
        with open(self.k.allapot_kifele, encoding="utf-8") as f:
            self.assertEqual(json.load(f).get("or_allapot"), "eles-jelzo")
        # Nothing went out.
        self.assertEqual(halo.letrehozasok, [])

    def test_homokozo_zart_mappaja_sajat_kulcsot_kap(self):
        h, _halo, _kiirt = self._szaraz_kor()
        self.assertTrue(os.path.isfile(h.bedobott_kulcs))
        self.assertFalse(os.path.exists(self.k.bedobott_kulcs),
                         "the dry run wrote a key into the LIVE locked folder")

    def test_masodik_szaraz_kor_friss_masolatot_kap(self):
        """The sandbox does not accumulate junk: every start is a fresh snapshot."""
        h1, _h, _k = self._szaraz_kor()
        elso = sorted(os.listdir(h1.elonezetek))
        h2, _h, _k = self._szaraz_kor()
        masodik = sorted(os.listdir(h2.elonezetek))
        # The second round does NOT see the sandbox record made in the first (the
        # snapshot of the LIVE records is empty), so it also ends up with one record.
        self.assertEqual(len(elso), 1)
        self.assertEqual(len(masodik), 1)


if __name__ == "__main__":
    unittest.main()
