"""`ujjlenyomat.py` — the checksum step of an update (K''2).

This script on its own protects against NOTHING: it is only worth something if the owner
records the fingerprint on a channel the untrusted side cannot rewrite
afterwards. What we test here: the fingerprint really changes when the content
changes, and the comparison NAMES the file that changed."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ujjlenyomat  # noqa: E402


class UjjlenyomatTeszt(unittest.TestCase):

    def setUp(self):
        self.mappa = tempfile.mkdtemp(prefix="ujjlenyomat_teszt_")
        self._ir("motor.py", "print('eredeti')\n")
        self._ir("tilto_mintak.json", '{"regexek": []}\n')
        os.makedirs(os.path.join(self.mappa, "__pycache__"), exist_ok=True)
        self._ir("__pycache__/motor.pyc", "bináris-utánzat")

    def tearDown(self):
        shutil.rmtree(self.mappa, ignore_errors=True)

    def _ir(self, nev, tartalom):
        utvonal = os.path.join(self.mappa, nev.replace("/", os.sep))
        os.makedirs(os.path.dirname(utvonal), exist_ok=True)
        with open(utvonal, "w", encoding="utf-8", newline="\n") as f:
            f.write(tartalom)
        return utvonal

    def test_a_pycache_kimarad(self):
        lenyomatok = ujjlenyomat.mappa_lenyomatai(self.mappa)
        self.assertEqual(sorted(lenyomatok), ["motor.py", "tilto_mintak.json"])

    def test_a_lenyomat_valtozik_a_tartalommal(self):
        elotte = ujjlenyomat.mappa_lenyomatai(self.mappa)
        self._ir("motor.py", "print('BECSEMPESZETT')\n")
        utana = ujjlenyomat.mappa_lenyomatai(self.mappa)
        self.assertNotEqual(elotte["motor.py"], utana["motor.py"])
        self.assertNotEqual(ujjlenyomat.osszesitett(elotte),
                            ujjlenyomat.osszesitett(utana))

    def test_az_osszesitett_valtozik_uj_fajltol_is(self):
        elotte = ujjlenyomat.osszesitett(
            ujjlenyomat.mappa_lenyomatai(self.mappa))
        self._ir("hatso_ajto.py", "# ártalmatlannak látszó új fájl\n")
        utana = ujjlenyomat.osszesitett(
            ujjlenyomat.mappa_lenyomatai(self.mappa))
        self.assertNotEqual(elotte, utana)

    def test_az_osszesitett_valtozik_atnevezestol_is(self):
        elotte = ujjlenyomat.osszesitett(
            ujjlenyomat.mappa_lenyomatai(self.mappa))
        os.replace(os.path.join(self.mappa, "motor.py"),
                   os.path.join(self.mappa, "motor2.py"))
        utana = ujjlenyomat.osszesitett(
            ujjlenyomat.mappa_lenyomatai(self.mappa))
        self.assertNotEqual(elotte, utana)

    def test_korabbi_lista_visszaolvashato(self):
        lista = os.path.join(self.mappa, "lenyomat.txt")
        kod = ujjlenyomat.main([self.mappa, "--kiir", lista],
                               kiir=lambda _s: None)
        self.assertEqual(kod, 0)
        visszaolvasott = ujjlenyomat.beolvas(lista)
        self.assertEqual(visszaolvasott,
                         ujjlenyomat.mappa_lenyomatai(self.mappa))

    def test_osszevetes_megnevezi_a_valtozast(self):
        lista = os.path.join(tempfile.gettempdir(), "ujjlenyomat_lista.txt")
        self.addCleanup(lambda: os.path.exists(lista) and os.remove(lista))
        ujjlenyomat.main([self.mappa, "--kiir", lista], kiir=lambda _s: None)

        self._ir("motor.py", "print('BECSEMPESZETT')\n")
        self._ir("hatso_ajto.py", "# új\n")
        os.remove(os.path.join(self.mappa, "tilto_mintak.json"))

        kimenet = []
        kod = ujjlenyomat.main([self.mappa, "--osszevet", lista],
                               kiir=kimenet.append)
        egyben = "\n".join(kimenet)
        self.assertEqual(kod, 1)
        self.assertIn("CHANGED", egyben)
        self.assertIn("motor.py", egyben)
        self.assertIn("hatso_ajto.py", egyben)
        self.assertIn("MISSING", egyben)

    def test_valtozatlan_mappa_nullaval_ter_vissza(self):
        lista = os.path.join(tempfile.gettempdir(), "ujjlenyomat_lista2.txt")
        self.addCleanup(lambda: os.path.exists(lista) and os.remove(lista))
        ujjlenyomat.main([self.mappa, "--kiir", lista], kiir=lambda _s: None)
        kimenet = []
        kod = ujjlenyomat.main([self.mappa, "--osszevet", lista],
                               kiir=kimenet.append)
        self.assertEqual(kod, 0)
        self.assertIn("EVERYTHING MATCHES", "\n".join(kimenet))


if __name__ == "__main__":
    unittest.main()
