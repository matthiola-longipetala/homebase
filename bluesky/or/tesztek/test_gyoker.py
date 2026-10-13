"""Verifying the THREAD ROOT (K''1) — the main attack of the third review.

The attack step by step:
  1. the attacker writes a post that addresses us with a mention facet
     (entirely legitimate, anyone can do it);
  2. in the record of that same post they write that it is part of a thread
     whose root is an arbitrary THIRD PARTY's post — with a real uri and a
     real cid, since these are public data;
  3. if we took the root over blindly, our reply would land in that third
     party's thread, and they would get a notification. The owner would approve
     something they cannot even see.

Convention: if a test FAILS, the attack WORKS."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import allapot  # noqa: E402
import beallitasok  # noqa: E402
import kimeno  # noqa: E402
import kozos_teszt  # noqa: E402
import motor  # noqa: E402


class GyokerAlap(unittest.TestCase):

    def setUp(self):
        self.k = kozos_teszt.kornyezet_keszit()
        self.halo = kozos_teszt.MockHalo()
        self.bot = kozos_teszt.MockBot()

    def tearDown(self):
        kozos_teszt.kornyezet_torol(self.k)

    def _tamado_poszt(self, gyoker_uri=kozos_teszt.GYOKER_URI,
                      gyoker_cid=kozos_teszt.GYOKER_CID):
        """A post addressing us, referring to a FOREIGN thread root."""
        return kozos_teszt.cel_poszt(gyoker_uri=gyoker_uri,
                                     gyoker_cid=gyoker_cid)

    def _valasz_piszkozat(self, nev="v.json", szoveg="Köszönöm a kérdést!"):
        kozos_teszt.piszkozat_ir(self.k, nev, kozos_teszt.alap_piszkozat(
            tipus="valasz", cel_uri=kozos_teszt.CEL_URI, szoveg=szoveg))

    def _kor(self):
        return motor.egy_kor(self.k, self.halo, self.bot, bejovo_is=False)

    def rekordok(self):
        return [r for r in allapot.betolt_mind(self.k) if not r.get("_serult")]


class AtnezoTamadasaTeszt(GyokerAlap):
    """An exact replay of section 1 of ATNEZES3's "bizonyitas.py"."""

    def test_idegen_gyoker_kezdemenyezett_es_nagybetus_figyelmeztetes(self):
        """The root exists, its cid is real — it just belongs to a third party.

        M'''1: this is NO LONGER a rejection. On Bluesky it is an everyday
        case that someone addresses us in a third party's thread; the old,
        rejecting rule was at once too strict for the honest case and — because
        of the mirror-image attack — too lenient for the lying one. Today it is
        classified as INITIATED (tighter daily ceiling), and the preview names
        in CAPITALS whose thread the reply will land in.
        """
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(),
            kozos_teszt.GYOKER_URI: kozos_teszt.gyoker_poszt(),
        }
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(len(self.bot.kuldott), 1,
                         "no preview went out for the legitimate mention")
        uzenet = self.bot.kuldott[-1]["szoveg"]
        self.assertIn("A FOREIGN THIRD PARTY ALSO TAKES PART IN THIS THREAD",
                      uzenet)
        self.assertIn("MAY ALSO BE NOTIFIED OF IT", uzenet)
        self.assertIn("harmadik.bsky.social", uzenet)
        self.assertIn("did:plc:harmadikfel789", uzenet)
        self.assertIn("idegen beszélgetés gyökere", uzenet)
        # so this is their conversation with us — NOT initiated, it falls
        # under the ceiling of five. The FOREIGN warning (above) still goes out.
        self.assertNotIn("UNSOLICITED", uzenet)
        self.assertFalse(self.rekordok()[0]["kanonikus"]["kezdemenyezett"])

    def test_nem_letezo_gyoker_elutasitva(self):
        self.halo.posztok = {kozos_teszt.CEL_URI: self._tamado_poszt()}
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(self.bot.kuldott, [])

    def test_hamis_gyoker_cid_elutasitva(self):
        """The root exists and its author would be fine, but the cid written
        into the record does not match the one on the network."""
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(
                gyoker_cid="bafyhamiscid00000"),
            kozos_teszt.GYOKER_URI: kozos_teszt.gyoker_poszt(
                szerzo_did=kozos_teszt.IDEGEN_DID),
        }
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(self.bot.kuldott, [])

    def test_ertelmezhetetlen_gyoker_alak_elutasitva(self):
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(
                gyoker_uri="https://nem-at-uri.invalid/x")}
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(self.bot.kuldott, [])


class EngedettGyokerTeszt(GyokerAlap):
    """When the root is FINE, the reply must go through."""

    def test_gyoker_a_celposzt_maga(self):
        """No thread: the target post is the root. In the outgoing record root = parent."""
        self.halo.posztok = {kozos_teszt.CEL_URI: kozos_teszt.cel_poszt()}
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(len(self.bot.kuldott), 1)
        cel = self.rekordok()[0]["kanonikus"]["valasz_cel"]
        self.assertEqual(cel["root_uri"], kozos_teszt.CEL_URI)
        self.assertEqual(cel["root_cid"], kozos_teszt.CEL_CID)

    def test_a_szulo_szerzojenek_sajat_szala_engedett(self):
        """The root belongs to the PARENT's author: it is their conversation, we may write into it."""
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(),
            kozos_teszt.GYOKER_URI: kozos_teszt.gyoker_poszt(
                szerzo_did=kozos_teszt.IDEGEN_DID),
        }
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(len(self.bot.kuldott), 1)
        cel = self.rekordok()[0]["kanonikus"]["valasz_cel"]
        self.assertEqual(cel["root_uri"], kozos_teszt.GYOKER_URI)

    def test_a_sajat_szalunk_engedett(self):
        sajat_gyoker = "at://%s/app.bsky.feed.post/sajatgyoker" \
            % kozos_teszt.SAJAT_DID
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(gyoker_uri=sajat_gyoker),
            sajat_gyoker: kozos_teszt.gyoker_poszt(
                uri=sajat_gyoker, szerzo_did=kozos_teszt.SAJAT_DID),
        }
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(len(self.bot.kuldott), 1)


class ElonezetGyokerTeszt(GyokerAlap):
    """The preview MUST SHOW the root (K''1, recommendation 3)."""

    def test_az_elonezet_kiirja_a_kulon_gyokeret(self):
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(),
            kozos_teszt.GYOKER_URI: kozos_teszt.gyoker_poszt(
                szerzo_did=kozos_teszt.IDEGEN_DID),
        }
        self._valasz_piszkozat()
        self._kor()
        uzenet = self.bot.kuldott[-1]["szoveg"]
        self.assertIn("THE POST GOES INTO THIS THREAD", uzenet)
        self.assertIn(kozos_teszt.GYOKER_URI, uzenet)
        self.assertIn("harmadik.bsky.social", uzenet)
        self.assertIn("idegen beszélgetés gyökere", uzenet)

    def test_az_elonezet_akkor_is_ir_a_szalrol_ha_a_gyoker_a_celposzt(self):
        self.halo.posztok = {kozos_teszt.CEL_URI: kozos_teszt.cel_poszt()}
        self._valasz_piszkozat()
        self._kor()
        uzenet = self.bot.kuldott[-1]["szoveg"]
        self.assertIn("THE POST GOES INTO THIS THREAD", uzenet)
        self.assertIn("the thread root is the target post above itself", uzenet)


class UjraellenorzesGyokerTeszt(GyokerAlap):
    """AFTER approval the root cannot be swapped either."""

    def test_gyoker_csereje_jovahagyas_utan_megallit(self):
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(),
            kozos_teszt.GYOKER_URI: kozos_teszt.gyoker_poszt(
                szerzo_did=kozos_teszt.IDEGEN_DID),
        }
        self._valasz_piszkozat()
        self._kor()
        elonezet_id = self.bot.kuldott[-1]["message_id"]

        # Between approval and sending, the attacker rewrites their own post:
        # from now on it points to ANOTHER thread, also a real one.
        masik_gyoker = "at://did:plc:negyedikfel/app.bsky.feed.post/masik01"
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(
                gyoker_uri=masik_gyoker, gyoker_cid="bafymasikcid0001"),
            masik_gyoker: kozos_teszt.gyoker_poszt(
                uri=masik_gyoker, cid="bafymasikcid0001",
                szerzo_did=kozos_teszt.IDEGEN_DID),
        }
        self.bot.igen(elonezet_id)
        self._kor()
        self.assertEqual(self.halo.letrehozasok, [],
                         "the root could be swapped after approval")

    def test_engedett_gyokerrel_kimegy_a_helyes_reply_blokk(self):
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(),
            kozos_teszt.GYOKER_URI: kozos_teszt.gyoker_poszt(
                szerzo_did=kozos_teszt.IDEGEN_DID),
        }
        self._valasz_piszkozat()
        self._kor()
        self.bot.igen(self.bot.kuldott[-1]["message_id"])
        self._kor()
        self.assertEqual(len(self.halo.letrehozasok), 1)
        cel = self.halo.letrehozasok[0]["kanonikus"]["valasz_cel"]
        self.assertEqual(cel["root_uri"], kozos_teszt.GYOKER_URI)
        self.assertEqual(cel["root_cid"], kozos_teszt.GYOKER_CID)
        self.assertEqual(cel["parent_uri"], kozos_teszt.CEL_URI)


ALDOZAT_DID = "did:plc:aldozat999"
ALDOZAT_URI = "at://did:plc:aldozat999/app.bsky.feed.post/aldozatposzt"
ALDOZAT_CID = "bafyaldozatcid001"
TAMADO_GYOKER_URI = "at://did:plc:idegenfiok456/app.bsky.feed.post/tamadoroot"
TAMADO_GYOKER_CID = "bafytamadorootcid1"


class Atnezes4TamadasTeszt(GyokerAlap):
    """The two attacks of the FOURTH review. If they fail, the attack works."""

    def test_igaz_parent_hazug_root_az_aldozat_szalaba_vinne(self):
        """K'''2 — the mirror-image attack.

        The attacker replies to a VICTIM's post (so `reply.parent` is TRUE),
        and in that reply addresses us with a mention facet. In their own
        record, however, they set the `reply.root` field to their OWN harmless
        earlier post. Previously the guard verified only the claimed root,
        never the target post's OWN ancestor — so the post would have landed
        in the victim's conversation without the preview even mentioning the
        victim.
        """
        self.halo.posztok = {
            kozos_teszt.CEL_URI: kozos_teszt.cel_poszt(
                szulo_uri=ALDOZAT_URI, szulo_cid=ALDOZAT_CID,
                gyoker_uri=TAMADO_GYOKER_URI, gyoker_cid=TAMADO_GYOKER_CID),
            TAMADO_GYOKER_URI: kozos_teszt.gyoker_poszt(
                uri=TAMADO_GYOKER_URI, cid=TAMADO_GYOKER_CID,
                szerzo_did=kozos_teszt.IDEGEN_DID,
                szoveg="A támadó saját, ártalmatlan posztja."),
            ALDOZAT_URI: kozos_teszt.gyoker_poszt(
                uri=ALDOZAT_URI, cid=ALDOZAT_CID, szerzo_did=ALDOZAT_DID,
                szoveg="Az áldozat beszélgetésének a nyitó posztja."),
        }
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(len(self.bot.kuldott), 1)
        uzenet = self.bot.kuldott[-1]["szoveg"]
        # The PREVIEW must not hide the victim.
        self.assertIn(ALDOZAT_DID, uzenet,
                      "the preview does not mention the victim")
        self.assertIn("A FOREIGN THIRD PARTY ALSO TAKES PART IN THIS THREAD",
                      uzenet)
        self.assertIn("THE ACTUAL TOP OF THE THREAD IS NOT THE SAME", uzenet)
        # J7: the attacker addressed us (mention) → not initiated;
        # the victim and the foreign party are still visible BY NAME in the preview.
        self.assertNotIn("UNSOLICITED", uzenet)
        self.assertFalse(self.rekordok()[0]["kanonikus"]["kezdemenyezett"])

    def test_atnezes3_tamadasa_tovabbra_is_lathatova_valik(self):
        """The attack of the THIRD review: a lying `root`, the parent is the
        only source of the root lie. Today it is not a rejection but INITIATED
        — but the preview names the third party, so the owner sees what we would be
        joining."""
        self.halo.posztok = {
            kozos_teszt.CEL_URI: self._tamado_poszt(),
            kozos_teszt.GYOKER_URI: kozos_teszt.gyoker_poszt(),
        }
        self._valasz_piszkozat()
        self._kor()
        uzenet = self.bot.kuldott[-1]["szoveg"]
        self.assertIn("did:plc:harmadikfel789", uzenet)
        self.assertIn("A FOREIGN THIRD PARTY", uzenet)

    def test_hetkoznapi_eset_mas_szalaban_szolitanak_meg(self):
        """M'''1 — the honest case: someone addresses us in a reply inside a
        third party's thread, and tells the TRUTH about the root.

        Previously this was rejected PERMANENTLY with reason
        `gyoker_szerzoje_idegen`: we replied to whoever lied, but not to whoever
        told the truth. Today the preview goes out, classified as initiated."""
        self.halo.posztok = {
            kozos_teszt.CEL_URI: kozos_teszt.cel_poszt(
                szulo_uri=ALDOZAT_URI, szulo_cid=ALDOZAT_CID,
                gyoker_uri=ALDOZAT_URI, gyoker_cid=ALDOZAT_CID),
            ALDOZAT_URI: kozos_teszt.gyoker_poszt(
                uri=ALDOZAT_URI, cid=ALDOZAT_CID, szerzo_did=ALDOZAT_DID,
                szoveg="Egy harmadik fél beszélgetésének a nyitó posztja."),
        }
        self._valasz_piszkozat()
        self._kor()
        self.assertEqual(len(self.bot.kuldott), 1,
                         "the legitimate mention was rejected")
        uzenet = self.bot.kuldott[-1]["szoveg"]
        self.assertIn("A FOREIGN THIRD PARTY", uzenet)
        self.assertIn(ALDOZAT_DID, uzenet)
        # Here the claimed root is TRUE, so there is no mismatch warning.
        self.assertNotIn("THE ACTUAL TOP OF THE THREAD IS NOT THE SAME", uzenet)

    def test_a_lanc_valtozasa_a_jovahagyas_utan_megallit(self):
        self.halo.posztok = {
            kozos_teszt.CEL_URI: kozos_teszt.cel_poszt(
                szulo_uri=ALDOZAT_URI, szulo_cid=ALDOZAT_CID,
                gyoker_uri=ALDOZAT_URI, gyoker_cid=ALDOZAT_CID),
            ALDOZAT_URI: kozos_teszt.gyoker_poszt(
                uri=ALDOZAT_URI, cid=ALDOZAT_CID, szerzo_did=ALDOZAT_DID),
        }
        self._valasz_piszkozat()
        self._kor()
        elonezet_id = self.bot.kuldott[-1]["message_id"]
        # After approval the attacker points their own post at ANOTHER
        # ancestor, also a real one.
        masik = "at://did:plc:otodikfel/app.bsky.feed.post/masikszal"
        self.halo.posztok[kozos_teszt.CEL_URI] = kozos_teszt.cel_poszt(
            szulo_uri=masik, szulo_cid=ALDOZAT_CID,
            gyoker_uri=ALDOZAT_URI, gyoker_cid=ALDOZAT_CID)
        self.halo.posztok[masik] = kozos_teszt.gyoker_poszt(
            uri=masik, cid=ALDOZAT_CID, szerzo_did="did:plc:otodikfel")
        self.bot.igen(elonezet_id)
        self._kor()
        self.assertEqual(self.halo.letrehozasok, [],
                         "the ancestor chain could be swapped after approval")

    def test_handle_csere_a_jovahagyas_utan_megallit(self):
        """Kö'''3 — the owner recognises from the handle whom we are replying to."""
        self.halo.posztok = {
            kozos_teszt.CEL_URI: kozos_teszt.cel_poszt(
                handle="kedves-barat.bsky.social")}
        self._valasz_piszkozat()
        self._kor()
        elonezet_id = self.bot.kuldott[-1]["message_id"]
        self.assertIn("kedves-barat.bsky.social",
                      self.bot.kuldott[-1]["szoveg"])
        self.halo.posztok[kozos_teszt.CEL_URI] = kozos_teszt.cel_poszt(
            handle="valaki-mas.bsky.social")
        self.bot.igen(elonezet_id)
        self._kor()
        self.assertEqual(self.halo.letrehozasok, [],
                         "the handle could be swapped after approval")
        self.assertTrue(any("handle" in u["szoveg"]
                            for u in self.bot.kuldott[1:]),
                        "the owner was not notified of the handle change")

    def test_bejarhatatlan_lanc_elutasitas(self):
        """If the ancestor cannot be fetched, it is NOT initiated but rejected."""
        self.halo.posztok = {
            kozos_teszt.CEL_URI: kozos_teszt.cel_poszt(
                szulo_uri=ALDOZAT_URI, szulo_cid=ALDOZAT_CID,
                gyoker_uri=ALDOZAT_URI, gyoker_cid=ALDOZAT_CID)}
        self._valasz_piszkozat()
        adat = self._kor()
        self.assertEqual(self.bot.kuldott, [])
        okok = [o for t in adat["tetelek"] for o in (t.get("ok") or [])]
        self.assertTrue(any("gyoker_nem_igazolhato" in o for o in okok), okok)

    def test_tul_hosszu_lanc_elutasitas(self):
        """Running out of the upper limit is FAIL-CLOSED: nothing goes out."""
        # We build a self-referencing chain that never reaches its top.
        posztok = {}
        elozo = None
        for i in range(beallitasok.FELMENO_MAX_LEPES + 3, -1, -1):
            uri = "at://did:plc:lanc/app.bsky.feed.post/l%d" % i
            posztok[uri] = kozos_teszt.cel_poszt(
                uri=uri, cid="bafylanccid%03d" % i, mention_did=None,
                szerzo_did="did:plc:lanc",
                szulo_uri=elozo, szulo_cid="bafylanccid999",
                gyoker_uri=elozo, gyoker_cid="bafylanccid999")
            elozo = uri
        # The topmost element is the target post: it addresses us.
        posztok[kozos_teszt.CEL_URI] = kozos_teszt.cel_poszt(
            szulo_uri=elozo, szulo_cid="bafylanccid999",
            gyoker_uri=elozo, gyoker_cid="bafylanccid999")
        posztok[elozo] = dict(posztok[elozo])
        self.halo.posztok = posztok
        self._valasz_piszkozat()
        adat = self._kor()
        self.assertEqual(self.bot.kuldott, [])
        okok = [o for t in adat["tetelek"] for o in (t.get("ok") or [])]
        self.assertTrue(okok)


class TisztaLancFuggvenyTeszt(unittest.TestCase):
    """`felmeno_lanc` and `gyoker_es_lanc_ellenorzes` without a network."""

    def test_nincs_szal_a_lanc_egy_elemu(self):
        lanc, okok = kimeno.felmeno_lanc(kozos_teszt.cel_poszt(),
                                         lambda _u: None)
        self.assertEqual(okok, [])
        self.assertEqual(len(lanc), 1)
        self.assertEqual(lanc[0]["uri"], kozos_teszt.CEL_URI)

    def test_nem_letezo_felmeno_elutasitas(self):
        _lanc, okok = kimeno.felmeno_lanc(
            kozos_teszt.cel_poszt(szulo_uri=ALDOZAT_URI,
                                  szulo_cid=ALDOZAT_CID),
            lambda _u: None)
        self.assertEqual(okok, ["felmeno_nem_igazolhato"])

    def test_korben_jaro_lanc_elutasitas(self):
        onmaga = kozos_teszt.cel_poszt(szulo_uri=kozos_teszt.CEL_URI,
                                       szulo_cid=kozos_teszt.CEL_CID)
        _lanc, okok = kimeno.felmeno_lanc(onmaga, lambda _u: onmaga)
        self.assertEqual(okok, ["felmeno_lanc_korben_jar"])

    def test_rossz_alaku_felmeno_elutasitas(self):
        rossz = kozos_teszt.cel_poszt(szulo_uri="https://nem-at-uri.invalid/x",
                                      szulo_cid="x")
        _lanc, okok = kimeno.felmeno_lanc(rossz, lambda _u: None)
        self.assertEqual(okok, ["felmeno_alakja_rossz"])

    def test_engedelylistas_harmadik_fel_nem_idegen(self):
        """The thread of an allowlisted account does not count as foreign."""
        cel = {
            "uri": kozos_teszt.CEL_URI, "cid": kozos_teszt.CEL_CID,
            "szerzo_did": kozos_teszt.IDEGEN_DID,
            "szerzo_handle": "idegen.bsky.social",
        }
        szulo = kozos_teszt.cel_poszt(gyoker_uri=kozos_teszt.GYOKER_URI,
                                      gyoker_cid=kozos_teszt.GYOKER_CID)
        okok = kimeno.gyoker_es_lanc_ellenorzes(
            cel, szulo, kozos_teszt.SAJAT_DID,
            [{"did": "did:plc:harmadikfel789"}],
            lambda _u: kozos_teszt.gyoker_poszt())
        self.assertEqual(okok, [])
        self.assertFalse(cel["idegen_szalban"])
        self.assertFalse(cel.get("kezdemenyezett"))

    def test_a_gyoker_lekerese_meg_sem_tortenik_ha_a_gyoker_a_szulo(self):
        hivasok = []

        def lekero(uri):
            hivasok.append(uri)
            return None

        cel = {
            "uri": kozos_teszt.CEL_URI, "cid": kozos_teszt.CEL_CID,
            "szerzo_did": kozos_teszt.IDEGEN_DID,
            "szerzo_handle": "idegen.bsky.social",
        }
        okok = kimeno.gyoker_es_lanc_ellenorzes(
            cel, kozos_teszt.cel_poszt(), kozos_teszt.SAJAT_DID, [], lekero)
        self.assertEqual(okok, [])
        self.assertEqual(hivasok, [])
        self.assertEqual(cel["gyoker_uri"], kozos_teszt.CEL_URI)
        self.assertFalse(cel["gyoker_kulon"])


if __name__ == "__main__":
    unittest.main()
