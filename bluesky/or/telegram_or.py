"""telegram_or.py — the GUARD'S OWN Telegram bot (the SECOND bot).

Why a new bot: the existing bot's database can be written by the untrusted
user, so it cannot be an anchor. This bot lives only under the guard's user,
its token is in the `titok\\` folder, and the guard reads it DIRECTLY, with its
own `getUpdates` long-poll — there is no intermediate database anyone could rewrite.

SECRET HYGIENE: the Telegram URL CONTAINS the bot token. Therefore
  * the URL never ends up in an exception text, a log, or the console;
  * every exception is stripped down to its TYPE;
  * the module writes NOTHING to a file.

Deciding on an approval is a PURE function (`dontes`) — testable without a network.
"""

import json
import urllib.parse
import urllib.request

import beallitasok

HOSZT = "api.telegram.org"
IDOTULLEPES_ALAP = 20


class TelegramHiba(RuntimeError):
    """Telegram error WITHOUT SECRETS: only the operation name and the error type."""


class _NincsAtiranyitas(urllib.request.HTTPRedirectHandler):
    """3xx refused — the URL containing the token must not go to a foreign host."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NincsAtiranyitas)


def _keres(token, metodus, adat=None, idotullepes=IDOTULLEPES_ALAP):
    """One Bot API call. The return value is the `result` field.

    The `token` appears ONLY here, and does not go anywhere from here either:
    every exception is caught and re-raised as `TelegramHiba`, without the URL.
    """
    url = "https://%s/bot%s/%s" % (HOSZT, token, metodus)
    if urllib.parse.urlparse(url).hostname != HOSZT:
        raise TelegramHiba("Only the fixed Telegram host may be called.")
    test = json.dumps(adat or {}).encode("utf-8")
    fejlec = {"Content-Type": "application/json", "Accept": "application/json"}
    keres = urllib.request.Request(url, data=test, headers=fejlec, method="POST")
    try:
        with _OPENER.open(keres, timeout=idotullepes) as valasz:
            nyers = valasz.read(beallitasok.TELEGRAM_MAX_VALASZ + 1)
    except Exception as e:  # noqa: BLE001 — stripped on purpose
        raise TelegramHiba("Telegram call failed: %s (%s)"
                           % (metodus, type(e).__name__)) from None
    if len(nyers) > beallitasok.TELEGRAM_MAX_VALASZ:
        raise TelegramHiba("Telegram response too large: " + metodus)
    try:
        csomag = json.loads(nyers.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise TelegramHiba("Telegram response could not be parsed: %s (%s)"
                           % (metodus, type(e).__name__)) from None
    if not isinstance(csomag, dict) or not csomag.get("ok"):
        # `description` comes from Telegram (external data) — we do not expose it.
        raise TelegramHiba("Telegram did not accept the call: " + metodus)
    return csomag.get("result")


def uzenet_kuldes(token, chat_id, szoveg):
    """Plain text message. Returns: the `message_id` of the sent message.

    There is INTENTIONALLY no formatting (`parse_mode`): characters in the text
    cannot create links or formatting.
    """
    eredmeny = _keres(token, "sendMessage", {
        "chat_id": chat_id,
        "text": szoveg,
        "disable_web_page_preview": True,
    })
    if not isinstance(eredmeny, dict) or not eredmeny.get("message_id"):
        raise TelegramHiba("Telegram returned no message_id (sendMessage).")
    return int(eredmeny["message_id"])


def frissitesek(token, offset, idotullepes=beallitasok.TELEGRAM_LONGPOLL):
    """`getUpdates` long-poll. `offset` is the NEXT expected update_id.

    NEVER called with `offset=-1`: that would drop unprocessed messages.
    `offset=None` → the server returns all pending ones (first start).
    """
    adat = {"timeout": int(idotullepes), "allowed_updates": ["message"]}
    if offset is not None:
        if int(offset) < 0:
            raise TelegramHiba("Negative getUpdates offset — not calling it.")
        adat["offset"] = int(offset)
    eredmeny = _keres(token, "getUpdates", adat, idotullepes=idotullepes + 10)
    return eredmeny if isinstance(eredmeny, list) else []


# --- DECIDING ON THE APPROVAL (pure function) ----------------------------

JOVAHAGY = "jovahagy"
ELVET = "elvet"
FIGYELMEN_KIVUL = "figyelmen_kivul"


def dontes(frissites, owner_user_id, owner_chat_id):
    """`(dontes, valasz_message_id, sajat_message_id)` — (decision, replied-to id, own id).

    A decision is valid ONLY if ALL of these hold:
      * `message` (not `edited_message`, not `channel_post`, no other type),
      * no `edit_date` (not edited),
      * not forwarded (none of `forward_*` / `forward_origin`),
      * not `via_bot`,
      * the sender is not a bot, and `from.id` = the owner's user_id,
      * `chat.id` = the owner's chat_id AND the chat is `private`,
      * there is a `reply_to_message.message_id` (the approval refers to it),
      * after trimming whitespace, case-insensitively, the text is EXACTLY
        "IGEN" (yes) or "NEM" (no).

    Anything else → FIGYELMEN_KIVUL (ignore). This list is fail-closed: a
    missing field also locks.
    """
    ures = (FIGYELMEN_KIVUL, None, None)
    if not isinstance(frissites, dict):
        return ures
    # Only a plain `message` counts. `edited_message` deliberately does not.
    if "message" not in frissites:
        return ures
    uzenet = frissites.get("message")
    if not isinstance(uzenet, dict):
        return ures
    if uzenet.get("edit_date"):
        return ures
    for mezo in ("forward_origin", "forward_from", "forward_from_chat",
                 "forward_date", "forward_sender_name", "forward_signature",
                 "via_bot", "sender_chat", "is_automatic_forward"):
        if uzenet.get(mezo):
            return ures

    kuldo = uzenet.get("from")
    if not isinstance(kuldo, dict) or kuldo.get("is_bot"):
        return ures
    if kuldo.get("id") is None or owner_user_id is None:
        return ures
    try:
        if int(kuldo["id"]) != int(owner_user_id):
            return ures
    except (TypeError, ValueError):
        return ures

    chat = uzenet.get("chat")
    if not isinstance(chat, dict) or chat.get("type") != "private":
        return ures
    if chat.get("id") is None or owner_chat_id is None:
        return ures
    try:
        if int(chat["id"]) != int(owner_chat_id):
            return ures
    except (TypeError, ValueError):
        return ures

    valasz = uzenet.get("reply_to_message")
    if not isinstance(valasz, dict) or valasz.get("message_id") is None:
        return ures
    try:
        valasz_id = int(valasz["message_id"])
    except (TypeError, ValueError):
        return ures

    szoveg = uzenet.get("text")
    if not isinstance(szoveg, str):
        return ures
    # The raw text LITERALLY: no Unicode normalisation, no homoglyph replacement.
    # The spec says "exactly IGEN"; the stricter reading is that no look-alike
    # form may slip through (Cyrillic "ИГЕН", full-width "ＩＧＥＮ").
    # Hungarian language data: the approval words "igen" (yes) and "nem" (no)
    # are the tokens the owner types; to use another language, change both
    # literals below (and the matching texts in the preview messages).
    tiszta = szoveg.strip().casefold()
    sajat_id = uzenet.get("message_id")
    try:
        sajat_id = int(sajat_id) if sajat_id is not None else None
    except (TypeError, ValueError):
        sajat_id = None

    if tiszta == "igen":
        return (JOVAHAGY, valasz_id, sajat_id)
    if tiszta == "nem":
        return (ELVET, valasz_id, sajat_id)
    return ures


def lejart_e(elonezet_ts, most_ts, ervenyesseg=beallitasok.ELONEZET_ERVENYESSEG):
    """The preview expires after 48 hours. Missing timestamp → EXPIRED (stricter)."""
    if elonezet_ts is None:
        return True
    try:
        return (most_ts - float(elonezet_ts)) > ervenyesseg
    except (TypeError, ValueError):
        return True
