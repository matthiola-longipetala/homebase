"""Regression tests for the remaining findings of the FOURTH review (ATNEZES4).

This holds every finding whose subject is neither the thread root nor the
permission check:

  * Kö'''1 — emoji sequences must not trip the byte-exactness gate, but an
    invisible character between letters is still rejected;
  * Kö'''2 — a FOLDER placed in the drop folder must not get stuck or use up
    the quota;
  * Kö'''4 — after an interruption there must not be two previews of the same
    draft;
  * M'''0/a — the fingerprint comparison must look at exactly the files that
    get copied over, and be able to report that EVERYTHING MATCHES;
  * M'''2 — intake is protected against the drop folder being a junction point.

Convention: if a test FAILS, the finding has come back."""

import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kimeno  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402
import piszkozat  # noqa: E402
import szures  # noqa: E402
import ujjlenyomat  # noqa: E402

BARACK = "\U0001F351"                       # 🍑
CSALAD = "\U0001F468‍\U0001F469‍\U0001F467"   # family emoji, with ZWJ
ZASZLO = "\U0001F1ED\U0001F1FA"             # flag (two regional indicators)
SZIV = "❤️"                       # heart with variation selector
MUNKAS = "\U0001F469‍\U0001F4BB"       # profession emoji, with ZWJ
BORSZIN = "\U0001F44D\U0001F3FD"            # with skin-tone modifier


class EmojiTeszt(unittest.TestCase):
    """Kö'''1: a legitimate emoji sequence MUST PASS, a hidden character must NOT."""

    def _atirna_e(self, szoveg):
        """Would the byte-exactness gate catch this text?"""
        return bool(kimeno.megjelenites_szabalyok(szoveg))

    def test_egyszeru_emoji_atmegy(self):
        self.assertFalse(self._atirna_e("Jó reggelt! " + BARACK))

    def test_csalad_emoji_atmegy(self):
        """This was the actual finding: the family emoji joined with ZWJ."""
        self.assertFalse(self._atirna_e("Szép napot " + CSALAD))

    def test_foglalkozas_emoji_atmegy(self):
        self.assertFalse(self._atirna_e("Dolgozom " + MUNKAS))

    def test_zaszlo_atmegy(self):
        self.assertFalse(self._atirna_e("Üdv innen " + ZASZLO))

    def test_variaciovalaszto_atmegy(self):
        self.assertFalse(self._atirna_e("Köszi " + SZIV))

    def test_borszin_jelolo_atmegy(self):
        self.assertFalse(self._atirna_e("Remek " + BORSZIN))

    def test_a_szanitalas_valoban_nem_valtoztat_az_emojin(self):
        for szoveg in (BARACK, CSALAD, ZASZLO, SZIV, MUNKAS, BORSZIN):
            self.assertEqual(szures.szanitalas(szoveg, 100), szoveg, szoveg)

    # --- the attacker side: invisible character BETWEEN LETTERS ----------

    def test_zwj_betuk_kozott_elutasitas(self):
        """A zero-width joiner between letters can be used to deceive."""
        self.assertTrue(self._atirna_e("pe‍bble"))

    def test_variaciovalaszto_betu_utan_elutasitas(self):
        self.assertTrue(self._atirna_e("pebble️"))

    def test_zwj_emoji_es_betu_kozott_elutasitas(self):
        self.assertTrue(self._atirna_e(BARACK + "‍pebble"))

    def test_egyeb_nulla_szelessegu_karakter_tovabbra_is_elutasitas(self):
        for rejtett in ("​", "‎", "‮", "﻿", "⁦"):
            self.assertTrue(self._atirna_e("pe" + rejtett + "bble"), rejtett)

    def test_teljes_menetben_kimegy_az_emojis_poszt(self):
        k = kozos_teszt.kornyezet_keszit()
        try:
            halo = kozos_teszt.MockHalo()
            bot = kozos_teszt.MockBot()
            szoveg = "Ma sütött a nap " + BARACK + " és jó volt " + CSALAD
            kozos_teszt.piszkozat_ir(k, "e.json",
                                     kozos_teszt.alap_piszkozat(szoveg=szoveg))
            motor.egy_kor(k, halo, bot, bejovo_is=False)
            self.assertEqual(len(bot.kuldott), 1)
            # The byte-exact preview contains this text VERBATIM.
            self.assertIn("\n" + szoveg + "\n=== VÉGE [",
                          bot.kuldott[0]["szoveg"])
        finally:
            kozos_teszt.kornyezet_torol(k)


class BedobottMappaTeszt(unittest.TestCase):
    """Kö'''2: a FOLDER placed in the drop folder must not get stuck."""

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.halo = kozos_teszt.MockHalo()
        self.bot = kozos_teszt.MockBot()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def test_ures_mappa_eltavolitasra_kerul(self):
        os.makedirs(os.path.join(self.k.ki, "0000_mappa.json"))
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(os.listdir(self.k.ki), [],
                         "the folder was left in the drop folder")

    def test_nem_ures_mappa_nehany_kor_utan_kihagyasra_kerul(self):
        mappa = os.path.join(self.k.ki, "0000_mappa.json")
        os.makedirs(mappa)
        with open(os.path.join(mappa, "valami.txt"), "w",
                  encoding="utf-8") as f:
            f.write("nem törölhető, mert nem üres")
        for _ in range(4):
            adat = motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertTrue(
            any("I SKIP IT" in u or "skipping them permanently" in u
                for u in adat["uzenetek"]), adat["uzenetek"])
        # And — this is the point — it does not use up the QUOTA: a real draft gets in.
        kozos_teszt.piszkozat_ir(self.k, "zzz.json",
                                 kozos_teszt.alap_piszkozat())
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(len(self.bot.kuldott), 1)

    def test_otven_mappa_nem_zarja_el_a_bedobot(self):
        """The actual denial of service: with fifty directories whose names
        sort early, the drop folder could fill up permanently."""
        for i in range(50):
            mappa = os.path.join(self.k.ki, "%04d.json" % i)
            os.makedirs(mappa)
            with open(os.path.join(mappa, "x.txt"), "w", encoding="utf-8") as f:
                f.write("nem üres")
        kozos_teszt.piszkozat_ir(self.k, "zzz.json",
                                 kozos_teszt.alap_piszkozat())
        for _ in range(4):
            motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(len(self.bot.kuldott), 1,
                         "the real draft did not get in")

    def test_a_kihagyando_lista_felso_korlatos(self):
        most = time.time()
        tabla = {"f%04d.json" % i: (5, int(most)) for i in range(500)}
        motor._kihagyando_ment(self.k, tabla)
        self.assertLessEqual(len(motor._kihagyando_betolt(self.k, most)),
                             motor.beallitasok.KIHAGYANDO_MAX)

    def test_piszkozat_fajlok_kihagyja_a_megjelolteket(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat())
        kozos_teszt.piszkozat_ir(self.k, "b.json", kozos_teszt.alap_piszkozat())
        maradt = piszkozat.piszkozat_fajlok(self.k.ki, kihagyando={"a.json"})
        self.assertEqual([os.path.basename(u) for u in maradt], ["b.json"])


class BedoboMappaVizsgalatTeszt(unittest.TestCase):
    """M'''2: the drop folder must not be a junction point or a link."""

    def setUp(self):
        self.mappa = tempfile.mkdtemp(prefix="or_bedobo_")

    def tearDown(self):
        shutil.rmtree(self.mappa, ignore_errors=True)

    def test_valodi_mappa_elfogadott(self):
        self.assertTrue(piszkozat.bedobo_valodi_mappa_e(self.mappa))

    def test_nem_letezo_mappa_elutasitva(self):
        self.assertFalse(piszkozat.bedobo_valodi_mappa_e(
            os.path.join(self.mappa, "nincs")))

    def test_fajl_nem_mappa(self):
        utvonal = os.path.join(self.mappa, "f.txt")
        with open(utvonal, "w", encoding="utf-8") as f:
            f.write("x")
        self.assertFalse(piszkozat.bedobo_valodi_mappa_e(utvonal))

    @unittest.skipUnless(sys.platform.startswith("win"),
                         "junction points are Windows-specific")
    def test_csatolasi_pont_elutasitva(self):
        import subprocess
        cel = os.path.join(self.mappa, "cel")
        link = os.path.join(self.mappa, "link")
        os.makedirs(cel)
        folyamat = subprocess.run(["cmd", "/c", "mklink", "/J", link, cel],
                                  capture_output=True, check=False)
        if folyamat.returncode != 0 or not os.path.exists(link):
            self.skipTest("could not create the junction point")
        self.assertFalse(piszkozat.bedobo_valodi_mappa_e(link))

    def test_a_bevetel_nem_vesz_be_hivatkozott_mappabol(self):
        k = kozos_teszt.kornyezet_keszit()
        try:
            uzenetek = []
            # We "hide" the drop folder: a non-existent path is not a real folder.
            k.ki = os.path.join(k.kozos, "nincs_ilyen")
            motor.bevetel(k, uzenetek, 0)
            self.assertTrue(any("not a real directory" in u for u in uzenetek),
                            uzenetek)
        finally:
            kozos_teszt.kornyezet_torol(k)


class DuplaElonezetTeszt(unittest.TestCase):
    """Kö'''4: the draft file is moved BEFORE THE PREVIEW IS SENT."""

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.halo = kozos_teszt.MockHalo()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def test_a_kuldes_kozbeni_megszakadas_utan_nincs_masodik_elonezet(self):
        class SzakadoBot(kozos_teszt.MockBot):
            def kuld(self, szoveg):
                super().kuld(szoveg)
                raise KeyboardInterrupt("the run is interrupted here")

        bot = SzakadoBot()
        kozos_teszt.piszkozat_ir(self.k, "p.json", kozos_teszt.alap_piszkozat())
        with self.assertRaises(KeyboardInterrupt):
            motor.egy_kor(self.k, self.halo, bot, bejovo_is=False)
        self.assertEqual(len(bot.kuldott), 1)
        # The waiting and drop folders are both EMPTY: the file is already in processed.
        self.assertEqual(motor.varo_fajlok(self.k), [])
        self.assertEqual(piszkozat.piszkozat_fajlok(self.k.ki), [])
        # The next round does NOT send a second preview of the same draft.
        uj_bot = kozos_teszt.MockBot()
        motor.egy_kor(self.k, self.halo, uj_bot, bejovo_is=False)
        self.assertEqual(uj_bot.kuldott, [])


class UjjlenyomatTeszt(unittest.TestCase):
    """M'''0/a: the comparison must be able to report EVERYTHING MATCHES."""

    def setUp(self):
        self.mappa = tempfile.mkdtemp(prefix="or_lenyomat_")
        self.cel = tempfile.mkdtemp(prefix="or_program_")
        for nev in ("or.py", "motor.py", "tilto_mintak.json",
                    "szivesen_latnak.json", "README_or.md",
                    "INSTALL.md", "bsky_kulcs.env.MINTA",
                    "or_bot.env.MINTA"):
            with open(os.path.join(self.mappa, nev), "w",
                      encoding="utf-8") as f:
                f.write("# %s\n" % nev)

    def tearDown(self):
        shutil.rmtree(self.mappa, ignore_errors=True)
        shutil.rmtree(self.cel, ignore_errors=True)

    def _atmasol_a_programba(self):
        """We copy exactly what the installation guide has you copy."""
        for nev in os.listdir(self.mappa):
            if ujjlenyomat._telepitendo_e(nev):
                shutil.copy2(os.path.join(self.mappa, nev),
                             os.path.join(self.cel, nev))

    def test_csak_telepitendo_szuro_a_helyes_fajlokat_valasztja(self):
        lenyomatok = ujjlenyomat.mappa_lenyomatai(self.mappa,
                                                  csak_telepitendo=True)
        self.assertEqual(sorted(lenyomatok),
                         ["motor.py", "or.py", "szivesen_latnak.json",
                          "tilto_mintak.json"])

    def test_minden_egyezik_a_program_mappaval(self):
        self._atmasol_a_programba()
        lista = os.path.join(self.mappa, "lenyomat.txt")
        kimenet = []
        kod = ujjlenyomat.main([self.mappa, "--csak-telepitendo",
                                "--kiir", lista], kiir=kimenet.append)
        self.assertEqual(kod, 0)
        kimenet = []
        kod = ujjlenyomat.main([self.cel, "--csak-telepitendo",
                                "--osszevet", lista], kiir=kimenet.append)
        self.assertEqual(kod, 0, "\n".join(kimenet))
        self.assertTrue(any("EVERYTHING MATCHES" in s for s in kimenet), kimenet)

    def test_becsempeszett_valtozast_tovabbra_is_eszreveszi(self):
        self._atmasol_a_programba()
        lista = os.path.join(self.mappa, "lenyomat.txt")
        ujjlenyomat.main([self.mappa, "--csak-telepitendo", "--kiir", lista],
                         kiir=lambda _s: None)
        with open(os.path.join(self.cel, "motor.py"), "a",
                  encoding="utf-8") as f:
            f.write("# becsempészett sor\n")
        kimenet = []
        kod = ujjlenyomat.main([self.cel, "--csak-telepitendo",
                                "--osszevet", lista], kiir=kimenet.append)
        self.assertEqual(kod, 1)
        self.assertTrue(any("CHANGED" in s for s in kimenet), kimenet)

    def test_uj_fajl_is_kiderul(self):
        self._atmasol_a_programba()
        lista = os.path.join(self.mappa, "lenyomat.txt")
        ujjlenyomat.main([self.mappa, "--csak-telepitendo", "--kiir", lista],
                         kiir=lambda _s: None)
        with open(os.path.join(self.cel, "hatso_ajto.py"), "w",
                  encoding="utf-8") as f:
            f.write("# ide nem való\n")
        kimenet = []
        kod = ujjlenyomat.main([self.cel, "--csak-telepitendo",
                                "--osszevet", lista], kiir=kimenet.append)
        self.assertEqual(kod, 1)
        self.assertTrue(any("NEW" in s for s in kimenet), kimenet)


if __name__ == "__main__":
    unittest.main()
