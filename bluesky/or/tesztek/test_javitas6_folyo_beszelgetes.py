"""FIX 6 (2026-09-20) — two wrong cases of the INITIATED classification.

(A) Our own post, addressed to nobody, must not consume the daily initiated
    quota — only the daily five that applies to all posts.
(B) "Ongoing conversation": X replied to OUR post, then kept writing under
    their own reply in the same chain, without tagging us. A reply to that is
    the MIDDLE of the conversation, not an initiation.

Every network and Telegram call is mocked; there are no live calls.
"""

import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import elonezet  # noqa: E402
import kimeno  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402
import szures  # noqa: E402

MIENK_URI = "at://" + kozos_teszt.SAJAT_DID + "/app.bsky.feed.post/mienk01"
MIENK_CID = "bafymienkcid0001"
X_VALASZ_URI = "at://" + kozos_teszt.IDEGEN_DID + "/app.bsky.feed.post/xval01"
X_VALASZ_CID = "bafyxvalaszcid001"
X_FOLYT_URI = "at://" + kozos_teszt.IDEGEN_DID + "/app.bsky.feed.post/xfoly01"
X_FOLYT_CID = "bafyxfolytatascid1"
HARMADIK_DID = "did:plc:harmadikfel789"
HARMADIK_URI = "at://" + HARMADIK_DID + "/app.bsky.feed.post/harm01"
HARMADIK_CID = "bafyharmadikcid001"


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def halozati_poszt(uri, cid, szerzo_did, szoveg="szöveg", handle="valaki.test",
                   szulo=None, gyoker=None, kor_masodperc=0, most=None,
                   ido_van=True, mention_did=None):
    """One `getPosts` response item for the ancestor chain.

    `szulo`/`gyoker`: `(uri, cid)` pairs. With `ido_van=False` the post has
    NO network timestamp at all — in that case rule (B) cannot be satisfied.
    """
    most = time.time() if most is None else most
    rekord = {"text": szoveg, "facets": []}
    if mention_did:
        rekord["facets"] = [{"features": [
            {"$type": "app.bsky.richtext.facet#mention", "did": mention_did}]}]
    if szulo:
        rekord["reply"] = {"parent": {"uri": szulo[0], "cid": szulo[1]},
                           "root": {"uri": (gyoker or szulo)[0],
                                    "cid": (gyoker or szulo)[1]}}
    elem = {"uri": uri, "cid": cid,
            "author": {"did": szerzo_did, "handle": handle},
            "record": rekord}
    if ido_van:
        rekord["createdAt"] = _iso(most - kor_masodperc)
        elem["indexedAt"] = _iso(most - kor_masodperc)
    return elem


def folyo_szal(most, x_valasz_kor=3600, x_ido_van=True,
               kozbeekelt_szerzo=None):
    """The network picture of today's real case: us → X's reply → X's continuation.

    `kozbeekelt_szerzo`: if given, a third party's post is inserted BETWEEN
    X's continuation and X's reply to us (negative case for B).
    """
    mienk = halozati_poszt(MIENK_URI, MIENK_CID, kozos_teszt.SAJAT_DID,
                           "A mi posztunk.", handle=kozos_teszt.SAJAT_HANDLE,
                           kor_masodperc=x_valasz_kor + 600, most=most)
    x_valasz = halozati_poszt(X_VALASZ_URI, X_VALASZ_CID,
                              kozos_teszt.IDEGEN_DID, "Ez érdekes!",
                              handle="idegen.bsky.social",
                              szulo=(MIENK_URI, MIENK_CID),
                              kor_masodperc=x_valasz_kor, most=most,
                              ido_van=x_ido_van)
    posztok = {MIENK_URI: mienk, X_VALASZ_URI: x_valasz}
    szulo = (X_VALASZ_URI, X_VALASZ_CID)
    if kozbeekelt_szerzo:
        kozbe = halozati_poszt(HARMADIK_URI, HARMADIK_CID, kozbeekelt_szerzo,
                               "Közbeszólok.", handle="harmadik.bsky.social",
                               szulo=(X_VALASZ_URI, X_VALASZ_CID),
                               gyoker=(MIENK_URI, MIENK_CID),
                               kor_masodperc=60, most=most)
        posztok[HARMADIK_URI] = kozbe
        szulo = (HARMADIK_URI, HARMADIK_CID)
    folytatas = halozati_poszt(X_FOLYT_URI, X_FOLYT_CID,
                               kozos_teszt.IDEGEN_DID, "Tessék, egy kép.",
                               handle="idegen.bsky.social", szulo=szulo,
                               gyoker=(MIENK_URI, MIENK_CID),
                               kor_masodperc=30, most=most)
    posztok[X_FOLYT_URI] = folytatas
    return posztok


# =========================================================================
# (A) — our own post and the initiated ceiling
# =========================================================================

class SajatPosztPlafonTeszt(unittest.TestCase):
    """Our own post without `reply` is NOT initiated."""

    def setUp(self):
        self.most = time.time()

    def _sajat_poszt_feed(self, darab):
        elemek = []
        for i in range(darab):
            elemek.append({"post": {
                "uri": "at://%s/app.bsky.feed.post/p%d"
                       % (kozos_teszt.SAJAT_DID, i),
                "author": {"did": kozos_teszt.SAJAT_DID},
                "record": {"text": "sajat poszt %d" % i,
                           "createdAt": _iso(self.most - 3600 * (i + 2))}}})
        return kimeno.sajat_posztok_a_feedbol({"feed": elemek},
                                              kozos_teszt.SAJAT_DID)

    def test_sajat_poszt_nem_fogyasztja_a_kezdemenyezett_keretet(self):
        posztok = self._sajat_poszt_feed(4)
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "masik-kulcs",
                                        kezdemenyezett=True)
        self.assertNotIn("kezdemenyezett_napi_limit", okok)
        self.assertEqual(okok, [])

    def test_sajat_poszt_az_otos_plafont_viszont_fogyasztja(self):
        posztok = self._sajat_poszt_feed(5)
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "masik-kulcs",
                                        kezdemenyezett=True)
        self.assertIn("napi_limit", okok)

    def test_sajat_szalban_folytatott_poszt_sem_kezdemenyezett(self):
        """Our own reply written under our own post does not consume the quota either."""
        elem = {"post": {
            "uri": "at://%s/app.bsky.feed.post/p9" % kozos_teszt.SAJAT_DID,
            "author": {"did": kozos_teszt.SAJAT_DID},
            "record": {"text": "folytatom",
                       "createdAt": _iso(self.most - 7200),
                       "reply": {"parent": {"uri": MIENK_URI},
                                 "root": {"uri": MIENK_URI}}}}}
        posztok = kimeno.sajat_posztok_a_feedbol({"feed": [elem]},
                                                 kozos_teszt.SAJAT_DID)
        okok = kimeno.plafon_ellenorzes(posztok, self.most,
                                        kozos_teszt.SAJAT_DID, "masik-kulcs",
                                        kezdemenyezett=True)
        self.assertEqual(okok, [])


# =========================================================================
# (B) — the ongoing conversation
# =========================================================================

class FolyoBeszelgetesTeszt(unittest.TestCase):
    """Today's real case and the related negative cases."""

    def setUp(self):
        self.most = time.time()

    def _cel(self, posztok, engedelylista=None, cel_uri=X_FOLYT_URI):
        return kimeno.cel_ellenorzes(
            posztok.get(cel_uri), cel_uri, kozos_teszt.SAJAT_DID,
            engedelylista or [], posztok.get, self.most)

    def test_a_mai_eset_nem_kezdemenyezett(self):
        cel, okok = self._cel(folyo_szal(self.most))
        self.assertEqual(okok, [])
        self.assertFalse(cel["kezdemenyezett"])
        self.assertTrue(cel["folyo_beszelgetes"])
        self.assertTrue(cel["megszolitottak"])
        self.assertEqual(cel["folyo_horgony_uri"], X_VALASZ_URI)

    def test_engedelylista_nem_feltetele(self):
        """Works with an empty allowlist too: X spoke to us first."""
        cel, okok = self._cel(folyo_szal(self.most), engedelylista=[])
        self.assertEqual(okok, [])
        self.assertTrue(cel["folyo_beszelgetes"])

    def test_masik_szalban_nem_nyit_kaput(self):
        """X writes in a COMPLETELY different thread — the old behaviour stays."""
        posztok = folyo_szal(self.most)
        masik_uri = ("at://" + kozos_teszt.IDEGEN_DID
                     + "/app.bsky.feed.post/masik1")
        posztok[masik_uri] = halozati_poszt(
            masik_uri, "bafymasikszalcid01", kozos_teszt.IDEGEN_DID,
            "Egy másik szál.", handle="idegen.bsky.social",
            kor_masodperc=30, most=self.most)
        cel, okok = self._cel(posztok, cel_uri=masik_uri)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_huszonot_oras_valasz_mar_nem_folyo(self):
        posztok = folyo_szal(self.most, x_valasz_kor=25 * 3600)
        cel, okok = self._cel(posztok)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_hianyzo_ido_eseten_nem_folyo(self):
        posztok = folyo_szal(self.most, x_ido_van=False)
        cel, okok = self._cel(posztok)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_jovobeli_idobelyeg_nem_nyitja_meg(self):
        posztok = folyo_szal(self.most, x_valasz_kor=-7200)
        cel, okok = self._cel(posztok)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_harmadik_fel_a_lancban_nem_folyo(self):
        posztok = folyo_szal(self.most, kozbeekelt_szerzo=HARMADIK_DID)
        cel, okok = self._cel(posztok)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_engedelylistas_harmadik_fel_eseten_a_regi_viselkedes_marad(self):
        """Allowlisted interposed party: INITIATED, not ongoing."""
        posztok = folyo_szal(self.most, kozbeekelt_szerzo=HARMADIK_DID)
        cel, okok = self._cel(posztok,
                              engedelylista=[{"did": kozos_teszt.IDEGEN_DID},
                                             {"did": HARMADIK_DID}])
        self.assertEqual(okok, [])
        self.assertTrue(cel["kezdemenyezett"])
        self.assertFalse(cel["folyo_beszelgetes"])

    def test_a_kozbenso_poszt_nem_x_e_akkor_nem_folyo(self):
        """The target post's author is NOT the one who replied to us."""
        posztok = folyo_szal(self.most)
        mas_did = "did:plc:masikfiok999"
        mas_uri = "at://" + mas_did + "/app.bsky.feed.post/mas01"
        posztok[mas_uri] = halozati_poszt(
            mas_uri, "bafymasikfiokcid01", mas_did, "Beszólok a szálba.",
            handle="masik.bsky.social", szulo=(X_VALASZ_URI, X_VALASZ_CID),
            gyoker=(MIENK_URI, MIENK_CID), kor_masodperc=30, most=self.most)
        cel, okok = self._cel(posztok, cel_uri=mas_uri)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_x_a_sajat_szalat_folytatja_nem_folyo(self):
        """The top of the chain is X's post: we did not start the conversation."""
        gyoker_uri = ("at://" + kozos_teszt.IDEGEN_DID
                      + "/app.bsky.feed.post/xgyok1")
        gyoker = halozati_poszt(gyoker_uri, "bafyxgyokercid001",
                                kozos_teszt.IDEGEN_DID, "X saját szála.",
                                handle="idegen.bsky.social",
                                kor_masodperc=7200, most=self.most)
        folytatas = halozati_poszt(
            X_FOLYT_URI, X_FOLYT_CID, kozos_teszt.IDEGEN_DID, "Folytatom.",
            handle="idegen.bsky.social", szulo=(gyoker_uri, "bafyxgyokercid001"),
            kor_masodperc=30, most=self.most)
        posztok = {gyoker_uri: gyoker, X_FOLYT_URI: folytatas}
        cel, okok = self._cel(posztok)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_torolt_valasz_eseten_a_lanc_nem_jarhato_be(self):
        posztok = folyo_szal(self.most)
        del posztok[X_VALASZ_URI]
        cel, okok = self._cel(posztok)
        self.assertIsNone(cel)
        self.assertEqual(okok, ["halozati_nem_szolitottak_meg"])

    def test_a_lanc_lekerese_nem_duplazodik(self):
        """The (B) check and the chain check use the SAME traversal."""
        posztok = folyo_szal(self.most)
        hivasok = []

        def lekero(uri):
            hivasok.append(uri)
            return posztok.get(uri)

        cel, okok = kimeno.cel_ellenorzes(
            posztok[X_FOLYT_URI], X_FOLYT_URI, kozos_teszt.SAJAT_DID, [],
            lekero, self.most)
        self.assertEqual(okok, [])
        self.assertTrue(cel["folyo_beszelgetes"])
        # ancestors: X's reply + our post; and the separate thread root (our
        # post) — the chain is NOT traversed a SECOND time.
        self.assertEqual(hivasok.count(X_VALASZ_URI), 1)
        self.assertLessEqual(hivasok.count(MIENK_URI), 2)


class FolyoBeszelgetesElonezetTeszt(unittest.TestCase):
    """For an ongoing conversation the preview writes a TRUE, short line, not INITIATED."""

    def setUp(self):
        self.most = time.time()
        self.posztok = folyo_szal(self.most)

    def test_elonezet_sora(self):
        cel, okok = kimeno.cel_ellenorzes(
            self.posztok[X_FOLYT_URI], X_FOLYT_URI, kozos_teszt.SAJAT_DID, [],
            self.posztok.get, self.most)
        self.assertEqual(okok, [])
        kanonikus = szures.kimeno_kanonikus("valasz", "Szép kép!", cel,
                                            ["hu"], False)
        uzenet = elonezet.szoveg(kanonikus, "jelolo", cel=cel)
        self.assertIn("ONGOING CONVERSATION:", uzenet)
        self.assertIn("idegen.bsky.social", uzenet)
        self.assertNotIn("UNSOLICITED", uzenet)


class FolyoBeszelgetesMenetTeszt(unittest.TestCase):
    """End to end: draft → preview → approval → sending."""

    def setUp(self):
        self.most = time.time()
        self.k = kozos_teszt.kornyezet_keszit()
        self.halo = kozos_teszt.MockHalo()
        self.halo.posztok = folyo_szal(self.most)
        self.bot = kozos_teszt.MockBot()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def _piszkozat(self, cel_uri=X_FOLYT_URI, szoveg="Szép kép!"):
        kozos_teszt.piszkozat_ir(self.k, "v.json", kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=cel_uri, szoveg=szoveg))

    def test_elonezet_folyo_beszelgetes_sorral_megy_ki(self):
        self._piszkozat()
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(len(self.bot.kuldott), 1)
        uzenet = self.bot.kuldott[0]["szoveg"]
        self.assertIn("ONGOING CONVERSATION:", uzenet)
        self.assertNotIn("UNSOLICITED", uzenet)

    def test_jovahagyas_utan_kimegy(self):
        self._piszkozat()
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.bot.igen(kozos_teszt.utolso_elonezet_id(self.bot))
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(len(self.halo.letrehozasok), 1)
        kanonikus = self.halo.letrehozasok[0]["kanonikus"]
        self.assertFalse(kanonikus["kezdemenyezett"])

    def test_x_torli_a_valaszat_az_elonezet_utan_megallunk(self):
        self._piszkozat()
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.bot.igen(kozos_teszt.utolso_elonezet_id(self.bot))
        # X deletes their reply to us: the chain can no longer be traversed.
        del self.halo.posztok[X_VALASZ_URI]
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(self.halo.letrehozasok, [])
        with open(self.k.allapot_kifele, "r", encoding="utf-8") as f:
            kifele = json.load(f)
        szoveg = json.dumps(kifele, ensure_ascii=False)
        self.assertIn("cannot go out after all", szoveg)

    def test_hamis_uri_a_piszkozatban_elutasitas(self):
        """Evasion: the draft points to a non-existent target."""
        self._piszkozat(cel_uri="at://did:plc:hamis/app.bsky.feed.post/nincs1")
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(self.bot.kuldott, [])
        self.assertEqual(self.halo.letrehozasok, [])

    def test_a_piszkozat_nem_allithat_folyo_beszelgetest(self):
        """The draft has no such field; the classification comes only from the network."""
        posztok = folyo_szal(self.most, x_valasz_kor=25 * 3600)
        self.halo.posztok = posztok
        self._piszkozat()
        motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)
        self.assertEqual(self.bot.kuldott, [])


if __name__ == "__main__":
    unittest.main()
