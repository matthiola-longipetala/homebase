# -*- coding: utf-8 -*-
"""The Bluesky session is renewed ON TIME (finding of 2026-09-21: the token
expires in ~2 hours, but the guard runs for days in one process -> HTTP 400)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import beallitasok  # noqa: E402
import motor  # noqa: E402


class _Korny:
    bsky_kulcs = "nem-letezo"


class SessionMegujitas(unittest.TestCase):
    def _halo(self, belepesek):
        halo = motor.EloHalo(_Korny())

        def hamis_belepes(handle, jelszo):
            belepesek.append(handle)
            return {"did": "did:plc:x", "handle": handle,
                    "accessJwt": "t%d" % len(belepesek)}
        return halo, hamis_belepes

    def test_frissen_nem_lep_be_ujra_de_90_perc_utan_igen(self):
        belepesek = []
        halo, hamis = self._halo(belepesek)
        kulcs = {"BSKY_HANDLE": "h", "BSKY_APP_PASSWORD": "p"}
        with mock.patch("tarolas_or.kulcs_ertek_betolt", return_value=kulcs), \
                mock.patch("bsky_api.belepes", side_effect=hamis), \
                mock.patch("motor.time.time") as ido:
            ido.return_value = 1000.0
            self.assertEqual(halo._s()["accessJwt"], "t1")
            ido.return_value = 1000.0 + beallitasok.SESSION_MAX_KOR - 1
            self.assertEqual(halo._s()["accessJwt"], "t1")
            self.assertEqual(len(belepesek), 1)
            ido.return_value = 1000.0 + beallitasok.SESSION_MAX_KOR
            self.assertEqual(halo._s()["accessJwt"], "t2")
            self.assertEqual(len(belepesek), 2)

    def test_a_megujitas_a_token_elettartama_alatt_van(self):
        self.assertLess(beallitasok.SESSION_MAX_KOR, 2 * 60 * 60)


if __name__ == "__main__":
    unittest.main()
