"""kozos_teszt.py — shared helpers for the tests.

The tests NEVER touch the network, Telegram, or the live
`C:\\BskyGuard\\` tree: everything runs in a temporary folder, every external
call is mocked.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import beallitasok  # noqa: E402

SAJAT_DID = "did:plc:sajatfiok123"
SAJAT_HANDLE = "test-bot.bsky.social"
IDEGEN_DID = "did:plc:idegenfiok456"
CEL_URI = "at://did:plc:idegenfiok456/app.bsky.feed.post/abc123"
CEL_CID = "bafyceltestcid001"


def kornyezet_keszit():
    """A complete, EMPTY guard tree in a temporary folder."""
    gyoker = tempfile.mkdtemp(prefix="or_teszt_")
    k = beallitasok.Kornyezet(gyoker)
    for mappa in (k.titok, k.program, k.allapot, k.kozos, k.be, k.ki,
                  k.elonezetek, k.feldolgozott, k.varo, k.archiv, k.zart):
        os.makedirs(mappa, exist_ok=True)
    return k


def kornyezet_torol(kornyezet):
    shutil.rmtree(kornyezet.gyoker, ignore_errors=True)


def piszkozat_ir(kornyezet, nev, adat):
    utvonal = os.path.join(kornyezet.ki, nev)
    with open(utvonal, "w", encoding="utf-8") as f:
        json.dump(adat, f, ensure_ascii=False)
    return utvonal


def alap_piszkozat(szoveg="Jó reggelt mindenkinek.", tipus="poszt",
                   cel_uri=None, megjegyzes="proba"):
    return {
        "verzio": 1,
        "tipus": tipus,
        "szoveg": szoveg,
        "valasz_cel_uri": cel_uri,
        "langs": ["hu"],
        "megjegyzes_tulajnak": megjegyzes,
    }


GYOKER_URI = "at://did:plc:harmadikfel789/app.bsky.feed.post/gyoker01"
GYOKER_CID = "bafygyokertestcid01"


def cel_poszt(uri=CEL_URI, cid=CEL_CID, mention_did=SAJAT_DID,
              szerzo_did=IDEGEN_DID, szoveg="Szia, mit gondolsz?",
              gyoker_uri=None, gyoker_cid=None, szulo_uri=None,
              szulo_cid=None, handle="idegen.bsky.social"):
    """One `getPosts` response item: by default a post that MENTIONS us.

    `gyoker_uri`/`gyoker_cid`: the THREAD ROOT written into the record
    (`reply.root`). An attacker can set this to anything in their own post —
    the core of finding K''1.
    `szulo_uri`/`szulo_cid`: the `reply.parent` written into the record. The
    mirror image of finding K'''2 is built from this: the `parent` is REAL (a
    victim's post), while the `root` points to the attacker's own harmless post.
    """
    facets = []
    if mention_did:
        facets = [{"features": [
            {"$type": "app.bsky.richtext.facet#mention", "did": mention_did}]}]
    rekord = {"text": szoveg, "facets": facets}
    if gyoker_uri or szulo_uri:
        rekord["reply"] = {
            "parent": {"uri": szulo_uri or gyoker_uri,
                       "cid": szulo_cid or gyoker_cid or ""},
            "root": {"uri": gyoker_uri or szulo_uri,
                     "cid": gyoker_cid or szulo_cid or ""},
        }
    return {
        "uri": uri,
        "cid": cid,
        "author": {"did": szerzo_did, "handle": handle},
        "record": rekord,
    }


def gyoker_poszt(uri=GYOKER_URI, cid=GYOKER_CID,
                 szerzo_did="did:plc:harmadikfel789",
                 szoveg="Ez egy teljesen idegen beszélgetés gyökere."):
    """The `getPosts` response item of the thread root."""
    return {
        "uri": uri,
        "cid": cid,
        "author": {"did": szerzo_did, "handle": "harmadik.bsky.social"},
        "record": {"text": szoveg, "facets": []},
    }


class MockHalo:
    """Bluesky adapter mock. No network call is ever made."""

    def __init__(self, did=SAJAT_DID, posztok=None, feed_elemek=None,
                 ertesites_valasz=None):
        self.did = did
        self.posztok = posztok or {}
        self.feed_elemek = feed_elemek or []
        self.ertesites_valasz = ertesites_valasz or {"notifications": []}
        self.letrehozasok = []
        self.hiba = None
        self.ablak_lefedve = True

    def sajat_did(self):
        return self.did

    def sajat_handle(self):
        return SAJAT_HANDLE

    def poszt(self, uri):
        return self.posztok.get(uri)

    def sajat_feed(self, limit=50):
        # `ablak_lefedve`: paging reached the end of the 24-hour window (M''3).
        # The run is FAIL-CLOSED: if this is missing or false, it does not post.
        return {"feed": list(self.feed_elemek),
                "ablak_lefedve": self.ablak_lefedve}

    def ertesitesek(self, limit=50):
        return self.ertesites_valasz

    def idovonal(self, limit):
        return {"feed": []}

    def letrehoz(self, kanonikus, letrehozva, reply_engedelyezve):
        if self.hiba:
            raise self.hiba
        self.letrehozasok.append({
            "kanonikus": json.loads(json.dumps(kanonikus)),
            "reply_engedelyezve": reply_engedelyezve,
        })
        return {"uri": "at://%s/app.bsky.feed.post/uj%d"
                       % (self.did, len(self.letrehozasok))}

    # --- FOLLOW and LIKE (2026-10-02) -----------------------------------
    # `profilok`: actor (handle OR did) → getProfile response; anything not
    # in it is a "non-existent profile" (None). `sajat_rekord_lista`:
    # collection → listRecords response. `akcio_hiba`: if set, the READ calls
    # raise it (simulates a network error).

    def profil(self, actor):
        if getattr(self, "akcio_hiba", None):
            raise self.akcio_hiba
        return getattr(self, "profilok", {}).get(actor)

    def sajat_rekordok(self, gyujtemeny, limit=100):
        if getattr(self, "akcio_hiba", None):
            raise self.akcio_hiba
        return getattr(self, "sajat_rekord_lista", {}).get(
            gyujtemeny, {"records": []})

    def kovet(self, cel_did, letrehozva):
        if self.hiba:
            raise self.hiba
        if not hasattr(self, "kovetesek"):
            self.kovetesek = []
        self.kovetesek.append({"cel_did": cel_did, "letrehozva": letrehozva})
        return {"uri": "at://%s/app.bsky.graph.follow/k%d"
                       % (self.did, len(self.kovetesek)),
                "cid": "bafyfollowcid%d" % len(self.kovetesek)}

    def kedvel(self, cel_uri, cel_cid, letrehozva):
        if self.hiba:
            raise self.hiba
        if not hasattr(self, "kedvelesek"):
            self.kedvelesek = []
        self.kedvelesek.append({"cel_uri": cel_uri, "cel_cid": cel_cid,
                                "letrehozva": letrehozva})
        return {"uri": "at://%s/app.bsky.feed.like/l%d"
                       % (self.did, len(self.kedvelesek)),
                "cid": "bafylikecid%d" % len(self.kedvelesek)}


class MockBot:
    """Telegram adapter mock."""

    def __init__(self, user_id=111, chat_id=222):
        self.user_id = user_id
        self.chat_id = chat_id
        self.kuldott = []
        self.sor = []
        self._kovetkezo = 1000

    def owner_user_id(self):
        return self.user_id

    def owner_chat_id(self):
        return self.chat_id

    def kuld(self, szoveg):
        self._kovetkezo += 1
        self.kuldott.append({"message_id": self._kovetkezo, "szoveg": szoveg})
        return self._kovetkezo

    def frissitesek(self, offset):
        ki = [f for f in self.sor
              if offset is None or f.get("update_id", 0) >= offset]
        return ki

    # --- convenience message builders -----------------------------------

    def igen(self, elonezet_message_id, update_id=1, message_id=None,
             szoveg="IGEN", **felulir):
        uzenet = {
            "message_id": message_id if message_id is not None
            else 5000 + update_id,
            "from": {"id": self.user_id, "is_bot": False},
            "chat": {"id": self.chat_id, "type": "private"},
            "text": szoveg,
            "reply_to_message": {"message_id": elonezet_message_id},
        }
        uzenet.update(felulir)
        self.sor.append({"update_id": update_id, "message": uzenet})
        return self.sor[-1]


def utolso_elonezet_id(bot):
    return bot.kuldott[-1]["message_id"]
