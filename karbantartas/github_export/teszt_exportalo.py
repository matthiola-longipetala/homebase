"""Standalone test for the exporter: uses a temporary source tree and a MADE-UP blocklist (no real names in it)."""
import json, os, sys, tempfile, unittest, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exportalo, szuro  # noqa: E402


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.src = os.path.join(self.d, "src"); os.makedirs(os.path.join(self.src, "pkg"))
        self.tl = os.path.join(self.d, "tl.json")
        json.dump({"szavak": ["Zsófi", "Kukutyin"], "regexek": []}, open(self.tl, "w", encoding="utf-8"))
        self.man = os.path.join(self.d, "man.json")
        json.dump({"include": ["pkg/*"], "exclude": ["*.bak*"],
                   "csere": [{"minta": "C:/" + "Users/[^/]+", "uj": "{HOME}"}],
                   "binaris_engedely": [".png"]}, open(self.man, "w", encoding="utf-8"))
        self.jel = os.path.join(self.d, "jel.txt")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def ir(self, rel, sz, mod="w"):
        open(os.path.join(self.src, rel), mod, encoding=None if "b" in mod else "utf-8").write(sz)

    def fut(self, cel="out", szaraz=False):
        args = ["--manifest", self.man, "--forras", self.src, "--cel", os.path.join(self.d, cel),
                "--tiltolista", self.tl, "--jelentes", self.jel] + (["--szaraz"] if szaraz else [])
        return exportalo.main(args)

    def test_tiszta_es_komment_kivagas(self):
        self.ir("pkg/a.py", 'x = 1  # 10-11 Zsófi kérése: így\n# 09-27 Zsófi: takarékos mód\ny = "#nem komment"\n')
        self.ir("pkg/doc.md", "Telepítés: a mappa a C:/" + "Users/valaki/proj alatt.\n")
        self.assertEqual(self.fut(), 0)
        ki = open(os.path.join(self.d, "out", "pkg", "a.py"), encoding="utf-8").read()
        self.assertNotIn("Zsófi", ki); self.assertIn("x = 1\n", ki); self.assertIn('"#nem komment"', ki)
        self.assertNotIn("09-27", ki)
        self.assertIn("{HOME}/proj", open(os.path.join(self.d, "out", "pkg", "doc.md"), encoding="utf-8").read())

    def test_helyorzo_a_kodban_blokkol(self):
        # a {HOME} that ends up in CODE = the file can't run yet → not ready
        self.ir("pkg/a.py", 'UT = "C:/' + 'Users/valaki/proj"\n')
        self.assertEqual(self.fut(), 2)
        self.assertIn("placeholder in code", open(self.jel, encoding="utf-8").read())

    def test_sztringben_nev_blokkol(self):
        self.ir("pkg/b.py", 'uzenet = "Szia Zsófi!"\n')
        self.assertEqual(self.fut(), 2)
        self.assertFalse(os.path.exists(os.path.join(self.d, "out")))
        jel = open(self.jel, encoding="utf-8").read()
        self.assertIn("blocklist", jel); self.assertNotIn("Zsófi", jel)   # the report masks

    def test_titok_blokkol(self):
        hamis = "1234" + "56789:" + "A" * 35   # assembled at runtime so the source itself is not token-shaped
        self.ir("pkg/c.py", f'TOKEN = "{hamis}"\n')
        self.assertEqual(self.fut(), 2)

    def test_ekezetfuggetlen_tiltolista(self):
        # accent-insensitive: the blocklist word "Zsófi" must also catch "Zsofi" and the lengthened "Zsófié"
        tilto = szuro.tiltolista_betolt(self.tl)
        for sor in ("Szia Zsofi!", "ez Zsófié", "ZSOFI", "Kukutyinban"):
            self.assertTrue(szuro.tilto_talal(sor, tilto), sor)
        self.assertFalse(szuro.tilto_talal("Zsák", tilto))

    def test_dokumentacios_ip_engedett(self):
        # RFC 5737 example ranges pass, a private LAN address still blocks
        self.assertEqual([f for _, f, _ in szuro.vizsgal("host = 192.0.2.10", [])], [])
        self.assertEqual([f for _, f, _ in szuro.vizsgal("host = 203.0.113.7", [])], [])
        self.assertIn("IPv4", [f for _, f, _ in szuro.vizsgal("host = 10.1." + "2.3", [])])

    def test_md_proza_nem_vag_hanem_blokkol(self):
        self.ir("pkg/README.md", "Ezt Kukutyinban írtuk.\n")
        self.assertEqual(self.fut(), 2)

    def test_ip_es_email_es_azonosito(self):
        # the samples are assembled at runtime so this test file itself passes the filter
        ip, mail, chat = ".".join(["192", "168", "1", "50"]), "valaki" + "@" + "gmail.com", "81234" + "56789"
        self.ir("pkg/d.py", f'PI = "{ip}"\nM = "{mail}"\nCHAT = {chat}\nOK = "127.0.0.1"\n')
        self.assertEqual(self.fut(), 2)
        jel = open(self.jel, encoding="utf-8").read()
        for f in ("IPv4", "e-mail", "long identifier"):
            self.assertIn(f, jel)
        self.assertEqual(jel.count("IPv4"), 1)   # 127.0.0.1 is allowed

    def test_binaris(self):
        self.ir("pkg/kep.png", b"\x89PNG", "wb"); self.ir("pkg/x.bin", b"\x00", "wb")
        self.assertEqual(self.fut(), 2)          # .bin is not allowed → blocks
        os.remove(os.path.join(self.src, "pkg", "x.bin"))
        self.assertEqual(self.fut("out2"), 0)    # .png may go out, but it is listed in the report
        self.assertIn("binary (review by hand", open(self.jel, encoding="utf-8").read())

    def test_szaraz_es_meglevo_cel(self):
        self.ir("pkg/a.py", "x = 1\n")
        self.assertEqual(self.fut(szaraz=True), 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "out")))
        os.makedirs(os.path.join(self.d, "van"))
        self.assertEqual(self.fut("van"), 1)

    def test_tiltolista_nelkul_nem_fut(self):
        os.remove(self.tl)
        with self.assertRaises(FileNotFoundError):
            self.fut()

    def test_munkapeldany(self):
        self.ir("pkg/b.py", 'uzenet = "Szia Zsófi!"\n')
        self.assertEqual(exportalo.main(["--manifest", self.man, "--forras", self.src, "--cel",
                                         os.path.join(self.d, "munka"), "--tiltolista", self.tl,
                                         "--jelentes", self.jel, "--munkapeldany"]), 0)
        self.assertTrue(os.path.exists(os.path.join(self.d, "munka", "pkg", "b.py")))   # the name may remain
        hamis = "1234" + "56789:" + "A" * 35
        self.ir("pkg/c.py", f'TOKEN = "{hamis}"\n')
        self.assertEqual(exportalo.main(["--manifest", self.man, "--forras", self.src, "--cel",
                                         os.path.join(self.d, "munka2"), "--tiltolista", self.tl,
                                         "--jelentes", self.jel, "--munkapeldany"]), 2)   # never with a secret
        self.assertFalse(os.path.exists(os.path.join(self.d, "munka2")))

    def test_bak_kizarva(self):
        self.ir("pkg/a.py", "x = 1\n"); self.ir("pkg/a.py.bak_regi", "Zsófi\n")
        self.assertEqual(self.fut(), 0)
        self.assertFalse(os.path.exists(os.path.join(self.d, "out", "pkg", "a.py.bak_regi")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
