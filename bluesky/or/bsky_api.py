"""bsky_api.py — the MINIMAL network layer towards Bluesky (AT Protocol).

Four calls are needed: login, notifications, FETCHING A POST (for the
pre-send check), creating a post (+ optionally timeline and author feed).

IMPORTANT:
- The password NEVER ends up in a log, on the console, or in an error message.
  Errors are "stripped" (`_biztonsagos_hiba`), because HTTP errors may embed
  the request body or the URL in their message.
- REDIRECTS ARE NOT FOLLOWED (Kö9): otherwise the `Authorization: Bearer <JWT>`
  header could also be sent to a foreign host.
- The response SIZE is capped: a compromised PDS cannot exhaust memory.
- Only the fixed host is called.
- The tests NEVER touch the network: the flow tests mock the adapter, and
  the 2026-10-02 record-structure tests replace the `_keres` function
  (`tesztek/test_kovetes_kedveles.py`).

2026-10-02: FOLLOW and LIKE. Four new calls: `getProfile` (the fresh state of
the profile to follow, including `viewer.following` — whether we already
follow it), `listRecords` (our own follow/like records, for the cap), and the
two writing `createRecord` calls (`app.bsky.graph.follow`, `app.bsky.feed.like`).
A writing call — as with posts — is NEVER retried.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

HOSZT = "bsky.social"
BAZIS = "https://" + HOSZT + "/xrpc/"
IDOTULLEPES = 15
MAX_VALASZ = 2 * 1024 * 1024  # 2 MB
# Filter for `getAuthorFeed`. We must see our replies too, because the daily
# cap applies to them as well (M''3).
SZERZO_FEED_SZURO = "posts_with_replies"


class _NincsAtiranyitas(urllib.request.HTTPRedirectHandler):
    """Every 3xx redirect is refused — the token must not go to a foreign host."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NincsAtiranyitas)


def _biztonsagos_hiba(muvelet, kivetel):
    """Error message without secrets: only the operation name and the error TYPE."""
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
    # 2026-09-20: authenticated READ calls (GET) sometimes take longer than 15 s,
    # and this made the guard stop at startup. Reads are repeatable, so GET gets
    # more patience and at most three attempts. A WRITE call (POST) is NEVER
    # retried: that could produce a duplicate post.
    probak = 3 if metodus == "GET" else 1
    turelem = IDOTULLEPES * 2 if metodus == "GET" else IDOTULLEPES
    for proba in range(probak):
        keres = urllib.request.Request(url, data=test, headers=fejlec, method=metodus)
        try:
            with _OPENER.open(keres, timeout=turelem) as valasz:
                nyers = valasz.read(MAX_VALASZ + 1)
            break
        except urllib.error.HTTPError as e:
            if e.code < 500 or proba == probak - 1:
                raise
        except (TimeoutError, urllib.error.URLError, OSError):
            if proba == probak - 1:
                raise
        time.sleep(2 + 3 * proba)
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


def szerzo_posztjai(token, handle, limit=20, kurzor=None,
                    szuro=SZERZO_FEED_SZURO):
    """app.bsky.feed.getAuthorFeed — the latest posts of a given account.

    The `filter` parameter is set EXPLICITLY (M''3): the correctness of the
    daily cap depends on our own REPLIES being in the feed. That is the
    service's current default, but we do not rely on it.

    The `kurzor` (cursor) is needed for paging: we must go back to the end of
    the 24-hour window, otherwise the cap does not see the whole truth.
    """
    parameterek = {"actor": handle, "limit": limit, "filter": szuro}
    if kurzor:
        parameterek["cursor"] = kurzor
    url = (BAZIS + "app.bsky.feed.getAuthorFeed?"
           + urllib.parse.urlencode(parameterek))
    try:
        return _keres("GET", url, token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("getAuthorFeed", e) from None


def poszt_lekerese(token, uri):
    """app.bsky.feed.getPosts — the fresh state of ONE post (K3).

    This is the trust anchor of sending a reply: not inbox.jsonl, but what
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
    A `reply` block is only added if the caller (kuldo.py) gives EXPLICIT
    permission after running the opt-in gate.

    Facets are INTENTIONALLY not generated: the post is plain text, with no
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


# --- FOLLOW and LIKE (2026-10-02) ----------------------------------------

# `getProfile` returns HTTP 400 for a non-existent (or permanently unavailable)
# profile, not an empty response. This must be distinguished from other 400
# errors: an expired token (ExpiredToken) is also 400, and that does NOT mean
# the profile does not exist — it is a transient error that should be deferred.
_NEM_LETEZO_PROFIL_HIBAK = ("AccountTakedown", "AccountDeactivated")
_HIBATEST_MAX = 64 * 1024


def _nem_letezo_profil_e(http_hiba):
    """Does the body of the 400 response say that the profile DOES NOT EXIST?

    We only compare: nothing from the server's text is passed on.
    """
    if getattr(http_hiba, "code", None) != 400:
        return False
    try:
        nyers = http_hiba.read(_HIBATEST_MAX + 1)
        adat = json.loads(nyers[:_HIBATEST_MAX].decode("utf-8"))
    except Exception:  # noqa: BLE001 — unparseable body: no decision
        return False
    if not isinstance(adat, dict):
        return False
    hiba = adat.get("error")
    uzenet = str(adat.get("message") or "").lower()
    if hiba in _NEM_LETEZO_PROFIL_HIBAK:
        return True
    return hiba == "InvalidRequest" and "not found" in uzenet


def profil_lekerese(token, actor):
    """app.bsky.actor.getProfile — the fresh state of ONE profile.

    `actor`: handle or DID (the draft schema has already checked its shape).
    Returns: the profile dict (for an authenticated call it contains the
    `viewer` block: `following`, `blockedBy`, `blocking`), or `None` if the
    profile DOES NOT EXIST. Any other error (network, expired token, 5xx) is an
    exception — the caller defers.
    """
    url = (BAZIS + "app.bsky.actor.getProfile?"
           + urllib.parse.urlencode({"actor": actor}))
    try:
        return _keres("GET", url, token=token)
    except urllib.error.HTTPError as e:
        try:
            nem_letezik = _nem_letezo_profil_e(e)
        finally:
            try:
                e.close()
            except Exception:  # noqa: BLE001
                pass
        if nem_letezik:
            return None
        raise _biztonsagos_hiba("getProfile", e) from None
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("getProfile", e) from None


def sajat_rekordok(token, did, gyujtemeny, limit=100):
    """com.atproto.repo.listRecords — one collection of our OWN repo.

    The service returns the newest records FIRST. The cap counts from this
    (`kimeno.akcio_plafon`): the network truth, not a local log.
    """
    url = (BAZIS + "com.atproto.repo.listRecords?"
           + urllib.parse.urlencode({"repo": did, "collection": gyujtemeny,
                                     "limit": limit}))
    try:
        return _keres("GET", url, token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("listRecords", e) from None


def kovetes_letrehozas(token, did, cel_did, letrehozva):
    """com.atproto.repo.createRecord — app.bsky.graph.follow.

    The record is built EXCLUSIVELY from the `cel_did` field of the approved
    canonical record; the caller (`motor.akcio_vegrehajtas`) re-checks it
    before sending. The writing call is not retried (`_keres`: POST = one attempt).
    """
    if not isinstance(cel_did, str) or not cel_did.startswith("did:"):
        raise ValueError("the follow target is not a DID — sending stops.")
    rekord = {
        "$type": "app.bsky.graph.follow",
        "subject": cel_did,
        "createdAt": letrehozva,
    }
    adat = {"repo": did, "collection": "app.bsky.graph.follow",
            "record": rekord}
    try:
        return _keres("POST", BAZIS + "com.atproto.repo.createRecord", adat,
                      token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("createRecord (follow)", e) from None


def kedveles_letrehozas(token, did, cel_uri, cel_cid, letrehozva):
    """com.atproto.repo.createRecord — app.bsky.feed.like.

    The caller took the `cid` FROM THE NETWORK (the draft cannot even contain
    it) and compared it again after approval: if the post changed in the
    meantime, we never get here.
    """
    if not isinstance(cel_uri, str) or not cel_uri.startswith("at://"):
        raise ValueError("the like target is not an AT-URI — sending stops.")
    if not isinstance(cel_cid, str) or not cel_cid:
        raise ValueError("the like target has no cid — sending stops.")
    rekord = {
        "$type": "app.bsky.feed.like",
        "subject": {"uri": cel_uri, "cid": cel_cid},
        "createdAt": letrehozva,
    }
    adat = {"repo": did, "collection": "app.bsky.feed.like", "record": rekord}
    try:
        return _keres("POST", BAZIS + "com.atproto.repo.createRecord", adat,
                      token=token)
    except Exception as e:  # noqa: BLE001
        raise _biztonsagos_hiba("createRecord (like)", e) from None
