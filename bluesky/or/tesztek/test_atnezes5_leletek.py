"""Regression tests for the findings of the FIFTH review (ATNEZES5).

  * M''''1 — invisible code points: the logic is INVERTED. Every code point
    that is not visible by default is rejected, with two narrow exceptions
    (keycap emoji, well-formed flag tag sequence) plus the existing handling
    of ZWJ / U+FE0F in an emoji context;
  * M''''2 — the draft enters the protected waiting folder as a NEW file, not
    with the drop folder's access list and owner;
  * Kö''''1 — the preview's INITIATED justification is TRUE;
  * Kö''''2 — the block filter ALSO runs on a copy stripped of invisibles;
  * Kö''''3 — keycap emoji are allowed, but ONLY as the exact triple;
  * Kö''''4 — a deeper thread fits, and rejecting a too-deep one gives an
    UNDERSTANDABLE reason;
  * Kö''''5 — an allowlisted third party is NAMED in the preview;
  * Kö''''6 — the owner of `kozos\\ki` must not be the untrusted user either;
  * A''''1 — entries on the skip list EXPIRE.

Convention: if a test FAILS, the finding has come back.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import allapot  # noqa: E402
import beallitasok  # noqa: E402
import elonezet  # noqa: E402
import jogok  # noqa: E402
import kimeno  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402
import piszkozat  # noqa: E402
import szures  # noqa: E402
import tarolas_or  # noqa: E402


# =========================================================================
# M''''1 — INVISIBLE CODE POINTS
# =========================================================================

# The sequences PROVEN by the review, plus one representative from each range
# of the inverted logic. Shape: (name, the character to insert).
LATHATATLANOK = [
    ("lagy_elvalaszto_00AD", "­"),
    ("grafema_osszekoto_034F", "͏"),
    ("arab_betujelolo_061C", "؜"),
    ("hangul_kitolto_115F", "ᅟ"),
    ("hangul_kitolto_1160", "ᅠ"),
    ("khmer_17B4", "឴"),
    ("khmer_17B5", "឵"),
    ("mongol_180B", "᠋"),
    ("mongol_180E", "᠎"),
    ("nulla_szelessegu_200B", "​"),
    ("bal_jobb_jelolo_200E", "‎"),
    ("bidi_felulirás_202E", "‮"),
    ("szo_osszekoto_2060", "⁠"),
    ("bidi_izolacio_2066", "⁦"),
    ("lathatatlan_operator_2062", "⁢"),
    ("braille_ures_2800", "⠀"),
    ("hangul_kitolto_3164", "ㅤ"),
    ("valtozatvalaszto_FE00", "︀"),
    ("valtozatvalaszto_FE0E", "︎"),
    ("bom_FEFF", "﻿"),
    ("felszelessegu_hangul_FFA0", "ﾠ"),
    ("kiosztatlan_FFF0", "￰"),
    ("gyorsiras_1BCA0", "\U0001bca0"),
    ("zenei_1D173", "\U0001d173"),
    ("cimke_karakter_E0041", "\U000e0041"),
    ("ideografiai_valtozat_E0100", "\U000e0100"),
    ("ideografiai_valtozat_E01EF", "\U000e01ef"),
    # non-ASCII space characters (Zs, except the plain space)
    ("nem_toro_szokoz_00A0", " "),
    ("ogham_szokoz_1680", " "),
    ("en_szokoz_2002", " "),
    ("keskeny_nem_toro_202F", " "),
    ("ideografiai_szokoz_3000", "　"),
]

ARTALMATLAN = "Kedves mindenki"


class LathatatlanKodpontTeszt(unittest.TestCase):
    """M''''1: what the owner cannot see must not go out in the post."""

    def setUp(self):
        self.mintak, _ = szures.tilto_mintak_biztos("nincs_ilyen_fajl.json")

    def test_minden_lathatatlan_megvaltoztatja_a_szanitalt_alakot(self):
        for nev, karakter in LATHATATLANOK:
            with self.subTest(nev):
                szoveg = ARTALMATLAN + karakter
                tiszta = szures.szanitalas(szoveg, len(szoveg) + 1)
                self.assertNotEqual(
                    tiszta, szoveg,
                    "character %s passed through sanitization UNTOUCHED" % nev)

    def test_minden_lathatatlan_elutasitja_a_piszkozatot(self):
        """The byte-exactness gate: the text would need rewriting → rejection."""
        for nev, karakter in LATHATATLANOK:
            with self.subTest(nev):
                tetel, okok = piszkozat.ertelmez(
                    '{"verzio":1,"tipus":"poszt","szoveg":%s,'
                    '"valasz_cel_uri":null,"langs":["hu"],'
                    '"megjegyzes_tulajnak":""}'
                    % _json_szoveg(ARTALMATLAN + karakter + " vege"))
                self.assertIsNone(tetel)
                self.assertIn("szoveg_atirasra_szorulna", okok, nev)

    def test_a_kimeno_szabaly_is_elutasitja(self):
        for nev, karakter in LATHATATLANOK:
            with self.subTest(nev):
                okok = kimeno.szoveg_szabalyok(ARTALMATLAN + karakter,
                                               self.mintak)
                self.assertTrue(
                    any(o.startswith("megjelenites_atirna") for o in okok),
                    "%s: %s" % (nev, okok))

    def test_a_bizonyitott_rejtett_adatsorok(self):
        """The four cases the review proved by running them, verbatim."""
        esetek = [
            ARTALMATLAN + "︀︁︂︃︄",
            ARTALMATLAN + "\U000e0100\U000e0101\U000e0102",
            "Kedvesㅤㅤmindenki",
            "Kedves⠀⠀mindenki",
        ]
        for szoveg in esetek:
            with self.subTest(ascii(szoveg)):
                self.assertNotEqual(szures.szanitalas(szoveg, len(szoveg) + 1),
                                    szoveg)

    def test_artalmatlan_szoveg_valtozatlan(self):
        for szoveg in (ARTALMATLAN, "Jó reggelt! \U0001f351",
                       "Két  szóköz\nés egy sortörés"):
            with self.subTest(szoveg):
                self.assertEqual(szures.szanitalas(szoveg, len(szoveg) + 1),
                                 szoveg)


class EmojiKivetelTeszt(unittest.TestCase):
    """The two narrow exceptions — and nothing else."""

    ATMEGY = [
        ("csalad_zwj", "\U0001f468‍\U0001f469‍\U0001f467"),
        ("borszin_jelolo", "\U0001f44d\U0001f3fb"),
        ("sziv_valtozatvalasztoval", "❤️"),
        ("barack", "\U0001f351"),
        # Kö''''3 — keycap emoji, as the EXACT triple
        ("billentyu_1", "1️⃣"),
        ("billentyu_0", "0️⃣"),
        ("billentyu_hashmark", "#️⃣"),
        ("billentyu_csillag", "*️⃣"),
        # flag tag sequence: 🏴 + tags + U+E007F
        ("zaszlo_alszekvencia",
         "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067"
         "\U000e007f"),
    ]

    ELUTASITVA = [
        ("zwj_ket_betu_kozott", "Ked‍ves"),
        ("valtozatvalaszto_betu_utan", "a️"),
        # EVERY broken form of the keycap triple
        ("billentyu_zaro_nelkul", "1️"),
        ("billentyu_betu_alappal", "a️⃣"),
        # Note: the "emoji + U+FE0F + U+20E3" form passes, but NOT via the
        # keycap exception: there the variation selector is kept by the old
        # emoji-context rule. It is a visible mark (enclosed emoji), not a
        # hidden channel, so we do not block it.
        # tag characters OUTSIDE a well-formed flag
        ("cimke_zaro_nelkul", "\U0001f3f4\U000e0067\U000e0062"),
        ("cimke_zaszlo_alap_nelkul", "x\U000e0067\U000e007f"),
        ("cimke_onmagaban", "Kedves\U000e0041mindenki"),
        ("cimke_masik_emoji_utan",
         "\U0001f351\U000e0067\U000e0062\U000e007f"),
    ]

    def test_engedett_sorozatok_valtozatlanok(self):
        for nev, szoveg in self.ATMEGY:
            with self.subTest(nev):
                self.assertEqual(szures.szanitalas(szoveg, len(szoveg) + 1),
                                 szoveg)

    def test_elutasitott_sorozatok_valtoznak(self):
        for nev, szoveg in self.ELUTASITVA:
            with self.subTest(nev):
                self.assertNotEqual(szures.szanitalas(szoveg, len(szoveg) + 1),
                                    szoveg)

    def test_a_billentyu_emoji_piszkozatkent_is_atmegy(self):
        mintak, _ = szures.tilto_mintak_biztos("nincs_ilyen_fajl.json")
        self.assertEqual(
            kimeno.szoveg_szabalyok("Első helyezés: 1️⃣", mintak),
            [])


class TiltoszuroLathatatlanTeszt(unittest.TestCase):
    """Kö''''2: a single invisible character must not disable the block filter."""

    def setUp(self):
        self.mintak, _ = szures.tilto_mintak_biztos("nincs_ilyen_fajl.json")
        self.mintak["telepulesek"].append("Példafalva")

    def _talalat(self, szoveg):
        return szures.tilto_ellenorzes(szoveg, self.mintak)

    def test_telepulesnev_lathatatlannal_kettevagva(self):
        self.assertTrue(self._talalat("Példafalva"))
        for _nev, karakter in LATHATATLANOK:
            with self.subTest(ascii(karakter)):
                self.assertTrue(self._talalat("Példa" + karakter + "falva"),
                                "the block filter became bypassable")

    def test_email_cim_lathatatlannal_kettevagva(self):
        self.assertTrue(self._talalat("valaki@example.com"))
        for _nev, karakter in LATHATATLANOK:
            with self.subTest(ascii(karakter)):
                self.assertTrue(self._talalat("valaki@pel" + karakter + "da.hu"))

    def test_artalmatlan_szoveg_tovabbra_is_atmegy(self):
        self.assertEqual(self._talalat("Jó reggelt mindenkinek."), [])


# =========================================================================
# Kö''''1 and Kö''''5 — THE PREVIEW TELLS THE TRUTH
# =========================================================================

class ElonezetIndoklasTeszt(unittest.TestCase):

    def _kanonikus(self):
        return szures.kimeno_kanonikus("valasz", "Köszönöm!", None, ["hu"],
                                       True)

    def test_engedelylistas_kezdemenyezes_a_regi_mondatot_kapja(self):
        cel = {"kezdemenyezett": True,
               "kezdemenyezett_oka": [szures.OK_ENGEDELYLISTAS]}
        uzenet = elonezet.szoveg(self._kanonikus(), "jelolo", cel=cel)
        self.assertIn(elonezet.NEM_SZOLITOTTAK_MEG, uzenet)
        self.assertNotIn(elonezet.IDEGEN_SZALBA, uzenet)

    def test_megszolitottak_de_idegen_a_szalban_mas_mondatot_kap(self):
        """The core of the finding: in this case we WERE ADDRESSED — the
        opposite must not be claimed."""
        cel = {"kezdemenyezett": True,
               "kezdemenyezett_oka": [szures.OK_IDEGEN_A_SZALBAN]}
        uzenet = elonezet.szoveg(self._kanonikus(), "jelolo", cel=cel)
        self.assertNotIn(elonezet.NEM_SZOLITOTTAK_MEG, uzenet)
        self.assertIn(elonezet.IDEGEN_SZALBA, uzenet)

    def test_mindket_ok_egyetlen_osszevont_mondatot_kap(self):
        """Kö'''''1 — the fourth combination must NOT get both sentences.

        The earlier version printed both, and the second one ("WE WERE
        ADDRESSED, BUT…") was factually false in this case, and it also directly
        contradicted the first. Now a SINGLE, merged, true sentence goes
        out.
        """
        cel = {"kezdemenyezett": True,
               "kezdemenyezett_oka": [szures.OK_ENGEDELYLISTAS,
                                      szures.OK_IDEGEN_A_SZALBAN]}
        uzenet = elonezet.szoveg(self._kanonikus(), "jelolo", cel=cel)
        self.assertIn(elonezet.MINDKET_OK, uzenet)
        self.assertNotIn(elonezet.NEM_SZOLITOTTAK_MEG, uzenet)
        self.assertNotIn(elonezet.IDEGEN_SZALBA, uzenet)

    def test_hianyzo_ok_eseten_a_szigorubb_mondat_megy_ki(self):
        cel = {"kezdemenyezett": True}
        uzenet = elonezet.szoveg(self._kanonikus(), "jelolo", cel=cel)
        self.assertIn(elonezet.NEM_SZOLITOTTAK_MEG, uzenet)


ENGEDELYLISTAS_DID = "did:plc:engedelyezett777"


class LancIndoklasTeszt(unittest.TestCase):
    """The flag is produced by the ACTUAL code path, not a hand-written dict."""

    def _lanc(self, engedelylista, gyoker_szerzo):
        cel_uri = kozos_teszt.CEL_URI
        gyoker_uri = kozos_teszt.GYOKER_URI
        celposzt = kozos_teszt.cel_poszt(gyoker_uri=gyoker_uri,
                                         gyoker_cid=kozos_teszt.GYOKER_CID)
        gyoker = kozos_teszt.gyoker_poszt(szerzo_did=gyoker_szerzo)
        posztok = {cel_uri: celposzt, gyoker_uri: gyoker}
        return kimeno.cel_ellenorzes(celposzt, cel_uri, kozos_teszt.SAJAT_DID,
                                     engedelylista, posztok.get)

    def test_idegen_a_lancban_megszolitva_nem_kezdemenyezett(self):
        """J7 (2026-09-27): the target post addresses us, only the top of the
        thread is a stranger — this is a conversation WITH US, not initiated.
        The target still names the stranger."""
        cel, okok = self._lanc([], "did:plc:harmadikfel789")
        self.assertEqual(okok, [])
        self.assertTrue(cel["megszolitottak"])
        self.assertTrue(cel["idegen_szalban"])
        self.assertTrue(cel.get("idegen_szalban_megszolitva"))
        self.assertFalse(cel["kezdemenyezett"])
        self.assertNotIn(szures.OK_IDEGEN_A_SZALBAN, cel["kezdemenyezett_oka"])
        self.assertNotIn(szures.OK_ENGEDELYLISTAS, cel["kezdemenyezett_oka"])

    def test_engedelylistas_a_lancban_nem_idegen_de_meg_van_nevezve(self):
        """Kö''''5: instead of "no stranger" we NAME them, with an allowlisted label."""
        engedelylista = [{"did": ENGEDELYLISTAS_DID}]
        cel, okok = self._lanc(engedelylista, ENGEDELYLISTAS_DID)
        self.assertEqual(okok, [])
        self.assertFalse(cel["idegen_szalban"])
        self.assertEqual(
            [sz["did"] for sz in cel["engedelylistas_szereplok"]],
            [ENGEDELYLISTAS_DID])
        kanonikus = szures.kimeno_kanonikus("valasz", "Szia!", cel, ["hu"],
                                            False)
        uzenet = elonezet.szoveg(kanonikus, "jelolo", cel=cel)
        self.assertIn("an allowlisted party also takes part", uzenet)
        self.assertIn(ENGEDELYLISTAS_DID, uzenet)


# =========================================================================
# Kö''''4 — DEEP THREAD
# =========================================================================

class MelySzalTeszt(unittest.TestCase):

    def _lanc_posztok(self, melyseg):
        """An ancestor chain of `melyseg` items: the target post is the lowest."""
        posztok = {}
        for i in range(melyseg):
            uri = "at://did:plc:idegenfiok456/app.bsky.feed.post/l%03d" % i
            szulo = ("at://did:plc:idegenfiok456/app.bsky.feed.post/l%03d"
                     % (i + 1)) if i + 1 < melyseg else None
            rekord = {"text": "lanc %d" % i, "facets": []}
            if szulo:
                rekord["reply"] = {"parent": {"uri": szulo, "cid": "c%03d"
                                              % (i + 1)},
                                   "root": {"uri": szulo, "cid": "c%03d"
                                            % (i + 1)}}
            posztok[uri] = {
                "uri": uri, "cid": "c%03d" % i,
                "author": {"did": kozos_teszt.SAJAT_DID, "handle": "en"},
                "record": rekord}
        return posztok

    def test_tizenket_elemu_lanc_ma_atmegy(self):
        """A thread deeper than eight but shallower than twenty-five: it NO LONGER fails."""
        posztok = self._lanc_posztok(12)
        also = posztok["at://did:plc:idegenfiok456/app.bsky.feed.post/l000"]
        lanc, okok = kimeno.felmeno_lanc(also, posztok.get)
        self.assertEqual(okok, [])
        self.assertEqual(len(lanc), 12)

    def test_tul_mely_lanc_tovabbra_is_elutasitas(self):
        posztok = self._lanc_posztok(40)
        also = posztok["at://did:plc:idegenfiok456/app.bsky.feed.post/l000"]
        _lanc, okok = kimeno.felmeno_lanc(also, posztok.get)
        self.assertEqual(okok, ["felmeno_lanc_tul_hosszu"])

    def test_az_allapotfajlban_ertheto_ok_all(self):
        tetel = allapot.elutasitott_tetel(
            "proba.json", ["optin:felmeno_lanc_tul_hosszu"])
        self.assertTrue(tetel["ok_magyarul"])
        self.assertIn("deeper", tetel["ok_magyarul"][0])

    def test_a_torolt_felmeno_is_ertheto_okot_kap(self):
        tetel = allapot.elutasitott_tetel(
            "proba.json", ["optin:gyoker_nem_igazolhato"])
        self.assertTrue(tetel["ok_magyarul"])
        self.assertIn("deleted", tetel["ok_magyarul"][0])

    def test_a_lathatatlan_karakter_is_ertheto_okot_kap(self):
        tetel = allapot.elutasitott_tetel("proba.json",
                                          ["szoveg_atirasra_szorulna"])
        self.assertTrue(tetel["ok_magyarul"])
        self.assertIn("not visible", tetel["ok_magyarul"][0])


# =========================================================================
# M''''2 — THE DRAFT ENTERS THE PROTECTED WAITING FOLDER AS A NEW FILE
# =========================================================================

class BevetelMasolasTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def test_a_tartalom_atkerul_es_az_eredeti_eltunik(self):
        eredeti = kozos_teszt.piszkozat_ir(self.k, "a.json",
                                           kozos_teszt.alap_piszkozat())
        motor.bevetel(self.k, [], time.time())
        self.assertFalse(os.path.exists(eredeti))
        varo = motor.varo_fajlok(self.k)
        self.assertEqual(len(varo), 1)
        tetel, okok = piszkozat.fajlbol(varo[0])
        self.assertEqual(okok, [])
        self.assertEqual(tetel["szoveg"], "Jó reggelt mindenkinek.")

    def test_a_varobeli_fajl_uj_inode_nem_ugyanaz_a_fajl(self):
        """If we moved it, the file ID would stay. We copy, so it does not."""
        eredeti = kozos_teszt.piszkozat_ir(self.k, "a.json",
                                           kozos_teszt.alap_piszkozat())
        regi_azonosito = (os.stat(eredeti).st_ino, os.stat(eredeti).st_dev)
        motor.bevetel(self.k, [], time.time())
        uj = motor.varo_fajlok(self.k)[0]
        uj_azonosito = (os.stat(uj).st_ino, os.stat(uj).st_dev)
        self.assertNotEqual(regi_azonosito, uj_azonosito)

    def test_torolhetetlen_fajl_tartalma_nem_kerul_be_ketszer(self):
        """If deleting from the drop folder fails, the hash prevents double processing."""
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat())
        eredeti_torles = motor._bedobott_eltavolitas
        motor._bedobott_eltavolitas = lambda _ut: False
        try:
            uzenetek = []
            motor.bevetel(self.k, uzenetek, time.time())
            self.assertEqual(len(motor.varo_fajlok(self.k)), 1)
            self.assertTrue(any("could not delete it" in u for u in uzenetek))
            # second round: the same file is still there, but is NOT taken in again
            motor.bevetel(self.k, [], time.time() + 1)
            self.assertEqual(len(motor.varo_fajlok(self.k)), 1)
        finally:
            motor._bedobott_eltavolitas = eredeti_torles

    def test_a_torles_sikere_utan_a_hash_elfelejtodik(self):
        kozos_teszt.piszkozat_ir(self.k, "a.json", kozos_teszt.alap_piszkozat())
        eredeti_torles = motor._bedobott_eltavolitas
        motor._bedobott_eltavolitas = lambda _ut: False
        try:
            motor.bevetel(self.k, [], time.time())
        finally:
            motor._bedobott_eltavolitas = eredeti_torles
        motor.bevetel(self.k, [], time.time() + 1)
        self.assertEqual(tarolas_or.json_olvas(self.k.bedobott_hashek, {}), {})
        self.assertEqual(os.listdir(self.k.ki), [])

    def test_uj_fajl_nem_ir_felul_meglevot(self):
        cel = os.path.join(self.k.varo, "x.json")
        tarolas_or.uj_fajl_bajtokkal(cel, b"elso")
        with self.assertRaises(OSError):
            tarolas_or.uj_fajl_bajtokkal(cel, b"masodik")
        with open(cel, "rb") as f:
            self.assertEqual(f.read(), b"elso")

    def test_biztonsagos_beolvasas_merev_hivatkozast_elutasit(self):
        ut = kozos_teszt.piszkozat_ir(self.k, "a.json",
                                      kozos_teszt.alap_piszkozat())
        link = os.path.join(self.k.ki, "b.json")
        try:
            os.link(ut, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("a hard link cannot be created on this volume")
        bajtok, ok = piszkozat.biztonsagos_beolvasas(link)
        self.assertIsNone(bajtok)
        self.assertEqual(ok, "merev_hivatkozas")


@unittest.skipUnless(sys.platform.startswith("win"),
                     "NTFS permissions only make sense on Windows")
class ValodiAclFustTeszt(unittest.TestCase):
    """REAL `icacls` smoke test: the new file does NOT inherit the source's explicit ACE.

    This is the core of the finding. On the same volume `os.replace`
    (MoveFileEx) also carries over the file's access list and owner; a byte
    copy does not. The test works in its OWN temporary folder and cleans up at
    the end.
    """

    def setUp(self):
        self.alap = tempfile.mkdtemp(prefix="or_acl_teszt_")
        self.forras = os.path.join(self.alap, "forras")
        self.cel = os.path.join(self.alap, "cel")
        os.makedirs(self.forras)
        os.makedirs(self.cel)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.alap, ignore_errors=True)

    def _icacls(self, utvonal, *argumentumok):
        try:
            folyamat = subprocess.run(["icacls", utvonal] + list(argumentumok),
                                      capture_output=True, timeout=30,
                                      check=False)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("icacls cannot be run")
        return (folyamat.stdout or b"").decode("oem", errors="replace")

    def _kulon_ace_vel(self, nev):
        """A file in the source folder with an EXPLICIT (non-inherited) access entry."""
        utvonal = os.path.join(self.forras, nev)
        with open(utvonal, "wb") as f:
            f.write(b'{"proba": 1}')
        # SID of the "Everyone" group — language-independent form.
        self._icacls(utvonal, "/grant", "*S-1-1-0:(M)")
        return utvonal

    @staticmethod
    def _van_benne_kulon_ace(kimenet):
        # "Mindenki" is the Hungarian-localized name of "Everyone" in icacls
        # output (Windows language data); extend the tuple for other locales.
        return any(jel in kimenet
                   for jel in ("S-1-1-0", "Mindenki", "Everyone"))

    def test_a_bajt_masolas_nem_viszi_at_a_kulon_ace_t(self):
        # 1. The OLD way (move): the explicit entry IS CARRIED OVER — this was the finding.
        regi = self._kulon_ace_vel("regi.json")
        if not self._van_benne_kulon_ace(self._icacls(regi)):
            self.skipTest("an explicit ACE cannot be set on this volume")
        regi_cel = os.path.join(self.cel, "regi.json")
        os.replace(regi, regi_cel)
        self.assertTrue(
            self._van_benne_kulon_ace(self._icacls(regi_cel)),
            "unexpected: moving no longer carries over the access list")

        # 2. The NEW way (read + new file): the explicit entry is NOT carried over.
        uj = self._kulon_ace_vel("uj.json")
        bajtok, ok = piszkozat.biztonsagos_beolvasas(uj)
        self.assertIsNone(ok)
        uj_cel = os.path.join(self.cel, "uj.json")
        tarolas_or.uj_fajl_bajtokkal(uj_cel, bajtok)
        os.remove(uj)
        self.assertFalse(
            self._van_benne_kulon_ace(self._icacls(uj_cel)),
            "the new file INHERITED the explicit entry set on the source file")
        with open(uj_cel, "rb") as f:
            self.assertEqual(f.read(), b'{"proba": 1}')
        self.assertFalse(os.path.exists(uj))


# =========================================================================
# Kö''''6 — THE OWNER OF THE DROP FOLDER
# (Sample icacls output below mimics a Hungarian-localized Windows:
# "Rendszergazdák" = "Administrators"; keep it in sync with what jogok.py expects.)
# =========================================================================

_JO_SOROK = (
    "{ut} NT AUTHORITY\\SYSTEM:(OI)(CI)(F)\n"
    "{ut} BUILTIN\\Rendszergazdák:(OI)(CI)(F)\n"
    "{ut} PC\\bsky-guard:(OI)(CI)(F)\n")
_KI_SOROK = _JO_SOROK + \
    "{ut} PC\\agent-user:(OI)(CI)(M)\n" \
    "{ut} PC\\agent-user:(DENY)(DE)\n"


class BedoboTulajdonosTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def _lekero(self, utvonal):
        sablon = _KI_SOROK if os.path.basename(utvonal) == "ki" else _JO_SOROK
        return sablon.format(ut=utvonal)

    def _tulajdonos(self, felulirasok):
        def lekero(utvonal):
            return felulirasok.get(os.path.basename(utvonal),
                                   "PC\\bsky-guard")
        return lekero

    def test_minden_jo_ha_az_or_a_tulajdonos(self):
        hibak = jogok.jog_ellenorzes(self.k, self._lekero,
                                     tulajdonos_lekero=self._tulajdonos({}))
        self.assertEqual(hibak, [])

    def test_a_kozos_ki_tulajdonosa_nem_lehet_a_nem_megbizhato_felhasznalo(self):
        """This is the finding: the protection of the `/deny` line DEPENDS ON
        that user not being the owner.

        The owner has the implicit right to change permissions (`WRITE_DAC`),
        so a single command could remove the deny entry.
        """
        hibak = jogok.jog_ellenorzes(
            self.k, self._lekero,
            tulajdonos_lekero=self._tulajdonos({"ki": "PC\\agent-user"}))
        self.assertTrue(any("kozos\\ki" in h and "OWNER" in h
                            for h in hibak), hibak)

    def test_mind_a_het_mappa_tulajdonosat_nezzuk(self):
        nevek = ("BskyGuard", "titok", "program", "allapot", "kozos", "be", "ki")
        for nev in nevek:
            with self.subTest(nev):
                mappanev = ("BskyGuard" if nev == "BskyGuard"
                            else nev)
                felulirasok = {os.path.basename(self.k.gyoker): "PC\\agent-user"} \
                    if nev == "BskyGuard" else {mappanev: "PC\\agent-user"}
                hibak = jogok.jog_ellenorzes(
                    self.k, self._lekero,
                    tulajdonos_lekero=self._tulajdonos(felulirasok))
                self.assertTrue(any("OWNER" in h for h in hibak),
                                "%s: %s" % (nev, hibak))


# =========================================================================
# A''''1 — THE SKIP LIST EXPIRES
# =========================================================================

class KihagyandoElevulesTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def test_egy_hetnel_regebbi_bejegyzes_elenyeszik(self):
        most = time.time()
        regi = int(most - beallitasok.KIHAGYANDO_ELAVULAS - 10)
        motor._kihagyando_ment(self.k, {"regi.json": (5, regi),
                                        "friss.json": (5, int(most))})
        tabla = motor._kihagyando_betolt(self.k, most)
        self.assertNotIn("regi.json", tabla)
        self.assertIn("friss.json", tabla)

    def test_a_regi_szam_alaku_bejegyzest_is_elfogadjuk(self):
        tarolas_or.json_ir(self.k.kihagyando, {"regi_alak.json": 2})
        tabla = motor._kihagyando_betolt(self.k, time.time())
        self.assertEqual(tabla["regi_alak.json"][0], 2)


def _json_szoveg(szoveg):
    import json
    return json.dumps(szoveg, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
