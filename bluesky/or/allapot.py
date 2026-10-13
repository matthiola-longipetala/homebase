"""allapot.py — the guard's OWN state: this is the trust ANCHOR.

The main lesson of the two earlier reviews: "the anchor of approval must not
be in a place writable by the untrusted side". Therefore everything that
binds — the `message_id` of the sent preview, the canonical outgoing record
and its sha256, the used approvals, the `kuldes_alatt` write-ahead marker —
lives in the `C:\\BskyGuard\\allapot\\` folder, where the untrusted user has
NO write access (this is checked by `jogok.py` at startup).

Posting happens EXCLUSIVELY from the canonical record stored here; the draft
file is never read again after the preview.
"""

import hashlib
import hmac
import json
import os
import secrets
import time

import beallitasok
import szures
import tarolas_or

VAR = "var"
KULDES_ALATT = "kuldes_alatt"
ELKULDVE = "elkuldve"
ELVETVE = "elvetve"
LEJART = "lejart"
KEZI_ELLENORZES = "kezi_ellenorzes"
ELUTASITVA = "elutasitva"

NYITOTT = (VAR,)
LEZART = (ELKULDVE, ELVETVE, LEJART, ELUTASITVA)


def uj_azonosito():
    return secrets.token_hex(8)


def rekord_hash(kanonikus):
    """The sha256 of the stored canonical record (sorted keys, UTF-8)."""
    return szures.jovahagyas_hash(kanonikus)


def _utvonal(kornyezet, azonosito):
    return os.path.join(kornyezet.elonezetek, azonosito + ".json")


def ment(kornyezet, rekord):
    """ATOMIC write of one preview record (fsync + os.replace)."""
    tarolas_or.mappa_keszit(kornyezet.elonezetek)
    tarolas_or.json_ir(_utvonal(kornyezet, rekord["azonosito"]), rekord)


def betolt_mind(kornyezet):
    """All preview records, sorted by creation.

    An unreadable or broken file does not vanish silently: it goes into the
    list with a `_serult` (damaged) marker, and the caller stops on it.
    """
    rekordok = []
    try:
        nevek = sorted(os.listdir(kornyezet.elonezetek))
    except OSError:
        return rekordok
    for nev in nevek:
        if not nev.endswith(".json"):
            continue
        teljes = os.path.join(kornyezet.elonezetek, nev)
        adat = tarolas_or.json_olvas(teljes)
        if not isinstance(adat, dict) or not adat.get("azonosito"):
            rekordok.append({"_serult": True, "fajl": nev})
            continue
        rekordok.append(adat)
    rekordok.sort(key=lambda r: (r.get("letrehozva_ts") or 0,
                                 r.get("azonosito") or ""))
    return rekordok


def serult(rekordok):
    return [r.get("fajl") for r in rekordok if r.get("_serult")]


def fuggo(rekordok):
    """The previews WAITING for approval."""
    return [r for r in rekordok if r.get("allapot") == VAR]


def beragadt(rekordok):
    """Records stuck in the `kuldes_alatt` state — these are NOT resent."""
    return [r for r in rekordok if r.get("allapot") == KULDES_ALATT]


def message_id_szerint(rekordok, message_id):
    """The preview whose SENT message has the given message_id.

    This is the anchor: the `reply_to_message.message_id` of the approving
    message is compared with it. There cannot be several matches (message_id
    is unique within the chat), but if there were, we return `None` —
    ambiguity means rejection.
    """
    if message_id is None:
        return None
    talalat = [r for r in rekordok
               if not r.get("_serult") and r.get("message_id") == message_id]
    return talalat[0] if len(talalat) == 1 else None


def uj_rekord(kanonikus, cel, piszkozat_nev, megjegyzes, most_ts):
    """The preview record to store (no message_id yet)."""
    return {
        "azonosito": uj_azonosito(),
        "letrehozva_ts": int(most_ts),
        "message_id": None,
        "allapot": VAR,
        "kanonikus": kanonikus,
        "kanonikus_sha256": rekord_hash(kanonikus),
        "cel": cel,
        "piszkozat_nev": szures.egysoros(piszkozat_nev, 120),
        "megjegyzes": szures.szanitalas(megjegyzes or "",
                                        beallitasok.MEGJEGYZES_MAX),
        "jovahagyo_message_id": None,
        "poszt_uri": None,
    }


def rekord_ep_e(rekord):
    """Is the stored record intact (does the sha256 match the canonical one)?

    This does not protect against the untrusted side — it does not write here —
    but against half-writes, disk errors and our own program bugs.
    """
    try:
        return rekord_hash(rekord["kanonikus"]) == rekord["kanonikus_sha256"]
    except (KeyError, TypeError):
        return False


# --- used approvals (one preview = one approval = one post) ---------------

def felhasznalt_rogzit(kornyezet, message_id, elonezet_azonosito, dontes):
    tarolas_or.jsonl_hozzafuz(kornyezet.felhasznalt, {
        "ido_ts": int(time.time()),
        "jovahagyo_message_id": message_id,
        "elonezet": elonezet_azonosito,
        "dontes": dontes,
    })


def felhasznalt_e(kornyezet, message_id):
    if message_id is None:
        return False
    for sor in tarolas_or.jsonl_olvas(kornyezet.felhasznalt):
        if sor.get("jovahagyo_message_id") == message_id:
            return True
    return False


# --- maintenance: the state must not grow without bound (Kö6) -------------

def felhasznalt_nyeses(kornyezet, most,
                       ablak=beallitasok.FELHASZNALT_ABLAK,
                       kuszob=500):
    """Trimming the log of used approvals.

    The dedup window can safely be one week: a preview expires after 48
    hours, so an older message could not approve anything anyway. The file
    is rewritten only if it has really grown — so there are no needless writes.
    """
    sorok = tarolas_or.jsonl_olvas(kornyezet.felhasznalt)
    if len(sorok) <= kuszob:
        return 0
    megtart = [s for s in sorok
               if not s.get("_serult")
               and (most - (s.get("ido_ts") or 0)) <= ablak]
    if len(megtart) == len(sorok):
        return 0
    tarolas_or.fsync_ir(
        kornyezet.felhasznalt,
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in megtart))
    return len(sorok) - len(megtart)


def archivalas(kornyezet, most, kor=beallitasok.ARCHIVALAS_KOR):
    """Moving old, CLOSED preview records under `allapot\\archiv\\`.

    Without this every round would read every record, and the closed items
    would also fill up the fifty-item list of the outward state file.
    """
    try:
        nevek = sorted(os.listdir(kornyezet.elonezetek))
    except OSError:
        return 0
    mozgatott = 0
    for nev in nevek:
        if not nev.endswith(".json"):
            continue
        teljes = os.path.join(kornyezet.elonezetek, nev)
        adat = tarolas_or.json_olvas(teljes)
        if not isinstance(adat, dict):
            continue
        if adat.get("allapot") not in LEZART:
            continue
        if (most - (adat.get("letrehozva_ts") or 0)) <= kor:
            continue
        tarolas_or.mappa_keszit(kornyezet.archiv)
        try:
            os.replace(teljes, os.path.join(kornyezet.archiv, nev))
            mozgatott += 1
        except OSError:
            continue
    return mozgatott


# --- FINGERPRINT of the dropped contents (K'''''1/b) ----------------------
#
# We have to identify the content of a dropped file (so the same content is
# not taken over twice), but the identifier must NEVER be reversible to the
# content. A plain sha256 of a short secret can be reversed with a dictionary
# attack, and the earlier version even wrote it to a readable place. Hence:
#
#   * the fingerprint is HMAC-SHA256 with the guard's OWN key, random per
#     installation — to a party not knowing the key, the value is meaningless;
#   * both the key and the table live in the CLOSED folder, on which the
#     untrusted user has no rights at all.

def ujjlenyomat_kulcs(kornyezet):
    """The guard's random HMAC key; created on first call, unchanged after."""
    adat = tarolas_or.json_olvas(kornyezet.bedobott_kulcs, {})
    kulcs = adat.get("kulcs") if isinstance(adat, dict) else None
    if not isinstance(kulcs, str) or len(kulcs) < 32:
        kulcs = secrets.token_hex(32)
        tarolas_or.json_ir(kornyezet.bedobott_kulcs, {"kulcs": kulcs})
    return kulcs.encode("ascii")


def tartalom_ujjlenyomat(kornyezet, bajtok):
    """An IRREVERSIBLE fingerprint of a dropped content."""
    return hmac.new(ujjlenyomat_kulcs(kornyezet), bajtok or b"",
                    hashlib.sha256).hexdigest()


# --- Telegram offset ----------------------------------------------------

def offset_olvas(kornyezet):
    adat = tarolas_or.json_olvas(kornyezet.offset, {})
    ertek = (adat or {}).get("offset")
    return int(ertek) if isinstance(ertek, int) and ertek >= 0 else None


def offset_ir(kornyezet, offset):
    if offset is None or offset < 0:
        return
    tarolas_or.json_ir(kornyezet.offset, {"offset": int(offset)})


# --- log and the outward state file -------------------------------------

def naploz(kornyezet, esemeny):
    """Log into the `allapot\\` folder.

    It is the caller's responsibility that no SECRET gets into it. Therefore
    nowhere do we write exception text into the log, only the exception TYPE,
    and nowhere do we write outgoing text, only its hash."""
    tarolas_or.jsonl_hozzafuz(kornyezet.naplo,
                              dict(esemeny, ido_ts=int(time.time())))


def kifele_allapot(kornyezet, futas_ts, or_allapot, tetelek, uzenetek):
    """The content of `kozos\\be\\allapot.json` — WITHOUT SECRETS.

    This file is READ by the untrusted side (it cannot write it). Nothing may
    get into it that comes from the secrets folder: no token, no password, no
    chat_id, no user_id, no full path.
    """
    return {
        "frissitve_ts": int(futas_ts),
        "or_allapot": or_allapot,
        "uzenetek": [szures.egysoros(u, 300) for u in uzenetek][:20],
        "tetelek": tetelek[:50],
    }


# Kö''''4: HUMAN-language counterparts of the rejection reasons. An outward
# code such as `felmeno_lanc_tul_hosszu` does not say what should be done.
# The machine reason does NOT disappear; the explanation is added next to it.
OK_MAGYARAZAT = {
    "felmeno_lanc_tul_hosszu":
        "the conversation is deeper than I can check all the way "
        "(%d steps). I do not reply into a thread whose top I have not seen. "
        "If it matters, reply directly to the post at the top of the thread."
        % beallitasok.FELMENO_MAX_LEPES,
    "felmeno_nem_igazolhato":
        "I can no longer find one of the earlier posts of the conversation on "
        "the network (it was probably deleted). So I cannot tell whose thread "
        "the reply would land in, therefore I am not sending it.",
    "gyoker_nem_igazolhato":
        "I can no longer find the top post of the conversation on the network "
        "(it was probably deleted). So I cannot tell whose thread the reply "
        "would land in, therefore I am not sending it.",
    "felmeno_lanc_korben_jar":
        "the ancestors of the conversation point around in a circle — this is "
        "not a regular thread, I do not reply into it.",
    "felmeno_alakja_rossz":
        "the reference of one of the conversation's ancestors has an irregular form.",
    "felmeno_uri_elter":
        "the ancestor post fetched from the network is not the one the reference points to.",
    "felmeno_szerzoje_ismeretlen":
        "the author of one of the conversation's ancestors cannot be determined.",
    "gyoker_alakja_rossz":
        "the target post names a thread root of irregular form.",
    "gyoker_uri_elter":
        "the thread root fetched from the network is not the one the target post names.",
    "gyoker_cid_elter":
        "the content of the thread root does not match what the target post names.",
    "gyoker_szerzoje_ismeretlen":
        "the author of the thread root cannot be determined.",
    "szoveg_atirasra_szorulna":
        "the text contains a character that is not visible on screen (hidden "
        "or special code point). What cannot be seen cannot be approved "
        "— rewrite the text with ordinary characters.",
    "handle_megvaltozott":
        "the addressee changed their name since the approval — you saw something "
        "different from what is there now, so I did not send it.",
    # --- FOLLOW and LIKE (2026-10-02) ------------------------------------
    "nincs_ilyen_profil":
        "no such profile on Bluesky (or it was deleted, suspended, "
        "deactivated). Check the handle or the DID, and drop it in again.",
    "nincs_ilyen_poszt":
        "I cannot find the post to be liked on the network (it was probably "
        "deleted, or the reference is wrong).",
    "mar_kovetve":
        "we already follow this profile — nothing to do.",
    "mar_kedvelve":
        "we already liked this post — nothing to do.",
    "mar_jovahagyasra_var":
        "a preview for the same target is already waiting for approval — I am "
        "not sending this second one.",
    "sajat_fiok":
        "we do not follow our own account.",
    "sajat_poszt":
        "we do not like our own post.",
    "letiltas_miatt_nem":
        "there is a block between us and the profile — I am not following it.",
    "profil_did_elter":
        "the DID of the profile fetched from the network is not the one in the draft.",
    "profil_handle_elter":
        "the handle of the profile fetched from the network is not the one in the draft.",
    "profil_did_ervenytelen":
        "the network returned a DID of irregular form for the profile.",
    "poszt_uri_elter":
        "the post fetched from the network is not the one the reference points to.",
    "poszt_cid_ervenytelen":
        "the network returned an irregular content identifier (cid) for the post.",
    "poszt_szerzo_elter":
        "the author of the post is not the one whose repo the reference points to.",
    "cel_handle_vagy_cel_did":
        "a follow needs the `cel_handle` or the `cel_did` field (one of them).",
    "cel_handle_es_cel_did_egyszerre":
        "for a follow, give EXACTLY ONE of `cel_handle` and "
        "`cel_did`.",
    "rossz_cel_handle":
        "`cel_handle` is not a valid handle (example: someone.bsky.social, "
        "without the @ sign).",
    "rossz_cel_did":
        "`cel_did` is not a valid DID (example: did:plc:…).",
    "rossz_cel_uri":
        "`cel_uri` is not the at:// reference of a post "
        "(at://did:…/app.bsky.feed.post/…).",
    "kovetes_napi_limit":
        "we reached the daily follow cap (%d / 24 hours). The draft waits, "
        "and will proceed by itself later." % beallitasok.KOVETES_NAPI_MAX,
    "kedveles_napi_limit":
        "we reached the daily like cap (%d / 24 hours). The draft waits, "
        "and will proceed by itself later." % beallitasok.KEDVELES_NAPI_MAX,
}


def ok_magyarazatok(okok):
    """The human explanations for the reasons (without repetition, in order).

    The reason's prefix (`optin:`, `tilto:`) is stripped before lookup.
    """
    ki = []
    for ok in okok or []:
        csupasz = str(ok).split(":", 1)[-1] if ":" in str(ok) else str(ok)
        szoveg = OK_MAGYARAZAT.get(csupasz)
        if szoveg and szoveg not in ki:
            ki.append(szoveg)
    return ki


def kifele_tetel(rekord):
    """The OUTWARD-showable summary of a preview (without secrets)."""
    kanonikus = rekord.get("kanonikus") or {}
    tetel = {
        "azonosito": rekord.get("azonosito"),
        "allapot": rekord.get("allapot"),
        "letrehozva_ts": rekord.get("letrehozva_ts"),
        "tipus": kanonikus.get("tipus"),
        "szoveg_sha256": hashlib.sha256(
            (kanonikus.get("szoveg") or "").encode("utf-8")).hexdigest(),
        "piszkozat": rekord.get("piszkozat_nev"),
        "cel_uri": (rekord.get("cel") or {}).get("uri"),
        "poszt_uri": rekord.get("poszt_uri"),
        "ok": rekord.get("ok"),
        "ok_magyarul": ok_magyarazatok(rekord.get("ok")),
    }
    # 2026-10-02: follow / like items also carry their own identifiers NEXT TO
    # the existing fields (the shape of post items is unchanged). Here
    # `szoveg_sha256` is the hash of the empty text: these have no outgoing text.
    if kanonikus.get("tipus") == "kovetes":
        tetel["cel_did"] = kanonikus.get("cel_did")
        tetel["cel_handle"] = kanonikus.get("cel_handle")
        tetel["follow_uri"] = rekord.get("follow_uri")
    elif kanonikus.get("tipus") == "kedveles":
        tetel["cel_uri"] = kanonikus.get("cel_uri")
        tetel["like_uri"] = rekord.get("like_uri")
    return tetel


def kifele_ir(kornyezet, adat):
    """Writing out `kozos\\be\\allapot.json`.

    We do NOT create the folder: the owner creates it with the correct NTFS rights.
    If we created it, it would come into existence with inherited (possibly wrong) rights.
    """
    if not os.path.isdir(kornyezet.be):
        raise OSError("the kozos\\be folder does not exist — I will not create it.")
    tarolas_or.json_ir(kornyezet.allapot_kifele, adat)


def elutasitott_tetel(piszkozat_nev, okok):
    """The outward-showable row of a REJECTED draft."""
    return {
        "azonosito": None,
        "allapot": ELUTASITVA,
        "letrehozva_ts": int(time.time()),
        "piszkozat": szures.egysoros(piszkozat_nev, 120),
        "ok": [szures.egysoros(o, 60) for o in okok][:10],
        "ok_magyarul": [szures.egysoros(m, 300)
                        for m in ok_magyarazatok(okok)][:5],
    }


def json_biztos(adat):
    """Test helper: whether the outward structure is serializable."""
    return json.dumps(adat, ensure_ascii=False)
