"""test_atnezes6_leletek.py — fixes for the findings of the SIXTH review.

Every test runs in a temporary folder; no network and no Telegram anywhere,
only mocks. The real filesystem probes (symbolic links) happen in the test's
OWN temporary folder, and `tearDown` cleans up after them.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kozos_teszt  # noqa: E402

import allapot  # noqa: E402
import beallitasok  # noqa: E402
import elonezet  # noqa: E402
import jogok  # noqa: E402
import kimeno  # noqa: E402
import motor  # noqa: E402
import piszkozat  # noqa: E402
import szures  # noqa: E402
import tarolas_or  # noqa: E402


def _mintak():
    utvonal = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "tilto_mintak.json")
    with open(utvonal, "r", encoding="utf-8") as f:
        return json.load(f)


def _symlink_keszitheto(mappa):
    """Can this machine create a symbolic link without elevation?"""
    cel = os.path.join(mappa, "_proba_cel")
    hivatkozas = os.path.join(mappa, "_proba_link")
    try:
        with open(cel, "w", encoding="utf-8") as f:
            f.write("x")
        os.symlink(cel, hivatkozas)
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        for ut in (hivatkozas, cel):
            try:
                os.remove(ut)
            except OSError:
                pass
    return True


# =========================================================================
# K'''''1/a — OPENING DOES NOT FOLLOW THE LINK
# =========================================================================

class NemKovetettHivatkozasTeszt(unittest.TestCase):
    """A dropped file is opened by the name itself, not by the link target."""

    def setUp(self):
        self.alap = tempfile.mkdtemp(prefix="or_hivatkozas_teszt_")
        self.bedobo = os.path.join(self.alap, "ki")
        self.titkos = os.path.join(self.alap, "titok")
        os.makedirs(self.bedobo)
        os.makedirs(self.titkos)
        self.titok_fajl = os.path.join(self.titkos, "kulcs.env")
        with open(self.titok_fajl, "w", encoding="utf-8") as f:
            f.write("PROBA_ERTEK=ezt_soha_nem_szabad_kiolvasni\n")

    def tearDown(self):
        shutil.rmtree(self.alap, ignore_errors=True)

    def _rendes_fajl(self, nev="a.json"):
        utvonal = os.path.join(self.bedobo, nev)
        with open(utvonal, "wb") as f:
            f.write(b'{"a": 1}')
        return utvonal

    def test_a_rendes_fajl_valtozatlanul_beolvashato(self):
        utvonal = self._rendes_fajl()
        bajtok, ok = piszkozat.biztonsagos_beolvasas(utvonal,
                                                     bedobo=self.bedobo)
        self.assertIsNone(ok)
        self.assertEqual(bajtok, b'{"a": 1}')

    def test_a_bedobon_kivulre_mutato_utvonal_elutasitas(self):
        """The FINAL path of the handle must be inside the drop folder."""
        kivul = os.path.join(self.titkos, "b.json")
        with open(kivul, "wb") as f:
            f.write(b'{"a": 1}')
        bajtok, ok = piszkozat.biztonsagos_beolvasas(kivul,
                                                     bedobo=self.bedobo)
        self.assertIsNone(bajtok)
        self.assertEqual(ok, "bedobon_kivuli_fajl")

    def test_a_konyvtar_nem_nyilik_meg(self):
        """Without `FILE_FLAG_BACKUP_SEMANTICS` opening a directory fails."""
        mappa = os.path.join(self.bedobo, "mappa.json")
        os.makedirs(mappa)
        bajtok, ok = piszkozat.biztonsagos_beolvasas(mappa,
                                                     bedobo=self.bedobo)
        self.assertIsNone(bajtok)
        self.assertIn(ok, ("nem_nyithato", "nem_regularis_fajl",
                           "IsADirectoryError", "PermissionError"))

    def test_a_titokra_mutato_hivatkozas_elutasitas(self):
        if not _symlink_keszitheto(self.alap):
            self.skipTest("this machine cannot create a symbolic "
                          "link without elevation")
        hivatkozas = os.path.join(self.bedobo, "link.json")
        os.symlink(self.titok_fajl, hivatkozas)
        bajtok, ok = piszkozat.biztonsagos_beolvasas(hivatkozas,
                                                     bedobo=self.bedobo)
        self.assertIsNone(bajtok)
        self.assertIn(ok, ("hivatkozas", "nem_regularis_fajl"))

    def test_a_tartalek_ut_is_elkapja_a_cseret(self):
        """If the ctypes path is unavailable: the `(st_dev, st_ino)` identity protects."""
        if not _symlink_keszitheto(self.alap):
            self.skipTest("this machine cannot create a symbolic "
                          "link without elevation")
        hivatkozas = os.path.join(self.bedobo, "link.json")
        os.symlink(self.titok_fajl, hivatkozas)
        bajtok, ok = piszkozat._tartalek_beolvasas(
            hivatkozas, beallitasok.PISZKOZAT_MAX_BAJT, self.bedobo)
        self.assertIsNone(bajtok)
        self.assertIn(ok, ("nem_regularis_fajl", "fajl_kicserelve",
                           "bedobon_kivuli_fajl"))

    def test_a_tartalek_ut_a_rendes_fajlt_atengedi(self):
        utvonal = self._rendes_fajl()
        bajtok, ok = piszkozat._tartalek_beolvasas(
            utvonal, beallitasok.PISZKOZAT_MAX_BAJT, self.bedobo)
        self.assertIsNone(ok)
        self.assertEqual(bajtok, b'{"a": 1}')


class VersenySzimulacioTeszt(unittest.TestCase):
    """DETERMINISTIC simulation of the race with an injected hook.

    The hook runs exactly BETWEEN the two steps — the structural check and the
    read — and there it swaps the regular file for a link pointing to the
    secret. This is the attacker's best case: they always win the race.
    Even so, the secret's content appears NOWHERE in the output tree.
    """

    TITOK = "BSKY_APP_JELSZO=ezt-soha-senki-nem-lathatja"

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.titok_fajl = os.path.join(self.k.titok, "bsky_kulcs.env")
        with open(self.titok_fajl, "w", encoding="utf-8") as f:
            f.write(self.TITOK + "\n")

    def tearDown(self):
        piszkozat._VERSENY_HOROG = None
        kozos_teszt.kornyezet_torol(self.k)

    def _fa_tartalma(self):
        """The GUARD's OUTPUT file tree: (path, content) pairs.

        The drop folder (`kozos\\ki`) does NOT belong here: it is the untrusted
        side's own area, that side put the link there, and it can read its own
        files from there anyway. The question is whether the GUARD writes
        anything from the secret into its own files or into the outgoing folder.
        """
        darabok = []
        for alap in (self.k.allapot, self.k.be):
            for gyoker, _mappak, fajlok in os.walk(alap):
                for nev in fajlok:
                    teljes = os.path.join(gyoker, nev)
                    try:
                        with open(teljes, "rb") as f:
                            darabok.append((teljes, f.read()))
                    except OSError:
                        continue
        return darabok

    def _horog_keszit(self, utvonal, hivatkozas_cel):
        def horog(kapott):
            if os.path.abspath(kapott) != os.path.abspath(utvonal):
                return
            piszkozat._VERSENY_HOROG = None   # we swap only once
            try:
                os.remove(utvonal)
                os.symlink(hivatkozas_cel, utvonal)
            except OSError:
                pass
        return horog

    def test_a_ket_lepes_kozotti_csere_utan_a_titok_sehol_nem_jelenik_meg(self):
        utvonal = kozos_teszt.piszkozat_ir(self.k, "a.json",
                                           kozos_teszt.alap_piszkozat())
        if not _symlink_keszitheto(self.k.ki):
            self.skipTest("this machine cannot create a symbolic "
                          "link without elevation")
        piszkozat._VERSENY_HOROG = self._horog_keszit(utvonal,
                                                      self.titok_fajl)
        uzenetek = []
        motor.bevetel(self.k, uzenetek, time.time())

        titok_bajtok = self.TITOK.encode("utf-8")
        for teljes, tartalom in self._fa_tartalma():
            self.assertNotIn(titok_bajtok, tartalom,
                             "the secret's content appeared here: %s" % teljes)
        # Nothing went into the queue, and a message went to the owner.
        self.assertEqual(motor.varo_fajlok(self.k), [])
        self.assertTrue(uzenetek)

    def test_a_csere_nelkuli_kor_valtozatlanul_mukodik(self):
        """The mere presence of the hook must not break the good path."""
        kozos_teszt.piszkozat_ir(self.k, "a.json",
                                 kozos_teszt.alap_piszkozat())
        piszkozat._VERSENY_HOROG = lambda _ut: None
        motor.bevetel(self.k, [], time.time())
        self.assertEqual(len(motor.varo_fajlok(self.k)), 1)


# =========================================================================
# K'''''1/b — RAW BYTES ARE NEVER WRITTEN TO A READABLE PLACE
# =========================================================================

class KanonikusAtvetelTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def test_a_varoba_a_kanonikus_alak_kerul_nem_az_eredeti_bajtok(self):
        """The dropped file's trimmings (order, indentation, BOM) do not carry over."""
        nyers = ('﻿{ "megjegyzes_tulajnak": "proba",\n'
                 '  "langs":["hu"], "szoveg": "Jó reggelt mindenkinek.",\n'
                 '  "valasz_cel_uri": null, "tipus":"poszt", "verzio":1 }')
        utvonal = os.path.join(self.k.ki, "a.json")
        with open(utvonal, "w", encoding="utf-8") as f:
            f.write(nyers)
        motor.bevetel(self.k, [], time.time())
        varo = motor.varo_fajlok(self.k)
        self.assertEqual(len(varo), 1)
        with open(varo[0], "rb") as f:
            tartalom = f.read()
        self.assertNotEqual(tartalom, nyers.encode("utf-8"))
        self.assertEqual(json.loads(tartalom.decode("utf-8")), {
            "verzio": 1,
            "tipus": "poszt",
            "szoveg": "Jó reggelt mindenkinek.",
            "valasz_cel_uri": None,
            "langs": ["hu"],
            "megjegyzes_tulajnak": "proba",
        })

    def test_a_kanonikus_alak_ujra_beolvasva_ugyanaz(self):
        """The canonical form passes its own schema, unchanged."""
        tetel, okok = piszkozat.ertelmez(json.dumps(
            kozos_teszt.alap_piszkozat(), ensure_ascii=False))
        self.assertEqual(okok, [])
        bajtok = piszkozat.kanonikus_bajtok(tetel)
        ujra, okok = piszkozat.bajtokbol(bajtok)
        self.assertEqual(okok, [])
        self.assertEqual(ujra, tetel)
        self.assertEqual(piszkozat.kanonikus_bajtok(ujra), bajtok)

    def test_a_seman_elbukott_tartalom_egy_bajtja_sem_kerul_lemezre(self):
        jel = "EZ_A_TARTALOM_SEHOVA_NEM_KERULHET"
        utvonal = os.path.join(self.k.ki, "rossz.json")
        with open(utvonal, "w", encoding="utf-8") as f:
            f.write('{"tipus": "poszt", "ismeretlen_%s": 1}' % jel)
        elutasitottak = motor.bevetel(self.k, [], time.time())
        self.assertEqual(len(elutasitottak), 1)
        self.assertEqual(motor.varo_fajlok(self.k), [])
        self.assertFalse(os.listdir(self.k.ki))
        for gyoker, _mappak, fajlok in os.walk(self.k.allapot):
            for nev in fajlok:
                with open(os.path.join(gyoker, nev), "rb") as f:
                    self.assertNotIn(jel.encode("utf-8"), f.read())
        egyben = json.dumps(elutasitottak, ensure_ascii=False)
        self.assertNotIn(jel, egyben)

    def test_az_ismeretlen_mezo_neve_nem_kerul_a_kifele_meno_okba(self):
        _tetel, okok = piszkozat.ertelmez(
            '{"verzio":1,"tipus":"poszt","szoveg":"x","valasz_cel_uri":null,'
            '"langs":["hu"],"megjegyzes_tulajnak":"","TITKOS_MEZONEV":1}')
        self.assertIn("ismeretlen_mezo", okok)
        self.assertNotIn("TITKOS_MEZONEV", " ".join(okok))

    def test_a_tartalom_ujjlenyomat_hmac_es_nem_puszta_sha256(self):
        """The fingerprint cannot be reversed without the key."""
        import hashlib
        bajtok = b'{"a": 1}'
        ujjlenyomat = allapot.tartalom_ujjlenyomat(self.k, bajtok)
        self.assertNotEqual(ujjlenyomat, hashlib.sha256(bajtok).hexdigest())
        # stable within the same installation
        self.assertEqual(ujjlenyomat,
                         allapot.tartalom_ujjlenyomat(self.k, bajtok))
        # the key and the table live in the CLOSED folder
        self.assertTrue(os.path.isfile(self.k.bedobott_kulcs))
        self.assertTrue(os.path.abspath(self.k.bedobott_kulcs)
                        .startswith(os.path.abspath(self.k.zart)))
        self.assertTrue(os.path.abspath(self.k.bedobott_hashek)
                        .startswith(os.path.abspath(self.k.zart)))

    def test_masik_telepites_masik_ujjlenyomatot_ad(self):
        masik = kozos_teszt.kornyezet_keszit()
        try:
            self.assertNotEqual(
                allapot.tartalom_ujjlenyomat(self.k, b"x"),
                allapot.tartalom_ujjlenyomat(masik, b"x"))
        finally:
            kozos_teszt.kornyezet_torol(masik)


# =========================================================================
# K'''''1/c — NO FOREIGN PERMISSION AT ALL ON THE CLOSED FOLDERS
# =========================================================================

class ZartMappakJogaTeszt(unittest.TestCase):
    """The self-check must REQUIRE that the untrusted side cannot read them."""

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        os.makedirs(self.k.zart, exist_ok=True)

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    @staticmethod
    def _tiszta(utvonal):
        return ("%s NT AUTHORITY\\SYSTEM:(OI)(CI)(F)\n"
                "%s BUILTIN\\Administrators:(OI)(CI)(F)\n"
                "%s GEP\\bsky-guard:(OI)(CI)(M)\n" % (utvonal, utvonal, utvonal))

    def _lekero(self, kivetel_mappa=None, extra_sor=""):
        def lekero(utvonal):
            kimenet = self._tiszta(utvonal)
            if kivetel_mappa and os.path.abspath(utvonal) == \
                    os.path.abspath(kivetel_mappa):
                kimenet += extra_sor % utvonal
            if os.path.abspath(utvonal) == os.path.abspath(self.k.ki):
                kimenet += ("%s GEP\\agent-user:(DENY)(DE)\n"
                            "%s GEP\\agent-user:(OI)(CI)(M)\n" % (utvonal, utvonal))
            return kimenet
        return lekero

    def _hibak(self, lekero):
        return jogok.jog_ellenorzes(self.k, lekero=lekero,
                                    tulajdonos_lekero=lambda _u: "GEP\\bsky-guard")

    def test_tiszta_jogokkal_nincs_hiba(self):
        self.assertEqual(self._hibak(self._lekero()), [])

    def test_a_varo_mappan_az_OLVASASI_jog_is_hiba(self):
        """This is the point: the untrusted side cannot even read `varo`."""
        lekero = self._lekero(self.k.varo, "%s GEP\\agent-user:(OI)(CI)(RX)\n")
        hibak = self._hibak(lekero)
        self.assertTrue(any("varo" in h and "unauthorized any access" in h for h in hibak),
                        hibak)

    def test_a_feldolgozott_mappan_az_OLVASASI_jog_is_hiba(self):
        lekero = self._lekero(self.k.feldolgozott,
                              "%s GEP\\agent-user:(OI)(CI)(RX)\n")
        hibak = self._hibak(lekero)
        self.assertTrue(any("feldolgozott" in h for h in hibak), hibak)

    def test_a_zart_mappan_az_OLVASASI_jog_is_hiba(self):
        lekero = self._lekero(self.k.zart, "%s GEP\\agent-user:(OI)(CI)(RX)\n")
        hibak = self._hibak(lekero)
        self.assertTrue(any("zart" in h for h in hibak), hibak)

    def test_az_allapot_mappan_az_olvasas_tovabbra_is_megengedett(self):
        """`allapot` stays readable — the other side gets its information there."""
        lekero = self._lekero(self.k.allapot, "%s GEP\\agent-user:(OI)(CI)(RX)\n")
        self.assertEqual(self._hibak(lekero), [])

    def test_a_harom_zart_mappa_szerepel_a_szabalyokban(self):
        zartak = {mezo for mezo, _nev, mit in jogok.SZABALYOK
                  if mit == "semmi"}
        self.assertEqual(zartak, {"varo", "feldolgozott", "zart"})

    def test_a_telepitesi_leiras_kulon_icacls_sort_ad_a_zart_mappakra(self):
        utvonal = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "INSTALL.md")
        with open(utvonal, "r", encoding="utf-8") as f:
            szoveg = f.read()
        for mappa in ("allapot\\varo", "allapot\\feldolgozott",
                      "allapot\\zart"):
            self.assertIn(mappa, szoveg,
                          "missing from the installation guide: %s" % mappa)


# =========================================================================
# K'''''2 — THE FLAG EXCEPTION IS EXACTLY THREE SEQUENCES
# =========================================================================

def _cimkesor(szoveg):
    return "".join(chr(0xE0000 + ord(betu)) for betu in szoveg)


def _zaszlo(cimkesor):
    return "\U0001F3F4" + _cimkesor(cimkesor) + "\U000E007F"


class ZaszloKivetelTeszt(unittest.TestCase):

    def setUp(self):
        self.mintak = _mintak()

    def _atmegy(self, szoveg):
        return kimeno.szoveg_szabalyok(szoveg, self.mintak) == []

    def test_a_harom_engedett_zaszlo_atmegy(self):
        self.assertEqual(len(szures.ENGEDETT_ZASZLOK), 3)
        for cimkesor in ("gbeng", "gbsct", "gbwls"):
            self.assertTrue(self._atmegy("Hajrá " + _zaszlo(cimkesor)),
                            cimkesor)

    def test_minden_mas_cimkesor_elutasitas(self):
        for cimkesor in ("hello", "gbxyz", "gbENG", "gben", "gbengg",
                         "a" * 16, "kapcsolat@example.com", "",
                         "us-tx", "0123456789abcdef"):
            szoveg = "Szia " + _zaszlo(cimkesor)
            self.assertFalse(self._atmegy(szoveg),
                             "a non-allowed tag sequence passed: %r" % cimkesor)

    def test_a_cimke_karakter_zaszlo_nelkul_elutasitas(self):
        self.assertFalse(self._atmegy("Szia" + _cimkesor("titok")))

    def test_a_zaro_nelkuli_cimkesor_elutasitas(self):
        self.assertFalse(self._atmegy("Szia \U0001F3F4" + _cimkesor("gbeng")))

    def test_a_rejtett_uzenet_nem_marad_meg_a_szanitalas_utan(self):
        szoveg = "Jó reggelt! " + _zaszlo("titok")
        tiszta = szures.szanitalas(szoveg, len(szoveg) + 1)
        for betu in "titok":
            self.assertNotIn(chr(0xE0000 + ord(betu)), tiszta)

    def test_a_kapacitas_tamadas_elutasitas(self):
        """The 256-character hidden payload described in the finding."""
        szoveg = "Jó reggelt mindenkinek! " + "".join(
            _zaszlo("abcdefghijklmnop") for _ in range(16))
        self.assertFalse(self._atmegy(szoveg))


# =========================================================================
# M'''''1 — THE INVISIBLE LIST COMES FROM THE UNICODE DATA
# =========================================================================

# The `Default_Ignorable_Code_Point` set per Unicode `DerivedCoreProperties.txt`
# — the TEST's OWN copy, INDEPENDENT of the module. If the two diverge, the
# test fails.
_DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C),
    (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
    (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F),
    (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x16FE4, 0x16FE4),
    (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)


def _default_ignorable_halmaz():
    halmaz = set()
    for also, felso in _DEFAULT_IGNORABLE:
        halmaz.update(range(also, felso + 1))
    return halmaz


class LathatatlanListaTeszt(unittest.TestCase):

    def setUp(self):
        self.mintak = _mintak()
        self.ignorable = _default_ignorable_halmaz()

    def test_a_teljes_default_ignorable_halmaz_benne_van(self):
        hianyzo = sorted(k for k in self.ignorable
                         if not szures.lathatatlan_kodpont_e(k))
        self.assertEqual(hianyzo, [],
                         "missing invisible code points: %s"
                         % [hex(k) for k in hianyzo[:10]])

    def test_a_khitan_kitolto_most_mar_elutasitas(self):
        """The finding's code point: category `Mn`, ends up in a zero grapheme."""
        self.assertEqual(unicodedata.category("\U00016FE4"), "Mn")
        szoveg = "Jó\U00016FE4 reggelt\U00016FE4 mindenkinek!"
        self.assertNotEqual(kimeno.szoveg_szabalyok(szoveg, self.mintak), [])

    def test_a_teljes_kodpont_ter_bejarasa(self):
        """Every `Mn`/`Me`/`Cf` AND `Default_Ignorable` code point is rejected.

        Apart from the exception rules: the emoji context of `U+200D` and
        `U+FE0F`, and the tags of the three allowed flags.
        """
        kivetelek = {0x200D, 0xFE0F}
        for zaszlo in szures.ENGEDETT_ZASZLOK:
            kivetelek.update(ord(ch) for ch in zaszlo)
        megvizsgalt = 0
        for kod in range(0x110000):
            if 0xD800 <= kod <= 0xDFFF:
                continue          # half of a surrogate pair: not even encodable in UTF-8
            if kod in kivetelek:
                continue
            karakter = chr(kod)
            if unicodedata.category(karakter) not in ("Mn", "Me", "Cf"):
                continue
            if kod not in self.ignorable:
                continue
            megvizsgalt += 1
            self.assertTrue(
                szures.lathatatlan_kodpont_e(kod),
                "passes the invisible filter: U+%04X" % kod)
            szoveg = "Jó reggelt" + karakter + " mindenkinek!"
            self.assertNotEqual(
                kimeno.szoveg_szabalyok(szoveg, self.mintak), [],
                "passed the outgoing rules: U+%04X" % kod)
        self.assertGreater(megvizsgalt, 100)

    def test_az_artalmatlan_szoveg_es_emoji_valtozatlanul_atmegy(self):
        for szoveg in ("Jó reggelt mindenkinek!",
                       "Jó reggelt! \U0001F351",
                       "1️⃣ helyezés",
                       "\U0001F468‍\U0001F469‍\U0001F467",
                       "Hajrá " + _zaszlo("gbwls")):
            self.assertEqual(kimeno.szoveg_szabalyok(szoveg, self.mintak), [],
                             szoveg)

    def test_az_ures_dobozt_rajzolo_jelek_elutasitasra_kerulnek(self):
        for kod in (0xFFFC, 0xFFFD, 0x2800):
            self.assertTrue(szures.lathatatlan_kodpont_e(kod), hex(kod))


# =========================================================================
# M'''''2 — QUANTITY LIMITS FOR ZWJ CHAINS AND ZALGO
# =========================================================================

class RejtettCsatornaKorlatTeszt(unittest.TestCase):

    def setUp(self):
        self.mintak = _mintak()

    def _okok(self, szoveg):
        return kimeno.szoveg_szabalyok(szoveg, self.mintak)

    def test_a_legitim_csalad_emoji_atmegy(self):
        csalad = "‍".join(["\U0001F468", "\U0001F469", "\U0001F467",
                                "\U0001F466"])
        self.assertEqual(self._okok(csalad), [])

    def test_a_hetnel_hosszabb_zwj_lanc_elutasitas(self):
        lanc = "‍".join(["\U0001F468"] * 8)
        self.assertIn("tul_hosszu_zwj_lanc", self._okok(lanc))

    def test_a_pontosan_het_elemu_lanc_meg_atmegy(self):
        lanc = "‍".join(["\U0001F468"] * 7)
        self.assertNotIn("tul_hosszu_zwj_lanc", self._okok(lanc))

    def test_posztonkent_legfeljebb_tiz_zwj(self):
        sok = " ".join(["\U0001F468‍\U0001F469"] * 11)
        self.assertIn("tul_sok_zwj", self._okok(sok))
        keves = " ".join(["\U0001F468‍\U0001F469"] * 10)
        self.assertNotIn("tul_sok_zwj", self._okok(keves))

    def test_a_bitcsatornakent_hasznalt_zwj_sor_elutasitas(self):
        """The finding: 33 emoji, 32 bits encoded with ZWJ."""
        bitek = "10110010101100101011001010110010"
        darabok = []
        for bit in bitek:
            darabok.append("\U0001F642")
            if bit == "1":
                darabok.append("‍")
        darabok.append("\U0001F642")
        self.assertNotEqual(self._okok("".join(darabok)), [])

    def test_ismetelt_valtozat_valaszto_elutasitas(self):
        self.assertIn("ismetelt_valtozat_valaszto",
                      self._okok("❤️️"))

    def test_egy_valtozat_valaszto_atmegy(self):
        self.assertEqual(self._okok("❤️"), [])

    def test_alapkarakterenkent_legfeljebb_ket_kombinalo_jel(self):
        self.assertEqual(
            szures.rejtett_csatorna_ellenorzes("à́"), [])
        self.assertIn("tul_sok_kombinalo_jel",
                      szures.rejtett_csatorna_ellenorzes("à́̂"))

    def test_posztonkent_legfeljebb_husz_kombinalo_jel(self):
        zalgo = "".join(betu + "̀́" for betu in "abcdefghijk")
        self.assertIn("tul_sok_kombinalo_jel",
                      szures.rejtett_csatorna_ellenorzes(zalgo))

    def test_a_magyar_ekezetes_szoveg_nem_bukik_el(self):
        # Hungarian language data: an accented pangram; replace with a sample
        # in your own language to check its diacritics are not flagged.
        self.assertEqual(
            self._okok("Árvíztűrő tükörfúrógép — őszi napsütés."), [])


# =========================================================================
# Kö'''''1 — THE PREVIEW'S JUSTIFICATIONS ARE EXCLUSIVE AND TRUE
# =========================================================================

class ElonezetIndoklasTablaTeszt(unittest.TestCase):
    """Table test for all FOUR combinations, with the allowlisted variants.

    In every case we check in the FULL preview text that no contradicting
    sentences appear: the "WE WERE NOT ADDRESSED" and the
    "WE WERE ADDRESSED, BUT…" claims must never stand side by side.
    """

    ENGEDELYLISTAS_DID = "did:plc:engedelyezett777"

    # (name, allowlisted reason, stranger reason, expected single sentence)
    TABLA = (
        ("1. addressed, no stranger", False, False, None),
        ("2. addressed + stranger in the thread", False, True,
         "IDEGEN_SZALBA"),
        ("3. not addressed, allowlisted", True, False,
         "NEM_SZOLITOTTAK_MEG"),
        ("4. not addressed AND stranger in the thread", True, True,
         "MINDKET_OK"),
    )

    MONDATOK = {
        "NEM_SZOLITOTTAK_MEG": elonezet.NEM_SZOLITOTTAK_MEG,
        "IDEGEN_SZALBA": elonezet.IDEGEN_SZALBA,
        "MINDKET_OK": elonezet.MINDKET_OK,
    }

    def _uzenet(self, engedelylistas, idegen, engedelylistas_szereplo=False):
        okok = []
        if engedelylistas:
            okok.append(szures.OK_ENGEDELYLISTAS)
        if idegen:
            okok.append(szures.OK_IDEGEN_A_SZALBAN)
        cel = {
            "kezdemenyezett": bool(okok),
            "kezdemenyezett_oka": okok,
            "uri": kozos_teszt.CEL_URI,
            "szerzo_handle": "idegen.bsky.social",
            "szerzo_did": kozos_teszt.IDEGEN_DID,
            "szoveg_roviden": "Szia, mit gondolsz?",
            "lanc_hossz": 2,
            "idegen_szereplok": ([{"handle": "harmadik.bsky.social",
                                   "did": "did:plc:harmadikfel789"}]
                                 if idegen else []),
            "engedelylistas_szereplok": ([{"handle": "baratunk.bsky.social",
                                           "did": self.ENGEDELYLISTAS_DID}]
                                         if engedelylistas_szereplo else []),
        }
        kanonikus = szures.kimeno_kanonikus("valasz", "Köszönöm!", cel, ["hu"],
                                            bool(okok))
        return elonezet.szoveg(kanonikus, "jelolo", cel=cel)

    def _ellentmondas_nelkul(self, nev, uzenet):
        jelen = [kulcs for kulcs, mondat in self.MONDATOK.items()
                 if mondat in uzenet]
        self.assertLessEqual(len(jelen), 1,
                             "%s: several mutually contradicting "
                             "justifications at once: %s" % (nev, jelen))
        # the two separate sentences must NEVER stand side by side
        self.assertFalse(
            elonezet.NEM_SZOLITOTTAK_MEG in uzenet
            and elonezet.IDEGEN_SZALBA in uzenet, nev)
        return jelen[0] if jelen else None

    def test_mind_a_negy_kombinacio(self):
        for nev, engedelylistas, idegen, vart in self.TABLA:
            with self.subTest(nev=nev):
                uzenet = self._uzenet(engedelylistas, idegen)
                self.assertEqual(self._ellentmondas_nelkul(nev, uzenet), vart)

    def test_mind_a_negy_kombinacio_engedelylistas_szereplovel(self):
        for nev, engedelylistas, idegen, vart in self.TABLA:
            with self.subTest(nev=nev + " + allowlisted party"):
                uzenet = self._uzenet(engedelylistas, idegen,
                                      engedelylistas_szereplo=True)
                self.assertEqual(self._ellentmondas_nelkul(nev, uzenet), vart)
                self.assertIn("an allowlisted party also takes part", uzenet)

    def test_az_elso_kombinacioban_nincs_figyelmeztetes(self):
        uzenet = self._uzenet(False, False)
        self.assertNotIn("UNSOLICITED", uzenet)
        self.assertIn("there is NO foreign third party in it", uzenet)

    def test_a_valodi_kodut_negyedik_kombinaciot_is_igy_kezeli(self):
        """The `cel` structure is produced by the ACTUAL `cel_ellenorzes`."""
        cel_uri = kozos_teszt.CEL_URI
        gyoker_uri = kozos_teszt.GYOKER_URI
        # not addressed (no mention facet), the author is allowlisted,
        # but the thread root belongs to a stranger third party
        celposzt = kozos_teszt.cel_poszt(
            mention_did=None, szerzo_did=self.ENGEDELYLISTAS_DID,
            gyoker_uri=gyoker_uri, gyoker_cid=kozos_teszt.GYOKER_CID)
        posztok = {cel_uri: celposzt,
                   gyoker_uri: kozos_teszt.gyoker_poszt()}
        cel, okok = kimeno.cel_ellenorzes(
            celposzt, cel_uri, kozos_teszt.SAJAT_DID,
            [{"did": self.ENGEDELYLISTAS_DID}], posztok.get)
        self.assertEqual(okok, [])
        self.assertTrue(cel["kezdemenyezett"])
        self.assertIn(szures.OK_ENGEDELYLISTAS, cel["kezdemenyezett_oka"])
        self.assertIn(szures.OK_IDEGEN_A_SZALBAN, cel["kezdemenyezett_oka"])
        kanonikus = szures.kimeno_kanonikus("valasz", "Köszönöm!", cel, ["hu"],
                                            True)
        uzenet = elonezet.szoveg(kanonikus, "jelolo", cel=cel)
        self.assertEqual(
            self._ellentmondas_nelkul("real code path", uzenet), "MINDKET_OK")


# =========================================================================
# Kö'''''4 and Kö'''''5 — THE TWO MISSING MESSAGES OF THE DROPPED-HASH BRANCH
# =========================================================================

class BedobottHashAgTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.eredeti_torles = motor._bedobott_eltavolitas

    def tearDown(self):
        motor._bedobott_eltavolitas = self.eredeti_torles
        kozos_teszt.kornyezet_torol(self.k)

    def _bent_ragad(self, nev="a.json"):
        """We take over the content, but cannot delete it from the drop folder."""
        kozos_teszt.piszkozat_ir(self.k, nev, kozos_teszt.alap_piszkozat())
        motor._bedobott_eltavolitas = lambda _ut: False
        motor.bevetel(self.k, [], time.time())

    def test_a_bent_ragadt_fajl_harom_kor_utan_veglegesen_kihagyott(self):
        """Kö'''''4: until now it used up the per-round quota forever."""
        self._bent_ragad()
        for kor in range(1, 4):
            uzenetek = []
            motor.bevetel(self.k, uzenetek, time.time() + kor)
        tabla = tarolas_or.json_olvas(self.k.kihagyando, {})
        self.assertIn("a.json", tabla)
        self.assertGreaterEqual(tabla["a.json"]["proba"],
                                beallitasok.BEDOBO_PROBA_MAX)
        # and in the next round it does not even make it into the list
        maradek = piszkozat.piszkozat_fajlok(self.k.ki, kihagyando={"a.json"})
        self.assertEqual(maradek, [])

    def test_a_bent_ragadt_fajl_uzenetet_kap_minden_korben(self):
        self._bent_ragad()
        uzenetek = []
        motor.bevetel(self.k, uzenetek, time.time() + 1)
        self.assertTrue(any("could not take over" in u or "I SKIP IT" in u
                            for u in uzenetek), uzenetek)

    def test_az_azonos_tartalmu_masodik_piszkozat_nem_nemul_el(self):
        """Kö'''''5: until now it vanished without a trace."""
        self._bent_ragad()
        motor._bedobott_eltavolitas = self.eredeti_torles
        uzenetek = []
        motor.bevetel(self.k, uzenetek, time.time() + 1)
        self.assertTrue(any("already taken over" in u for u in uzenetek), uzenetek)
        self.assertTrue(any("can be dropped in again" in u for u in uzenetek), uzenetek)
        naplo = tarolas_or.jsonl_olvas(self.k.naplo)
        self.assertTrue(any(s.get("esemeny") == "bedobott_azonos_tartalom"
                            for s in naplo))

    def test_a_kihagyas_utan_ugyanaz_a_szoveg_ujra_bedobhato(self):
        self._bent_ragad()
        motor._bedobott_eltavolitas = self.eredeti_torles
        motor.bevetel(self.k, [], time.time() + 1)     # the hash is forgotten
        self.assertEqual(tarolas_or.json_olvas(self.k.bedobott_hashek, {}), {})
        kozos_teszt.piszkozat_ir(self.k, "b.json", kozos_teszt.alap_piszkozat())
        motor.bevetel(self.k, [], time.time() + 2)
        self.assertEqual(len(motor.varo_fajlok(self.k)), 2)


# =========================================================================
# A'''''1 — PER-ROUND CHAIN-LOOKUP QUOTA
# =========================================================================

class LancKeretTeszt(unittest.TestCase):

    def test_a_keret_fogy_es_elfogy(self):
        keret = motor.LancKeret(3)
        for _ in range(3):
            keret.fogyaszt()
        with self.assertRaises(motor.UzemHiba):
            keret.fogyaszt()

    def test_az_alapertelmezett_keret_a_beallitasokbol_jon(self):
        self.assertEqual(motor.LancKeret().maradek,
                         beallitasok.LANC_KERET_KORONKENT)

    def test_a_keret_tullepese_HALASZTAS_nem_elutasitas(self):
        """The draft stays in the protected waiting folder and waits for the next round."""
        k = kozos_teszt.kornyezet_keszit()
        try:
            kozos_teszt.piszkozat_ir(
                k, "a.json",
                kozos_teszt.alap_piszkozat(tipus="valasz",
                                           cel_uri=kozos_teszt.CEL_URI))
            halo = kozos_teszt.MockHalo(
                posztok={kozos_teszt.CEL_URI: kozos_teszt.cel_poszt()})
            uzenetek = []
            motor.bevetel(k, uzenetek, time.time())
            utvonal = motor.varo_fajlok(k)[0]
            with self.assertRaises(motor.UzemHiba):
                motor.piszkozat_feldolgozas(
                    k, halo, kozos_teszt.MockBot(), _mintak(),
                    kozos_teszt.SAJAT_DID, [], time.time(), [], utvonal,
                    motor.LancKeret(0))
            self.assertEqual(len(motor.varo_fajlok(k)), 1)
        finally:
            kozos_teszt.kornyezet_torol(k)


if __name__ == "__main__":
    unittest.main()
