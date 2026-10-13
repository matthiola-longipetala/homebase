"""The logic of the startup ACL self-check, with a MOCKED permission query.

If these tests fail, the guard would start even with wrong permissions —
and then the whole separate-user protection is just scenery."""

import importlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import beallitasok  # noqa: E402
import jogok  # noqa: E402
import kozos_teszt  # noqa: E402
import tarolas_or  # noqa: E402

# Hungarian language data: sample `icacls` output from a Hungarian-localised
# Windows ("Rendszergazdák" = Administrators, "Felhasználók" = Users,
# "Mindenki" = Everyone). Replace/extend with the localised group names of
# another Windows language if needed.
JO = ("{ut} NT AUTHORITY\\SYSTEM:(OI)(CI)(F)\n"
      "{ut} BUILTIN\\Rendszergazdák:(OI)(CI)(F)\n"
      "{ut} PC\\bsky-guard:(OI)(CI)(M)\n"
      "\nSuccessfully processed 1 files; Failed processing 0 files\n")

JO_OLVASHATO = JO + "{ut} PC\\agent-user:(OI)(CI)(RX)\n"

# The CORRECT setup of the drop (`kozos\ki`) folder: the untrusted user
# may write into it, but cannot delete the folder itself (K'''4).
KI_JO = (JO + "{ut} PC\\agent-user:(OI)(CI)(M)\n"
              "{ut} PC\\agent-user:(DENY)(DE)\n")
# The same WITHOUT the delete-denying line — this is the setup that allows
# a silent denial of service.
KI_TORLES_TILTAS_NELKUL = JO + "{ut} PC\\agent-user:(OI)(CI)(M)\n"


def _kimenet(sablon, utvonal):
    return sablon.format(ut=utvonal)


class ErtelmezesTeszt(unittest.TestCase):

    def test_bejegyzesek_kiolvasasa(self):
        b = jogok.bejegyzesek_ertelmezese(_kimenet(JO, "C:\\X"), "C:\\X")
        nevek = [jogok._normalizal_identitas(n) for n, _, _ in b]
        self.assertIn("system", nevek)
        self.assertIn("bsky-guard", nevek)

    def test_ertelmezhetetlen_sor_kivetel(self):
        with self.assertRaises(jogok.JogHiba):
            jogok.bejegyzesek_ertelmezese("C:\\X ez egy zagyva sor\n", "C:\\X")

    def test_ures_kimenet_kivetel(self):
        with self.assertRaises(jogok.JogHiba):
            jogok.bejegyzesek_ertelmezese("", "C:\\X")

    def test_magyar_es_angol_csoportnev_ugyanaz(self):
        self.assertEqual(jogok._normalizal_identitas("BUILTIN\\Rendszergazdák"),
                         jogok._normalizal_identitas("BUILTIN\\Rendszergazd?k"))


class SzabalyTeszt(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def _lekero(self, sablonok):
        def lekero(utvonal):
            nev = os.path.basename(utvonal)
            # The default, CORRECT setup of the drop folder differs from the
            # others: the untrusted user may write there on purpose.
            alap = KI_JO if nev == "ki" else JO
            sablon = sablonok.get(nev, alap)
            return _kimenet(sablon, utvonal)
        return lekero

    def _tulajdonos(self, felulirasok=None):
        """Mocked owner query; by default the guard owns every folder."""
        felulirasok = felulirasok or {}
        def lekero(utvonal):
            return felulirasok.get(os.path.basename(utvonal),
                                   "PC\\bsky-guard")
        return lekero

    def _ellenoriz(self, sablonok, tulajdonosok=None):
        return jogok.jog_ellenorzes(self.k, self._lekero(sablonok),
                                    tulajdonos_lekero=self._tulajdonos(
                                        tulajdonosok))

    def test_ok_helyes_jogok(self):
        hibak = self._ellenoriz({
            "titok": JO, "program": JO_OLVASHATO,
            "allapot": JO_OLVASHATO, "be": JO_OLVASHATO})
        self.assertEqual(hibak, [])

    def test_titok_olvashato_ugynoknek(self):
        hibak = self._ellenoriz({"titok": JO_OLVASHATO})
        self.assertTrue(any("titok" in h for h in hibak))

    def test_program_irhato_ugynoknek(self):
        rossz = JO + "{ut} PC\\agent-user:(OI)(CI)(M)\n"
        hibak = self._ellenoriz({"program": rossz})
        self.assertTrue(any("program" in h for h in hibak))

    def test_allapot_irhato_a_felhasznalok_csoportnak(self):
        """`agent-user` is not the only danger: access can also come via the group."""
        rossz = JO + "{ut} BUILTIN\\Felhasználók:(OI)(CI)(M)\n"
        hibak = self._ellenoriz({"allapot": rossz})
        self.assertTrue(any("allapot" in h for h in hibak))

    def test_kozos_be_irhato(self):
        rossz = JO + "{ut} Mindenki:(OI)(CI)(W)\n"
        hibak = self._ellenoriz({"be": rossz})
        self.assertTrue(any("be" in h for h in hibak))

    def test_ismeretlen_jog_token_talalatnak_szamit(self):
        rossz = JO + "{ut} PC\\agent-user:(OI)(CI)(XYZ)\n"
        hibak = self._ellenoriz({"titok": rossz})
        self.assertTrue(hibak)

    def test_deny_bejegyzes_nem_ad_jogot(self):
        jo = JO + "{ut} PC\\agent-user:(DENY)(OI)(CI)(F)\n"
        hibak = self._ellenoriz({"titok": jo})
        self.assertEqual(hibak, [])

    def test_hianyzo_mappa_megallit(self):
        os.rmdir(self.k.ki)
        hibak = self._ellenoriz({})
        self.assertTrue(any("missing folder" in h for h in hibak))

    # --- M''1: root, kozos, subfolders and the OWNER ---------------------

    def test_gyokermappa_irhato_megallit(self):
        """On the root, the Modify right also grants `FILE_DELETE_CHILD`: the
        `allapot` folder could thus be RENAMED, even without rights on it."""
        rossz = JO + "{ut} PC\\agent-user:(OI)(CI)(M)\n"
        gyoker_nev = os.path.basename(self.k.gyoker)
        hibak = self._ellenoriz({gyoker_nev: rossz})
        self.assertTrue(any("(root)" in h for h in hibak), hibak)

    def test_kozos_mappa_irhato_megallit(self):
        rossz = JO + "{ut} Mindenki:(OI)(CI)(M)\n"
        hibak = self._ellenoriz({"kozos": rossz})
        self.assertTrue(any(h.startswith("the kozos ") for h in hibak), hibak)

    def test_allapot_alkonyvtarai_is_ellenorzodnek(self):
        rossz = JO + "{ut} PC\\agent-user:(OI)(CI)(M)\n"
        for mappa, cimke in (("elonezetek", "elonezetek"),
                             ("feldolgozott", "feldolgozott"),
                             ("varo", "varo"), ("archiv", "archiv")):
            hibak = self._ellenoriz({mappa: rossz})
            self.assertTrue(any(cimke in h for h in hibak), (mappa, hibak))

    def test_idegen_tulajdonos_megallit(self):
        """The owner has an implicit WRITE_DAC right: they can grant themselves
        access again at any time, however clean the access list is."""
        hibak = self._ellenoriz({}, {"allapot": "PC\\agent-user"})
        self.assertTrue(any("OWNER" in h for h in hibak), hibak)

    def test_ismeretlen_tulajdonos_megallit(self):
        hibak = self._ellenoriz({}, {"titok": ""})
        self.assertTrue(any("cannot be determined" in h
                            for h in hibak), hibak)

    def test_artalmatlan_token_nem_allit_meg(self):
        """Kö4: synchronize and read-access-list rights do not grant
        data access."""
        jo = JO + "{ut} BUILTIN\\Felhasználók:(OI)(CI)(S,RC)\n"
        hibak = self._ellenoriz({"program": jo})
        self.assertEqual(hibak, [])

    def test_ismeretlen_token_sajat_uzenetet_kap(self):
        rossz = JO + "{ut} BUILTIN\\Felhasználók:(OI)(CI)(ZZZ)\n"
        hibak = self._ellenoriz({"program": rossz})
        self.assertTrue(any("unknown permission marker" in h
                            for h in hibak), hibak)

    # --- K'''4 and M'''2: the drop (`kozos\ki`) folder -------------------

    def test_bedobo_torles_tiltas_nelkul_megallit(self):
        """The untrusted user must not be able to DELETE the drop folder:
        from then on the guard would refuse to start with a "missing folder: ki"
        error."""
        hibak = self._ellenoriz({"ki": KI_TORLES_TILTAS_NELKUL})
        self.assertTrue(any("DENYING DELETION" in h for h in hibak), hibak)

    def test_bedobo_helyes_beallitassal_rendben(self):
        self.assertEqual(self._ellenoriz({"ki": KI_JO}), [])

    def test_bedobo_idegen_iroja_megallit(self):
        """Nobody OTHER than the untrusted user may write into the drop folder."""
        rossz = KI_JO + "{ut} Mindenki:(OI)(CI)(M)\n"
        hibak = self._ellenoriz({"ki": rossz})
        self.assertTrue(any("unauthorized" in h and "ki" in h for h in hibak),
                        hibak)

    def test_bedobo_idegen_tulajdonos_megallit(self):
        """Because of the owner's implicit WRITE_DAC right, the untrusted user
        must not be the owner of the drop folder either."""
        hibak = self._ellenoriz({}, {"ki": "PC\\agent-user"})
        self.assertTrue(any("OWNER" in h for h in hibak), hibak)

    def test_or_nem_indul_rossz_jogokkal(self):
        # `or` is a keyword, so `import or` cannot be written — but the module
        # can be loaded by name (Task Scheduler starts the script as `or.py`).
        or_modul = importlib.import_module("or")
        kimenet = []
        kod = or_modul.main(["--ellenoriz", "--gyoker", self.k.gyoker],
                            kiir=kimenet.append, kornyezet=self.k,
                            jog_lekero=self._lekero({"titok": JO_OLVASHATO}),
                            admin_lekero=lambda: "",
                            tulajdonos_lekero=self._tulajdonos())
        self.assertEqual(kod, 3)
        self.assertTrue(any("WILL NOT START" in s for s in kimenet))

    def test_or_indul_jo_jogokkal(self):
        or_modul = importlib.import_module("or")
        kimenet = []
        kod = or_modul.main(["--ellenoriz", "--gyoker", self.k.gyoker],
                            kiir=kimenet.append, kornyezet=self.k,
                            jog_lekero=self._lekero({}),
                            admin_lekero=lambda: "",
                            tulajdonos_lekero=self._tulajdonos())
        self.assertEqual(kod, 0)

    def test_admin_figyelmeztetes_az_allapotfajlba_kerul(self):
        """K''4: the warning must be visible even without a console."""
        or_modul = importlib.import_module("or")
        rendben, figyelmeztetesek = or_modul.jogok_kapu(
            self.k, lambda _s: None, self._lekero({}),
            lambda: "PC\\agent-user\n", self._tulajdonos())
        self.assertTrue(rendben)
        self.assertTrue(figyelmeztetesek)

        import kozos_teszt as kt
        import motor
        adat = motor.egy_kor(self.k, kt.MockHalo(), kt.MockBot(),
                             bejovo_is=False,
                             indulasi_uzenetek=figyelmeztetesek)
        self.assertTrue(any("Administrators group" in u
                            for u in adat["uzenetek"]), adat["uzenetek"])

    def test_admin_figyelmeztetes_telegramra_is_kimegy(self):
        or_modul = importlib.import_module("or")
        import kozos_teszt as kt
        bot = kt.MockBot()
        or_modul.indulasi_ertesites(bot, ["FIGYELEM: próba-figyelmeztetés."],
                                    lambda _s: None)
        self.assertEqual(len(bot.kuldott), 1)
        self.assertIn("próba-figyelmeztetés", bot.kuldott[0]["szoveg"])

    def test_admin_tagsag_figyelmeztetes(self):
        uzenet = jogok.admin_tagsag_figyelmeztetes(
            lambda: "PC\\admin\nPC\\agent-user\n", "agent-user")
        self.assertIsNotNone(uzenet)
        self.assertIn("Administrators group", uzenet)

    def test_admin_tagsag_rendben(self):
        self.assertIsNone(jogok.admin_tagsag_figyelmeztetes(
            lambda: "PC\\admin\nPC\\Rendszergazda\n", "agent-user"))

    def test_admin_tagsag_nem_ellenorizheto(self):
        def robbano():
            raise OSError("no permission")
        uzenet = jogok.admin_tagsag_figyelmeztetes(robbano, "agent-user")
        self.assertIn("cannot be checked", uzenet)


@unittest.skipUnless(sys.platform.startswith("win"),
                     "Windows-specific external commands")
class ValodiFustTeszt(unittest.TestCase):
    """REAL, NON-MOCKED smoke tests for the external commands (K'''1).

    The most serious finding of the fourth review was that the command line
    of `jogok._powershell_tulajdonos` was broken, so on the real machine the
    guard would not have completed a single round — yet the test suite did
    not notice, because EVERY test passed in a mocked query and none of them
    ever called the body of the live function.

    The general lesson: EVERY function that calls an external command must
    have at least one real smoke test. These calls are exclusively READ-ONLY,
    local queries (`icacls`, `Get-Acl`, `Get-LocalGroupMember`, `tasklist`)
    on a temporary folder or on our own process — they do not touch the
    network, Telegram or machine settings.
    """

    @classmethod
    def setUpClass(cls):
        cls.mappa = tempfile.mkdtemp(prefix="or_fust_")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.mappa, ignore_errors=True)

    def test_powershell_tulajdonos_valodi_mappan(self):
        """The LIVE owner query must run and return a name."""
        tulajdonos = jogok._powershell_tulajdonos(self.mappa)
        self.assertTrue(tulajdonos,
                        "the owner query returned an empty answer")
        self.assertIn("\\", tulajdonos,
                      "the owner is not in `DOMAIN\\name` form: %r"
                      % tulajdonos)

    def test_powershell_tulajdonos_normalizalhato(self):
        """The returned name also passes the `_normalizal_identitas` filter."""
        tulajdonos = jogok._powershell_tulajdonos(self.mappa)
        self.assertTrue(jogok._normalizal_identitas(tulajdonos))

    def test_powershell_tulajdonos_szokozos_utvonalon(self):
        """A path containing spaces and accented letters must not break the command.

        This was exactly the pitfall of the old `-args` form: command line
        parsing. The environment-variable form has no such issue.
        """
        alut = os.path.join(self.mappa, "szóköz és ékezet")
        os.makedirs(alut, exist_ok=True)
        self.assertTrue(jogok._powershell_tulajdonos(alut))

    def test_powershell_tulajdonos_nem_letezo_mappan_joghibat_dob(self):
        """FAIL-CLOSED: what we cannot query, we do not accept."""
        with self.assertRaises(jogok.JogHiba):
            jogok._powershell_tulajdonos(
                os.path.join(self.mappa, "nincs_ilyen_mappa_12345"))

    def test_icacls_lekero_valodi_mappan(self):
        """The LIVE `icacls` query must run and be PARSEABLE."""
        kimenet = jogok.icacls_lekero(self.mappa)
        self.assertTrue(kimenet.strip())
        bejegyzesek = jogok.bejegyzesek_ertelmezese(kimenet, self.mappa)
        self.assertTrue(bejegyzesek, "not a single permission line was found")
        for azonosito, jog_lista, _tiltas in bejegyzesek:
            self.assertTrue(azonosito.strip())
            self.assertTrue(jog_lista, azonosito)

    def test_icacls_lekero_nem_letezo_mappan_joghibat_dob(self):
        with self.assertRaises(jogok.JogHiba):
            jogok.icacls_lekero(
                os.path.join(self.mappa, "nincs_ilyen_mappa_12345"))

    def test_teljes_jog_ellenorzes_valodi_lekerdezessel(self):
        """The full self-check with REAL `icacls` and owner calls.

        The temporary folder's permissions obviously do not meet the guard's
        expectations, so we EXPECT errors — the point is that the queries run
        and the function does not stop with an exception.
        """
        kornyezet = beallitasok.Kornyezet(
            os.path.join(self.mappa, "BskyGuardProba"))
        for mappa in (kornyezet.titok, kornyezet.program, kornyezet.allapot,
                      kornyezet.kozos, kornyezet.be, kornyezet.ki):
            os.makedirs(mappa, exist_ok=True)
        hibak = jogok.jog_ellenorzes(kornyezet)
        self.assertIsInstance(hibak, list)
        self.assertTrue(hibak, "it must find errors on the test tree")

    def test_admin_tagok_valodi_lekerdezese(self):
        """The LIVE query of the Administrators group must run."""
        kimenet = jogok._powershell_admin_tagok()
        self.assertIsInstance(kimenet, str)
        # The warning function must not raise on the live output either.
        uzenet = jogok.admin_tagsag_figyelmeztetes()
        self.assertTrue(uzenet is None or isinstance(uzenet, str))

    def test_tasklist_valodi_lekerdezese(self):
        """The `tasklist`-based process query, LIVE (`tarolas_or`).

        Our OWN process must be running; for a surely non-existent PID,
        however, it must not say "running" — otherwise a stuck lock file
        would never be released.
        """
        self.assertTrue(tarolas_or.fut_e_a_folyamat(os.getpid()))
        self.assertFalse(tarolas_or.fut_e_a_folyamat(999999))


class KulcsFajlTeszt(unittest.TestCase):
    """M'''0/c: Notepad's "UTF-8 BOM" variant must not cause a false error either."""

    def setUp(self):
        self.mappa = tempfile.mkdtemp(prefix="or_kulcs_")

    def tearDown(self):
        shutil.rmtree(self.mappa, ignore_errors=True)

    def _ir(self, kodolas):
        utvonal = os.path.join(self.mappa, "proba.env")
        with open(utvonal, "w", encoding=kodolas) as f:
            f.write("OR_BOT_TOKEN=abc\nOWNER_USER_ID=1\n")
        return utvonal

    def test_bom_nelkul(self):
        adat = tarolas_or.kulcs_ertek_betolt(
            self._ir("utf-8"), ("OR_BOT_TOKEN", "OWNER_USER_ID"))
        self.assertEqual(adat["OR_BOT_TOKEN"], "abc")

    def test_bom_mel_is_olvashato(self):
        adat = tarolas_or.kulcs_ertek_betolt(
            self._ir("utf-8-sig"), ("OR_BOT_TOKEN", "OWNER_USER_ID"))
        self.assertEqual(adat["OR_BOT_TOKEN"], "abc")
        self.assertNotIn("﻿OR_BOT_TOKEN", adat)


if __name__ == "__main__":
    unittest.main()
