"""Deciding on APPROVAL. This is the heart of the system: if any test fails,
someone other than the owner could approve a post."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import telegram_or  # noqa: E402

USER = 111
CHAT = 222
ELONEZET = 9001


def uzenet(**felulir):
    alap = {
        "message_id": 5001,
        "from": {"id": USER, "is_bot": False},
        "chat": {"id": CHAT, "type": "private"},
        "text": "IGEN",
        "reply_to_message": {"message_id": ELONEZET},
    }
    alap.update(felulir)
    return {"update_id": 1, "message": alap}


def dontes(frissites):
    return telegram_or.dontes(frissites, USER, CHAT)[0]


class ErvenyesTeszt(unittest.TestCase):

    def test_ok_igen(self):
        self.assertEqual(dontes(uzenet()), telegram_or.JOVAHAGY)

    def test_ok_kisbetus_igen(self):
        self.assertEqual(dontes(uzenet(text="igen")), telegram_or.JOVAHAGY)

    def test_ok_szokozos_igen(self):
        self.assertEqual(dontes(uzenet(text="  IGEN \n")),
                         telegram_or.JOVAHAGY)

    def test_ok_nem(self):
        self.assertEqual(dontes(uzenet(text="NEM")), telegram_or.ELVET)

    def test_ok_a_valaszolt_message_id_visszajon(self):
        _, valasz_id, sajat_id = telegram_or.dontes(uzenet(), USER, CHAT)
        self.assertEqual(valasz_id, ELONEZET)
        self.assertEqual(sajat_id, 5001)


class HamisJovahagyasTeszt(unittest.TestCase):
    """Convention: if a test FAILS, the attack WORKS."""

    def kiv(self, frissites, mit):
        self.assertEqual(dontes(frissites), telegram_or.FIGYELMEN_KIVUL, mit)

    def test_mas_user(self):
        self.kiv(uzenet(**{"from": {"id": 999, "is_bot": False}}), "other user")

    def test_hianyzo_user(self):
        self.kiv(uzenet(**{"from": {}}), "without user_id")

    def test_bot_kuldte(self):
        self.kiv(uzenet(**{"from": {"id": USER, "is_bot": True}}), "bot")

    def test_mas_chat(self):
        self.kiv(uzenet(chat={"id": 333, "type": "private"}), "other chat")

    def test_csoport_chat(self):
        self.kiv(uzenet(chat={"id": CHAT, "type": "group"}), "group")

    def test_csatorna(self):
        self.kiv({"update_id": 1, "channel_post": uzenet()["message"]},
                 "channel post")

    def test_nem_reply(self):
        m = uzenet()["message"]
        del m["reply_to_message"]
        self.kiv({"update_id": 1, "message": m}, "not a reply")

    def test_masik_elonezetre_adott_reply(self):
        """This is a VALID decision, but for ANOTHER preview — the engine links them."""
        _, valasz_id, _ = telegram_or.dontes(
            uzenet(reply_to_message={"message_id": 7777}), USER, CHAT)
        self.assertNotEqual(valasz_id, ELONEZET)

    def test_szerkesztett(self):
        self.kiv(uzenet(edit_date=123), "edited")

    def test_edited_message_nem_szamit(self):
        self.kiv({"update_id": 1, "edited_message": uzenet()["message"]},
                 "edited_message")

    def test_tovabbitott(self):
        for mezo in ("forward_origin", "forward_from", "forward_date",
                     "forward_sender_name", "forward_from_chat",
                     "is_automatic_forward"):
            self.kiv(uzenet(**{mezo: {"x": 1}}), "forwarded: " + mezo)

    def test_via_bot(self):
        self.kiv(uzenet(via_bot={"id": 5}), "via_bot")

    def test_sender_chat(self):
        self.kiv(uzenet(sender_chat={"id": 5}), "sender_chat (anonymous admin)")

    def test_igen_felkialtojellel(self):
        self.kiv(uzenet(text="igen!"), "\"igen!\"")

    def test_igen_kod(self):
        self.kiv(uzenet(text="IGEN 123"), "\"IGEN 123\"")

    def test_mondatba_agyazott_igen(self):
        self.kiv(uzenet(text="szerintem igen, mehet"), "inside a sentence")

    def test_idezett_igen(self):
        self.kiv(uzenet(text="\"IGEN\""), "in quotation marks")

    def test_homograf_igen(self):
        self.kiv(uzenet(text="ИГЕН"), "Cyrillic homograph")
        self.kiv(uzenet(text="ＩＧＥＮ"), "full-width")

    def test_nem_szoveges_uzenet(self):
        m = uzenet()["message"]
        del m["text"]
        m["photo"] = [{"file_id": "x"}]
        self.kiv({"update_id": 1, "message": m}, "image")

    def test_ures_frissites(self):
        self.kiv({}, "empty")
        self.kiv({"update_id": 1}, "without message")


class LejaratTeszt(unittest.TestCase):

    def test_48_ora_utan_lejar(self):
        self.assertTrue(telegram_or.lejart_e(1000, 1000 + 48 * 3600 + 1))

    def test_48_oran_belul_ervenyes(self):
        self.assertFalse(telegram_or.lejart_e(1000, 1000 + 47 * 3600))

    def test_hianyzo_idobelyeg_lejart(self):
        self.assertTrue(telegram_or.lejart_e(None, 1000))
        self.assertTrue(telegram_or.lejart_e("tegnap", 1000))


class OffsetTeszt(unittest.TestCase):

    def test_negativ_offset_tilos(self):
        """`getUpdates offset=-1` would discard the unprocessed messages."""
        with self.assertRaises(telegram_or.TelegramHiba):
            telegram_or.frissitesek("nemletezo-token", -1)


if __name__ == "__main__":
    unittest.main()
