"""kimeno.py — the RULES of the outgoing run. Pure functions, no network.

The caller passes the network response in as a parameter, so every rule is
testable. Two important principles taught by the two adversarial reviews:

  * the CAP comes from the network truth (`getAuthorFeed` on our own
    account), not from a local, deletable log;
  * the RIGHT TO REPLY comes from the freshly fetched target post, not from
    the dropped-in draft and not from the inbox file.
"""

import datetime
import re
import time

import beallitasok
import szures


def ido_ts(iso_szoveg):
    """ISO-8601 → unix time. Unparseable form → None."""
    if not iso_szoveg or not isinstance(iso_szoveg, str):
        return None
    szoveg = iso_szoveg.strip().replace("Z", "+00:00")
    try:
        ido = datetime.datetime.fromisoformat(szoveg)
    except ValueError:
        return None
    if ido.tzinfo is None:
        ido = ido.replace(tzinfo=datetime.timezone.utc)
    return ido.timestamp()


def sajat_posztok_a_feedbol(feed, sajat_did):
    """Our OWN, non-repost posts from the `getAuthorFeed` response."""
    ki = []
    for elem in (feed or {}).get("feed") or []:
        if not isinstance(elem, dict):
            continue
        if elem.get("reason"):
            continue  # repost — not something we wrote
        poszt = elem.get("post") or {}
        if (poszt.get("author") or {}).get("did") != sajat_did:
            continue
        rekord = poszt.get("record") or {}
        ki.append({
            "ts": ido_ts(rekord.get("createdAt") or poszt.get("indexedAt")),
            "szoveg": rekord.get("text") or "",
            "szulo_uri": ((rekord.get("reply") or {}).get("parent")
                          or {}).get("uri") or "",
            "uri": poszt.get("uri") or "",
        })
    return ki


def ablakban(poszt, most, ablak):
    """Unknown time → it IS inside (stricter). Future time → also inside."""
    if poszt.get("ts") is None:
        return True
    return (most - poszt["ts"]) < ablak


def feed_lapozas(oldal_lekero, most, ablak=beallitasok.ABLAK_24,
                 max_oldal=beallitasok.FEED_MAX_OLDAL):
    """Page through our own feed to the END of the 24-hour window (M''3).

    `oldal_lekero(cursor)` returns a `getAuthorFeed` response. We keep paging
    until the oldest returned post falls outside the window, or until the
    cursor runs out.

    Returns `{"feed": [...], "ablak_lefedve": bool}`. If the upper limit ran
    out while we are still inside the window, `ablak_lefedve` is FALSE — the
    caller then does not post, FAIL-CLOSED, because the cap may not be
    accurate.
    """
    elemek = []
    kurzor = None
    lefedve = False
    for _ in range(max(1, int(max_oldal))):
        valasz = oldal_lekero(kurzor) or {}
        oldal = valasz.get("feed") or []
        elemek.extend(oldal)
        kurzor = valasz.get("cursor")
        if not oldal or not kurzor:
            # The feed ran out: we saw the whole history, so the window too.
            lefedve = True
            break
        # With a post of unknown time we cannot tell whether we have left the
        # window — so we keep paging (stricter direction).
        idok = [ido_ts((e.get("post") or {}).get("record", {}).get("createdAt")
                       or (e.get("post") or {}).get("indexedAt"))
                for e in oldal if isinstance(e, dict)]
        ismert = [t for t in idok if t is not None]
        if ismert and len(ismert) == len(idok) and (most - min(ismert)) >= ablak:
            lefedve = True
            break
    return {"feed": elemek, "ablak_lefedve": lefedve}


def ismeretlen_ideju_darab(sajat_posztok):
    """How many of our own posts have an unparseable time? (A''2 — visible message.)"""
    return len([p for p in sajat_posztok if p.get("ts") is None])


def plafon_ellenorzes(sajat_posztok, most, sajat_did, dedup_kulcs,
                      szulo_uri=None, kezdemenyezett=False):
    """The caps and the duplicate post. List of reasons (empty = may go)."""
    okok = []
    napon_belul = [p for p in sajat_posztok
                   if ablakban(p, most, beallitasok.ABLAK_24)]
    if len(napon_belul) >= beallitasok.NAPI_MAX:
        okok.append("napi_limit")
    # The ten-minute limit is checked ONLY against posts with a known time
    # (A''2). Previously a single own post with an unparseable `createdAt`
    # triggered the `perc_limit` reason FOREVER, so nothing would ever have
    # gone out. Posts of unknown time still count toward the 24-hour cap.
    if any(p.get("ts") is not None and ablakban(p, most, beallitasok.PERC_KOZ)
           for p in sajat_posztok):
        okok.append("perc_limit")

    if kezdemenyezett:
        # From the feed we see whether it was a reply and whose the parent
        # is; not whether it answered being addressed. So EVERY "reply to
        # someone else" counts toward the initiated cap — the stricter
        # direction.
        valasz_masnak = [
            p for p in napon_belul
            if p["szulo_uri"]
            and not p["szulo_uri"].startswith("at://" + sajat_did + "/")]
        if len(valasz_masnak) >= beallitasok.KEZDEMENYEZETT_NAPI_MAX:
            okok.append("kezdemenyezett_napi_limit")

    for p in sajat_posztok:
        if szures.dedup_kulcs(p["szoveg"]) == dedup_kulcs and \
                (p["szulo_uri"] or "") == (szulo_uri or ""):
            okok.append("dupla_poszt")
            break
    return sorted(set(okok))


def fuggo_plafon(fuggo_darab, max_darab=beallitasok.FUGGO_MAX):
    """At most `max_darab` previews awaiting approval at a time."""
    return ["fuggo_limit"] if fuggo_darab >= max_darab else []


def cel_ellenorzes(poszt, uri, sajat_did, engedelylista, poszt_lekero,
                   most=None):
    """Check the reply target ON THE NETWORK. `(cel, okok)`.

    `poszt`: the fresh state of the target (`getPosts`), None = no such post.
    `poszt_lekero(uri)`: fetch any post — also from the network.

    We take the `cid` FROM THE NETWORK, not from the draft: the draft cannot
    even contain a cid (there is no such field in the schema).

    If the right to reply does not come from a mention facet but from the post
    "having replied to our post", we fetch the PARENT too — anyone can write
    a parent uri into their own post.

    And — K''1, K'''2 — the THREAD ROOT and the full ANCESTOR CHAIN are also
    verified on the network, because they are part of the addressing too: the
    thread's participants get notified, and the post appears in their
    conversation. See `gyoker_es_lanc_ellenorzes`.

    J6/(B) — "ONGOING CONVERSATION". If under the old rule we were not
    addressed, the target post may still be the direct continuation of a
    conversation going on with us (X replied to our post, then wrote under
    their own reply). We determine this from the ancestor chain we walk anyway
    — the chain is fetched ONCE and used for both decisions, so the per-round
    network budget does not grow.
    """
    if not poszt:
        return None, ["nincs_halozati_ellenorzes"]
    most = time.time() if most is None else most
    halo_cid = poszt.get("cid") or ""
    cel, okok = szures.halozati_cel_ellenorzes(
        poszt, uri, halo_cid, sajat_did, engedelylista)

    lanc_eredmeny = None
    folyo = None
    if okok == ["halozati_nem_szolitottak_meg"] or (
            cel and cel.get("kezdemenyezett")
            and not cel.get("megszolitottak")):
        # We only look at rule (B) if under the old gate we were NOT
        # addressed: it would be either a rejection or an allowlisted
        # INITIATED reply. The chain walk does not use the draft here either.
        lanc_eredmeny = felmeno_lanc(poszt, poszt_lekero)
        folyo = folyo_beszelgetes(lanc_eredmeny[0], lanc_eredmeny[1],
                                  sajat_did, most)
        if folyo:
            cel, okok = szures.halozati_cel_ellenorzes(
                poszt, uri, halo_cid, sajat_did, engedelylista,
                folyo_beszelgetes=True)
    if okok:
        return None, okok
    if folyo and cel.get("folyo_beszelgetes"):
        # The anchor: X's post that replied DIRECTLY to our post. The
        # re-check before sending compares this too.
        cel["folyo_horgony_uri"] = folyo.get("uri") or ""

    if not cel["kezdemenyezett"] and not cel.get("folyo_beszelgetes"):
        rekord = poszt.get("record") or {}
        if not szures.emlit_e_minket(rekord, sajat_did):
            szulo_uri = ((rekord.get("reply") or {}).get("parent")
                         or {}).get("uri") or ""
            szulo = poszt_lekero(szulo_uri) if szulo_uri else None
            if not szulo:
                return None, ["szulo_poszt_nem_letezik"]
            if (szulo.get("author") or {}).get("did") != sajat_did:
                return None, ["szulo_szerzoje_nem_mi"]

    okok = gyoker_es_lanc_ellenorzes(cel, poszt, sajat_did, engedelylista,
                                     poszt_lekero, lanc_eredmeny)
    if okok:
        return None, okok
    return cel, []


# --- THE THREAD ROOT AND THE ANCESTOR CHAIN (K''1 + K'''2 + M'''1) -------
#
# We must defend against two attacks that are MIRROR IMAGES of each other:
#
#   (a) the attacker addresses us (that is legitimate), and in their OWN
#       record lies about a third party's post as `reply.root`. If we took the
#       root over blindly, our reply would land in that third party's
#       thread.
#   (b) the attacker addresses us in a REAL reply sitting under a victim's
#       post, but writes their own harmless post into the `reply.root` field.
#       Here the `parent` is TRUE, and the truth is exactly the problem: the
#       preview would show the attacker's harmless post as the root, while our
#       reply would appear in the victim's conversation.
#
# So we NEVER take the root from the draft or from anyone's say-so:
#   * the root is the `reply.root` of the TARGET POST'S OWN record fetched
#     from the network (if the target post has no `reply`, the target post
#     itself), and this root is ALSO fetched from the network (does it exist,
#     does the cid match);
#   * starting from the target post we also walk the actual ancestor chain
#     UPWARDS, fetching each step from the network, with an upper limit.
#
# The decision is based on the participants of the chain:
#   * if EVERY author in the chain is us / the addressing party /
#     allowlisted → normal reply;
#   * if there is a STRANGER third party in it → not a rejection, but an
#     INITIATED classification (falls under the daily cap of two), and the
#     preview states in CAPITALS whose thread the reply lands in, and that a
#     third party may also get notified. The decision is the owner's;
#   * if the chain cannot be walked, the limit runs out, or anything does not
#     match → REJECTION.


def _poszt_adatai(poszt):
    """SANITIZED summary of a `getPosts` element, as used in the chain."""
    rekord = (poszt or {}).get("record") or {}
    valasz = rekord.get("reply") or {}
    szerzo = (poszt or {}).get("author") or {}
    return {
        "uri": (poszt or {}).get("uri") or "",
        "cid": (poszt or {}).get("cid") or "",
        "szerzo_did": szerzo.get("did") or "",
        "szerzo_handle": szures.egysoros(szerzo.get("handle", ""), 120),
        "szoveg_roviden": szures.keret_veszelytelenites(
            szures.szanitalas(rekord.get("text", ""), 300)),
        "szulo_uri": (valasz.get("parent") or {}).get("uri") or "",
        "van_valasz": bool(valasz),
        # J6/(B): the post's NETWORK timestamps. `createdAt` is the author's
        # own claim, `indexedAt` is the service's — the ongoing-conversation
        # rule requires both to be inside the window, if present.
        "letrejott_ts": ido_ts(rekord.get("createdAt")),
        "indexelt_ts": ido_ts((poszt or {}).get("indexedAt")),
        "kert_gyoker_uri": (valasz.get("root") or {}).get("uri") or "",
        "kert_gyoker_cid": (valasz.get("root") or {}).get("cid") or "",
    }


def felmeno_lanc(szulo_poszt, poszt_lekero,
                 max_lepes=beallitasok.FELMENO_MAX_LEPES):
    """From the target post UPWARDS to the top of the thread. `(lanc, okok)`.

    `lanc[0]` is the target post itself, `lanc[-1]` is the top of the thread.
    Every step is fetched FROM THE NETWORK: the `reply.parent` field is
    self-reported, but the existence and uri of the fetched post are not.

    If the chain cannot be walked (non-existent ancestor, bad shape, a cycle,
    or the upper limit runs out), the reason list is not empty — the caller
    then REJECTS.
    """
    lanc = [_poszt_adatai(szulo_poszt)]
    latott = {lanc[0]["uri"]}
    for _ in range(max(1, int(max_lepes))):
        kovetkezo = lanc[-1]["szulo_uri"]
        if not kovetkezo:
            return lanc, []          # we reached the top of the thread
        tiszta = szures.uri_veszelytelenites(kovetkezo)
        if not tiszta or tiszta != kovetkezo.strip():
            return lanc, ["felmeno_alakja_rossz"]
        if tiszta in latott:
            return lanc, ["felmeno_lanc_korben_jar"]
        poszt = poszt_lekero(tiszta)
        if not poszt:
            return lanc, ["felmeno_nem_igazolhato"]
        adat = _poszt_adatai(poszt)
        if adat["uri"] != tiszta:
            return lanc, ["felmeno_uri_elter"]
        if not adat["szerzo_did"]:
            return lanc, ["felmeno_szerzoje_ismeretlen"]
        latott.add(tiszta)
        lanc.append(adat)
    if lanc[-1]["szulo_uri"]:
        # We did not reach the top within the allowed number of steps: there
        # is an ancestor we did not check — fail-closed.
        return lanc, ["felmeno_lanc_tul_hosszu"]
    return lanc, []


def _ido_ablakban(adat, most, ablak,
                  jovo_tures=beallitasok.IDO_JOVOBELI_TURES):
    """J6/(B) condition 2: is the post at most `ablak` old?

    Fail-closed: if there is NOT A SINGLE network timestamp, the rule is not
    met. If both exist, BOTH must be inside the window — the older one
    decides. A future timestamp is not accepted either: the author writes the
    `createdAt` field, so it must not be usable to hold the gate open at will.
    """
    idok = [t for t in (adat.get("letrejott_ts"), adat.get("indexelt_ts"))
            if t is not None]
    if not idok:
        return False
    for t in idok:
        if t > most + jovo_tures:
            return False
        if (most - t) >= ablak:
            return False
    return True


def folyo_beszelgetes(lanc, lanc_okok, sajat_did, most,
                      ablak=beallitasok.FOLYO_BESZELGETES_ABLAK):
    """J6/(B) — is the target post the direct continuation of an ALREADY
    ONGOING conversation?

    The real case this rule covers: we write a post, X replies to it, then X
    writes one more under their OWN reply (e.g. a picture) without tagging
    us. Answering that is not an initiation but the middle of a conversation.

    Every condition is determined from the `lanc` walked ON THE NETWORK
    (`lanc[0]` is the target post, `lanc[-1]` the top of the thread); the
    draft plays no role here. Returns: the data of the ANCHOR post (X's post
    that replied directly to our post), or None.

    The rule is narrow and fail-closed — uncertainty in any condition gives
    None:
      1. the chain walk succeeded (`lanc_okok` is empty);
      2. the target post's author is X (not us), and from the target post
         upwards ONLY X's posts stand until the anchor;
      3. the anchor's parent is OUR post;
      4. the anchor is at most 24 hours old;
      5. nobody but us and X is in the whole chain.
    Because of point 5, X cannot "open a gate" in ANOTHER thread: the rule
    applies only to the direct continuation of the same chain.
    """
    if lanc_okok:
        return None
    if not lanc or len(lanc) < 2:
        return None
    x_did = lanc[0].get("szerzo_did") or ""
    if not x_did or x_did == sajat_did:
        return None
    # Condition 5: only we and X may appear in the chain. An allowlisted
    # third party does not open this path either — the existing behaviour
    # stays there.
    for tag in lanc:
        did = tag.get("szerzo_did") or ""
        if did != x_did and did != sajat_did:
            return None
    # Conditions 2–3: find the anchor by moving up the chain.
    horgony = None
    for i, tag in enumerate(lanc):
        if (tag.get("szerzo_did") or "") != x_did:
            return None          # someone else got in between X's posts
        if i + 1 >= len(lanc):
            return None          # X is the thread top: we did not start the thread
        if (lanc[i + 1].get("szerzo_did") or "") == sajat_did:
            horgony = tag
            break
    if horgony is None:
        return None
    # Condition 4: time. If it cannot be determined, the rule is NOT met.
    if not _ido_ablakban(horgony, most, ablak):
        return None
    return horgony


def _sajat_korunk_e(did, sajat_did, szulo_did, engedelylista):
    """Is a participant of the chain "not a stranger"?

    Not a stranger: we ourselves, the party ADDRESSING us (the target post's
    author), and the allowlisted accounts.

    Since Kö''''5, `gyoker_es_lanc_ellenorzes` checks these three cases
    SEPARATELY, because it must name the allowlisted party separately in the
    preview. This function remains the READABLE summary of the rule, and the
    tests call it too.
    """
    if not did:
        return False
    if did == sajat_did:
        return True
    if szulo_did and did == szulo_did:
        return True
    return szures.engedelylistas_did_e(did, engedelylista or [])


def gyoker_es_lanc_ellenorzes(cel, szulo_poszt, sajat_did, engedelylista,
                              poszt_lekero, lanc_eredmeny=None):
    """Verify the thread root AND walk the ancestor chain. List of reasons.

    Empty list = may go. The `cel` dict is extended in place with the root and
    chain data — the preview prints these, and the re-check after approval
    compares them with freshly fetched ones.
    """
    szulo = _poszt_adatai(szulo_poszt)
    szulo_uri = cel.get("uri") or ""
    szulo_cid = cel.get("cid") or ""
    szulo_did = cel.get("szerzo_did") or ""

    # --- 1. THE ROOT: the `reply.root` of the target post's OWN record ------
    if not szulo["van_valasz"] or not (szulo["kert_gyoker_uri"]
                                       or szulo["kert_gyoker_cid"]):
        # The target post has no `reply` block: it is itself the thread root.
        cel["gyoker_uri"] = szulo_uri
        cel["gyoker_cid"] = szulo_cid
        cel["gyoker_igazolva"] = True
        cel["gyoker_kulon"] = False
        cel["gyoker_szerzo_did"] = szures.egysoros(szulo_did, 120)
        cel["gyoker_szerzo_handle"] = cel.get("szerzo_handle") or ""
        cel["gyoker_szoveg_roviden"] = cel.get("szoveg_roviden") or ""
    else:
        kert_uri = szures.uri_veszelytelenites(szulo["kert_gyoker_uri"])
        kert_cid = szures.cid_veszelytelenites(szulo["kert_gyoker_cid"])
        if not kert_uri or not kert_cid:
            return ["gyoker_alakja_rossz"]
        if kert_uri == szulo_uri:
            # The root is the target post itself; the cid must match too,
            # otherwise the record contradicts itself.
            if kert_cid != szulo_cid:
                return ["gyoker_cid_elter"]
            cel["gyoker_uri"] = szulo_uri
            cel["gyoker_cid"] = szulo_cid
            cel["gyoker_igazolva"] = True
            cel["gyoker_kulon"] = False
            cel["gyoker_szerzo_did"] = szures.egysoros(szulo_did, 120)
            cel["gyoker_szerzo_handle"] = cel.get("szerzo_handle") or ""
            cel["gyoker_szoveg_roviden"] = cel.get("szoveg_roviden") or ""
        else:
            gyoker = poszt_lekero(kert_uri)
            if not gyoker:
                return ["gyoker_nem_igazolhato"]
            gyoker_adat = _poszt_adatai(gyoker)
            if gyoker_adat["uri"] != kert_uri:
                return ["gyoker_uri_elter"]
            if gyoker_adat["cid"] != kert_cid:
                return ["gyoker_cid_elter"]
            if not gyoker_adat["szerzo_did"]:
                return ["gyoker_szerzoje_ismeretlen"]
            cel["gyoker_uri"] = kert_uri
            cel["gyoker_cid"] = kert_cid
            cel["gyoker_igazolva"] = True
            cel["gyoker_kulon"] = True
            cel["gyoker_szerzo_did"] = szures.egysoros(
                gyoker_adat["szerzo_did"], 120)
            cel["gyoker_szerzo_handle"] = gyoker_adat["szerzo_handle"]
            cel["gyoker_szoveg_roviden"] = gyoker_adat["szoveg_roviden"]

    # --- 2. THE ACTUAL ANCESTOR CHAIN -------------------------------------
    # `lanc_eredmeny` is the chain walk the caller already did (J6/(B)). If we
    # have it, we do NOT fetch it AGAIN: so the per-round network budget does
    # not grow.
    if lanc_eredmeny is None:
        lanc_eredmeny = felmeno_lanc(szulo_poszt, poszt_lekero)
    lanc, okok = lanc_eredmeny
    if okok:
        return okok

    teteje = lanc[-1]
    cel["lanc_uri_lista"] = [t["uri"] for t in lanc]
    cel["lanc_hossz"] = len(lanc)
    cel["lanc_teteje_uri"] = teteje["uri"]
    cel["lanc_teteje_handle"] = teteje["szerzo_handle"]
    cel["lanc_teteje_did"] = szures.egysoros(teteje["szerzo_did"], 120)
    cel["lanc_teteje_szoveg"] = teteje["szoveg_roviden"]
    # A mismatch between the claimed root and the ACTUAL thread top deserves
    # a warning in itself: in that case the record's `reply.root` field does
    # not say where the reply actually lands.
    cel["gyoker_es_lanc_elter"] = bool(
        cel.get("gyoker_uri") and teteje["uri"]
        and cel["gyoker_uri"] != teteje["uri"])

    # --- 3. A STRANGER THIRD PARTY IN THE CHAIN? --------------------------
    #
    # Kö''''5: an ALLOWLISTED third party is not "non-existent" either. We do
    # not count them among the strangers (a deliberate design decision: the
    # allowlist lives in the `program\` folder, which the untrusted side
    # cannot write), but we NAME them in the preview, under a separate label.
    # Previously the preview claimed in this case that "there is NO stranger
    # third party in it" — which was true, but gave the impression that
    # nobody besides us was in the thread.
    idegenek = []
    engedelylistasok = []
    latott_did = set()
    for szereplo in lanc + [{
            "szerzo_did": (cel.get("gyoker_szerzo_did") or ""),
            "szerzo_handle": (cel.get("gyoker_szerzo_handle") or "")}]:
        did = szereplo.get("szerzo_did") or ""
        if not did or did in latott_did:
            continue
        latott_did.add(did)
        if did == sajat_did or (szulo_did and did == szulo_did):
            continue  # we ourselves, or the party addressing us
        if szures.engedelylistas_did_e(did, engedelylista or []):
            engedelylistasok.append(
                {"did": szures.egysoros(did, 120),
                 "handle": szereplo.get("szerzo_handle") or ""})
            continue
        idegenek.append({"did": szures.egysoros(did, 120),
                         "handle": szereplo.get("szerzo_handle") or ""})
    cel["idegen_szereplok"] = idegenek
    cel["idegen_szalban"] = bool(idegenek)
    cel["engedelylistas_szereplok"] = engedelylistasok
    if idegenek and cel.get("megszolitottak"):
        # If we were addressed (mention, a reply to our post, or an ongoing
        # conversation), the other party is talking TO US even if the thread
        # branches higher up from a stranger's post. This is not an initiated
        # reply, so it does not fall under the narrower daily cap. The
        # STRANGER THIRD PARTY warning still goes out UNCHANGED in the
        # preview (`idegen_szereplok`). Real case: 09-26/27, a thread that a
        # stranger had also joined on 09-23 — the guard applied the cap of
        # two even to a reply addressed TO US.
        cel["idegen_szalban_megszolitva"] = True
    elif idegenek:
        # NOT a rejection: this is an everyday case on Bluesky (someone speaks
        # up in a third party's thread but did not address us — an
        # allowlisted initiation). It falls under the narrower initiated cap,
        # and the preview warns in CAPITALS.
        cel["kezdemenyezett"] = True
        okok_listaja = list(cel.get("kezdemenyezett_oka") or [])
        if szures.OK_IDEGEN_A_SZALBAN not in okok_listaja:
            okok_listaja.append(szures.OK_IDEGEN_A_SZALBAN)
        cel["kezdemenyezett_oka"] = okok_listaja
    return []


def szoveg_szabalyok(szoveg, mintak):
    """Length + block filter + BYTE-EXACT PREVIEW. List of reasons (empty = may go).

    The reasons contain ONLY pattern names — never the matched text.
    """
    okok = list(szures.hossz_ellenorzes(szoveg))
    okok += ["tilto:" + o for o in szures.tilto_ellenorzes(szoveg, mintak)]
    okok += megjelenites_szabalyok(szoveg)
    # M'''''2 — QUANTITY limit on the invisible characters LET THROUGH by
    # sanitization (ZWJ, variation selector) and on combining marks.
    # Sanitization does not rewrite these, so the byte-exactness gate does
    # not catch them either.
    okok += szures.rejtett_csatorna_ellenorzes(szoveg)
    # Text imitating the approval formula is a separate rejection: in the
    # preview it could pass for the real approval line.
    if "jovahagyo_formula" in szures.injection_jelolek(szoveg):
        okok.append("jovahagyo_formula_utanzas")
    if _jovahagyo_sor_utanzas(szoveg):
        okok.append("jovahagyo_formula_utanzas")
    return sorted(set(okok))


def megjelenites_szabalyok(szoveg):
    """M''2: the preview must be BYTE-EXACT — list of reasons (empty = may go).

    The preview is the ONLY place where a human eye decides. The
    frame-protecting filters (`szanitalas`, `keret_veszelytelenites`),
    however, REWRITE the displayed text: `==` becomes `[=]`, the spaces of
    "külső adat" become middle dots, control and zero-width characters
    disappear. In that case the owner would not see exactly what goes out.

    The decision (not prettifying, but rejection): if the outgoing text
    contains a character or pattern that would have to be rewritten for
    display, we REJECT the draft, with a reason. So the filter will never
    change anything, and the preview shows exactly the canonical text.
    """
    szoveg = szoveg or ""
    okok = []
    # 1. Control, zero-width, bidi (right-to-left) and format characters, and
    #    differences from Unicode normalization.
    if szures.szanitalas(szoveg, len(szoveg) + 1) != szoveg:
        okok.append("megjelenites_atirna:vezerlo_vagy_normalizalando_karakter")
    # 2. Frame imitation: two or more equals signs, or the "külső adat"
    #    ("external data") phrase — these could break the hard frame.
    if szures.keret_veszelytelenites(szoveg) != szoveg:
        okok.append("megjelenites_atirna:keret_utanzat")
    return okok


# Hungarian language data: fragments of the fixed Hungarian approval line
# shown in the Telegram preview. If the approval line (elonezet.py / the
# Telegram texts) is translated or changed, update these patterns to match
# the new wording, otherwise imitations of it are no longer caught.
_JOVAHAGYO_SOR = re.compile(
    r"j[óo]v[áa]hagy[áa]s\s*:"          # "Jóváhagyás:" ("Approval:")
    r"|v[áa]laszolj\s+erre"             # "válaszolj ERRE az üzenetre" ("reply to THIS message")
    r"|elvet[ée]s\s*:"                  # "Elvetés: NEM" ("Discard: NO")
    r"|approval\s*:|reply\s+to\s+this|rejection\s*:|discard\s*:",   # the English line
    re.IGNORECASE)


def _jovahagyo_sor_utanzas(szoveg):
    """Imitation of the fixed approval line in the outgoing text.

    The owner sees ONE fixed line at the bottom of the preview. If the
    outgoing text imitates it, it could trick out an approval meant for a
    different item — hence REJECTION. The check runs on the
    homoglyph-normalized form.
    """
    alap = szures.osszehasonlito_alak(szoveg or "")
    return bool(_JOVAHAGYO_SOR.search(alap))


# =========================================================================
# FOLLOW and LIKE (2026-10-02)
# =========================================================================
#
# Two operations WITHOUT TEXT. There is no outgoing text, so the content
# filter (`szoveg_szabalyok`) does not apply to them. What does apply:
#   * the TARGET is freshly verified on the network (`getProfile` /
#     `getPosts`) — only the address comes from the draft, the DID and the
#     cid come from the network;
#   * the CAP comes from the network truth (`listRecords` on our own repo);

AKCIO_TIPUSOK = ("kovetes", "kedveles")
GYUJTEMENY = {
    "kovetes": "app.bsky.graph.follow",
    "kedveles": "app.bsky.feed.like",
}
PLAFON_OK = {
    "kovetes": "kovetes_napi_limit",
    "kedveles": "kedveles_napi_limit",
}
_DID_ALAK = re.compile(r"^did:[a-z]+:[A-Za-z0-9._:%-]*[A-Za-z0-9._-]$")


def akcio_plafon_max(tipus):
    return {"kovetes": beallitasok.KOVETES_NAPI_MAX,
            "kedveles": beallitasok.KEDVELES_NAPI_MAX}[tipus]


def akcio_plafon(rekord_valasz, most, tipus, ablak=beallitasok.ABLAK_24):
    """The follow/like cap from the `listRecords` response. `(darab, okok)`.

    `darab`: how many of our own records fall into the rolling 24-hour
    window. A record with an unparseable (or future) time COUNTS — the
    stricter direction, as with the post cap. If the page is full (there is a
    cursor) and ALL its records are inside the window, we have not seen the
    full count: fail-closed, cap reached.
    """
    maximum = akcio_plafon_max(tipus)
    rekordok = [r for r in ((rekord_valasz or {}).get("records") or [])
                if isinstance(r, dict)]
    darab = 0
    for r in rekordok:
        ts = ido_ts((r.get("value") or {}).get("createdAt"))
        if ts is None or (most - ts) < ablak:
            darab += 1
    okok = []
    if darab >= maximum:
        okok.append(PLAFON_OK[tipus])
    elif rekordok and darab == len(rekordok) and \
            (rekord_valasz or {}).get("cursor"):
        okok.append(PLAFON_OK[tipus])
    return darab, okok


def kovetes_cel_ellenorzes(profil, kert_handle, kert_did, sajat_did):
    """Check the profile to follow ON THE NETWORK. `(cel, okok)`.

    `profil`: the fresh `getProfile` response, `None` = no such profile.
    The follow targets the DID (the handle can change); we take the DID from
    the network, and if the draft gave a DID or handle, it must match what
    the network says.
    """
    if not profil or not isinstance(profil, dict):
        return None, ["nincs_ilyen_profil"]
    did = profil.get("did") or ""
    if not isinstance(did, str) or not _DID_ALAK.match(did) or len(did) > 200:
        return None, ["profil_did_ervenytelen"]
    handle = profil.get("handle") or ""
    if not isinstance(handle, str):
        handle = ""
    if kert_did and did != kert_did:
        return None, ["profil_did_elter"]
    if kert_handle and handle.lower() != kert_handle.lower():
        return None, ["profil_handle_elter"]
    if did == sajat_did:
        return None, ["sajat_fiok"]
    viewer = profil.get("viewer") or {}
    if not isinstance(viewer, dict):
        viewer = {}
    if viewer.get("blockedBy") or viewer.get("blocking"):
        return None, ["letiltas_miatt_nem"]
    if viewer.get("following"):
        return None, ["mar_kovetve"]

    def _szam(ertek):
        return ertek if isinstance(ertek, int) and \
            not isinstance(ertek, bool) and ertek >= 0 else None

    return {
        "did": szures.egysoros(did, 200),
        "handle": szures.egysoros(handle, 120),
        "megjelenitett_nev": szures.egysoros(profil.get("displayName") or "",
                                             64),
        "leiras_roviden": szures.keret_veszelytelenites(
            szures.szanitalas(profil.get("description") or "", 300)),
        "kovetok": _szam(profil.get("followersCount")),
        "posztok": _szam(profil.get("postsCount")),
    }, []


def kedveles_cel_ellenorzes(poszt, uri, sajat_did):
    """Check the post to like ON THE NETWORK. `(cel, okok)`.

    `poszt`: the fresh `getPosts` element, `None` = no such post. We take the
    `cid` FROM THE NETWORK (the draft has no such field at all), and compare
    it again before sending: if the post changed in the meantime, we do not
    like it.
    """
    if not poszt or not isinstance(poszt, dict):
        return None, ["nincs_ilyen_poszt"]
    if (poszt.get("uri") or "") != uri:
        return None, ["poszt_uri_elter"]
    cid = szures.cid_veszelytelenites(poszt.get("cid") or "")
    if not cid:
        return None, ["poszt_cid_ervenytelen"]
    szerzo = poszt.get("author") or {}
    szerzo_did = szerzo.get("did") or ""
    if not isinstance(szerzo_did, str) or not szerzo_did or \
            not uri.startswith("at://" + szerzo_did + "/"):
        return None, ["poszt_szerzo_elter"]
    if szerzo_did == sajat_did:
        return None, ["sajat_poszt"]
    viewer = poszt.get("viewer") or {}
    if not isinstance(viewer, dict):
        viewer = {}
    if viewer.get("like"):
        return None, ["mar_kedvelve"]
    rekord = poszt.get("record") or {}
    return {
        "uri": uri,
        "cid": cid,
        "szerzo_did": szures.egysoros(szerzo_did, 200),
        "szerzo_handle": szures.egysoros(szerzo.get("handle") or "", 120),
        "szoveg_roviden": szures.keret_veszelytelenites(
            szures.szanitalas(rekord.get("text") or "", 300)),
    }, []


def akcio_kanonikus(tipus, cel):
    """The hashed canonical record to be approved. The follow/like record
    that is sent out is built FROM THIS — and only from this."""
    if tipus == "kovetes":
        return {"tipus": "kovetes", "cel_did": cel.get("did") or "",
                "cel_handle": cel.get("handle") or ""}
    return {"tipus": "kedveles", "cel_uri": cel.get("uri") or "",
            "cel_cid": cel.get("cid") or ""}


def akcio_kanonikus_ervenyes(kanonikus):
    """Structural check of the canonical follow/like record. List of reasons."""
    tipus = kanonikus.get("tipus")
    if tipus == "kovetes":
        if set(kanonikus) != {"tipus", "cel_did", "cel_handle"}:
            return ["kanonikus_mezok_hibasak"]
        if not _DID_ALAK.match(kanonikus.get("cel_did") or ""):
            return ["rossz_did_alak"]
        return []
    if tipus == "kedveles":
        if set(kanonikus) != {"tipus", "cel_uri", "cel_cid"}:
            return ["kanonikus_mezok_hibasak"]
        if not szures.uri_veszelytelenites(kanonikus.get("cel_uri") or ""):
            return ["rossz_uri_alak"]
        if not szures.cid_veszelytelenites(kanonikus.get("cel_cid") or ""):
            return ["rossz_cid_alak"]
        return []
    return ["ismeretlen_tipus"]


def akcio_azonos_cel(kanonikus_a, kanonikus_b):
    """Do two canonical follow/like records target the SAME thing?"""
    if (kanonikus_a or {}).get("tipus") != (kanonikus_b or {}).get("tipus"):
        return False
    if kanonikus_a.get("tipus") == "kovetes":
        return bool(kanonikus_a.get("cel_did")) and \
            kanonikus_a.get("cel_did") == kanonikus_b.get("cel_did")
    if kanonikus_a.get("tipus") == "kedveles":
        return bool(kanonikus_a.get("cel_uri")) and \
            kanonikus_a.get("cel_uri") == kanonikus_b.get("cel_uri")
    return False
