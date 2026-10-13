"""bsky_api.py — the MINIMAL network layer towards Bluesky (AT Protocol).

Four calls are needed: login, notifications, FETCHING A POST (for the check
before sending), creating a post (+ optionally the timeline and an author feed).

IMPORTANT:
- The password NEVER ends up in a log, on the console or in an error message.
  Errors are "stripped" (`_biztonsagos_hiba`), because HTTP errors may include
  the request body or the URL in their message.
- REDIRECTS ARE NOT FOLLOWED (Kö9): otherwise the `Authorization: Bearer <JWT>`
  header could be sent to a foreign host.
- The response SIZE is capped: a compromised PDS cannot exhaust memory.
- Only the fixed host is called.
- The tests do NOT call this module: there is no network there.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

HOSZT = "bsky.social"
BAZIS = "https://" + HOSZT + "/xrpc/"
IDOTULLEPES = 15
MAX_VALASZ = 2 * 1024 * 1024  # 2 MB


class _NincsAtiranyitas(urllib.request.HTTPRedirectHandler):
    """Every 3xx redirect is refused — the token must not go to a foreign host."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NincsAtiranyitas)


def _biztonsagos_hiba(muvelet, kivetel):
    """An error message without secrets: only the operation name and the error TYPE."""
    tipus = type(kivetel).__name__
    kod = getattr(kivetel, "code", None)
    if kod:
        return RuntimeError(f"Bluesky call failed: {muvelet} ({tipus}, HTTP {kod})")
    return RuntimeError(f"Bluesky call failed: {muvelet} ({tipus})")


def _hoszt_ellenorzes(url):
    if urllib.parse.urlparse(url).hostname != HOSZT:
        raise ValueError("Only the fixed Bluesky host may be called.")


def _keres(metodus, url, adat=None, token=None):
    _hoszt_ellenorzes(url)
    fejlec = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        fejlec["Authorization"] = "Bearer " + token
    test = json.dumps(adat).encode("utf-8") if adat is not None else None
    keres = urllib.request.Request(url, data=test, headers=fejlec, method=metodus)
    with _OPENER.open(keres, timeout=IDOTULLEPES) as valasz:
        nyers = valasz.read(MAX_VALASZ + 1)
    if len(nyers) > MAX_VALASZ:
        raise ValueError("The response exceeded the allowed size.")
    return json.loads(nyers.decode("utf-8"))


def belepes(handle, app_jelszo):
    """com.atproto.server.createSession — with an app password.

    Returns: {"did":…, "accessJwt":…, "handle":…}. The password is then forgotten.
    """
    try:
        return _keres("POST", BAZIS + "com.atproto.server.createSession",
                      {"identifier": handle, "password": app_jelszo})
    except Exception as e:  # noqa: BLE001 — the error is stripped on purpose
        raise _biztonsagos_hiba("createSession", e) from None


def ertesitesek(token, limit=50):
    """app.bsky.notification.listNotifications"""
    url = (BAZIS + "app.bsky.notification.listNotifications?"
           + urllib.parse.urlencode({"limit": limit}))
    try:
        return _keres("GET", url, token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("listNotifications", e) from None


def idovonal(token, limit=20):
    """app.bsky.feed.getTimeline — posts of the followed accounts."""
    url = BAZIS + "app.bsky.feed.getTimeline?" + urllib.parse.urlencode({"limit": limit})
    try:
        return _keres("GET", url, token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("getTimeline", e) from None


def szerzo_posztjai(token, handle, limit=20):
    """app.bsky.feed.getAuthorFeed — the latest posts of a given account."""
    url = (BAZIS + "app.bsky.feed.getAuthorFeed?"
           + urllib.parse.urlencode({"actor": handle, "limit": limit}))
    try:
        return _keres("GET", url, token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("getAuthorFeed", e) from None


def poszt_lekerese(token, uri):
    """app.bsky.feed.getPosts — the fresh state of ONE post (K3).

    This is the trust anchor for sending a reply: not inbox.jsonl, but what
    Bluesky says about the target RIGHT NOW. None if there is no such post.
    """
    url = (BAZIS + "app.bsky.feed.getPosts?"
           + urllib.parse.urlencode({"uris": uri}))
    try:
        valasz = _keres("GET", url, token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("getPosts", e) from None
    posztok = valasz.get("posts") or []
    for p in posztok:
        if p.get("uri") == uri:
            return p
    return None


def poszt_letrehozas(token, did, kanonikus, letrehozva, reply_engedelyezve=False):
    """com.atproto.repo.createRecord — app.bsky.feed.post.

    The record is built EXCLUSIVELY from the `kanonikus` (i.e. approved and
    hashed) outgoing structure — no other field can leak into it (K1, K2).
    A `reply` block is added only if the caller (kuldo.py) gives EXPLICIT
    permission after running the opt-in gate.

    Facets are DELIBERATELY not generated: the post is plain text, with no
    automatic mention/link — so we cannot address anyone by accident.
    """
    cel = kanonikus.get("valasz_cel")
    if cel and not reply_engedelyezve:
        raise ValueError("reply block without permission — sending stops.")
    if kanonikus.get("tipus") != "valasz" and cel:
        raise ValueError("tipus/valasz_cel contradiction — sending stops.")

    rekord = {
        "$type": "app.bsky.feed.post",
        "text": kanonikus["szoveg"],
        "createdAt": letrehozva,
        "langs": list(kanonikus.get("langs") or ["hu"]),
    }
    if cel and reply_engedelyezve:
        rekord["reply"] = {
            "root": {"uri": cel["root_uri"], "cid": cel["root_cid"]},
            "parent": {"uri": cel["parent_uri"], "cid": cel["parent_cid"]},
        }
    adat = {"repo": did, "collection": "app.bsky.feed.post", "record": rekord}
    try:
        return _keres("POST", BAZIS + "com.atproto.repo.createRecord", adat, token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("createRecord", e) from None
