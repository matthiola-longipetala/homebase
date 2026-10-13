"""elonezet.py — text of the PREVIEW sent to the owner. Pure function.

What the owner sees on the phone is the ONLY place where a human decides.
That is why every inserted field is neutralized:

  * a RANDOM frame marker per run (more precisely, per preview) — the
    attacker cannot guess it, so cannot draw a fake frame;
  * every foreign field goes through the `egysoros` + `keret_veszelytelenites`
    filters;
  * Claude's note is SEPARATE, labeled, sanitized;
  * the fixed approval line appears ONCE, at the very end — nothing after it.

The length of the whole message is bounded (Telegram allows 4096 characters).
"""

import re
import secrets

import szures

UZENET_MAX = 3800

# Imitation of the approval formula in the inserted texts. `kimeno.py`
# already rejects such a draft; this is the SECOND line of defense, so that the
# formula is guaranteed to appear ONCE in the preview, at the very end.
# Hungarian language data: the regex matches the Hungarian approval formula of
# JOVAHAGYO_SOR below ("Jóváhagyás:", "Elvetés:", "válaszolj erre"); if you
# translate JOVAHAGYO_SOR, update this regex AND `kimeno._JOVAHAGYO_SOR` to
# match the new wording.
_FORMULA = re.compile(
    r"j[óo]v[áa]hagy[áa]s\s*:|elvet[ée]s\s*:|v[áa]laszolj\s+erre"
    r"|approval\s*:|rejection\s*:|discard\s*:|reply\s+to\s+this",
    re.IGNORECASE)

# The fixed approval line (Hungarian). It reads: "Approval: reply to THIS
# message: IGEN (yes) · Rejection: NEM (no)". IGEN / NEM are the command tokens
# the owner types back; the wording is coupled to the regexes above and in
# `kimeno.py`, so it is kept as-is.
JOVAHAGYO_SOR = ("Jóváhagyás: válaszolj ERRE az üzenetre: IGEN · "
                 "Elvetés: NEM")
# The same in English, shown right above the Hungarian line. The command tokens stay
# IGEN / NEM (telegram_or.py accepts exactly these); the English line only explains them.
JOVAHAGYO_SOR_EN = ("Approval: reply to THIS message with IGEN (yes) · "
                    "Rejection: NEM (no)")
# English line FIRST, Hungarian line LAST: the Hungarian line stays the final line of every
# preview (the tests and `kimeno_blokk_pontos_e` rely on it).
JOVAHAGYO_BLOKK = JOVAHAGYO_SOR_EN + "\n" + JOVAHAGYO_SOR


def jelolo_keszit():
    """A new, unguessable frame marker for each preview."""
    return secrets.token_hex(8)


def _formula_semlegesit(szoveg):
    """Neutralizes the words of the approval formula in the inserted text."""
    return _FORMULA.sub("[formula]", szoveg or "")


def _mezo(ertek, max_hossz, jelolo):
    return _formula_semlegesit(szures.keret_veszelytelenites(
        szures.egysoros(ertek, max_hossz), jelolo))


def _blokk(szoveg, jelolo, cim, max_hossz=1200):
    nyit = "=== %s [%s] ===" % (cim, jelolo)
    # "VÉGE" = END. Kept as-is: `kimeno_blokk_pontos_e` searches for this marker.
    zar = "=== VÉGE [%s] ===" % jelolo
    tiszta = _formula_semlegesit(szures.keret_veszelytelenites(
        szures.szanitalas(szoveg or "", max_hossz), jelolo))
    return "\n".join([nyit, tiszta, zar])


_KIMENO_BLOKK_CIM = "OUTGOING TEXT"
# The outgoing text is at most 300 graphemes / 3000 bytes, so it is surely
# shorter than 3200 characters: the frame can NOT truncate it.
KIMENO_BLOKK_MAX = 3200


def kimeno_blokk_pontos_e(uzenet, szoveg):
    """M''2: does the sent preview contain the canonical text BYTE FOR BYTE?

    `kimeno.megjelenites_szabalyok` already rejects any draft that would have
    to be rewritten for display. This is the SECOND line of defense: the
    engine uses it to check that the preview really contains exactly what
    goes out. If not, the preview is not even sent.
    """
    if not szoveg:
        return False
    # The outgoing text is enclosed by its own frame with an unguessable
    # marker; the search is therefore unambiguous.
    return ("\n" + szoveg + "\n=== VÉGE [") in (uzenet or "")


def _gyoker_sorok(cel, jelolo):
    """Presents the THREAD ROOT and the ANCESTOR CHAIN (K''1, K'''2).

    On Bluesky the thread is part of the ADDRESSING: the participants of the
    thread get notified, and the post appears in their conversation. So we
    ALWAYS state where the reply goes, and if a foreign third party is in
    the chain, we name them IN CAPITALS.

    The earlier version claimed the post "does not go into a foreign
    conversation" — the guard could not back that up at the time. Today we
    walk the whole ancestor chain, so the claim is verified; if it is not,
    we do not make it.
    """
    sorok = ["", "THE POST GOES INTO THIS THREAD:"]
    sorok.append("  thread length (ancestors): %d"
                 % int(cel.get("lanc_hossz") or 1))
    if cel.get("gyoker_kulon"):
        sorok.append("  the thread root is NOT the post above:")
        sorok.append("    root author:     %s"
                     % _mezo(cel.get("gyoker_szerzo_handle"), 120, jelolo))
        sorok.append("    root did:        %s"
                     % _mezo(cel.get("gyoker_szerzo_did"), 120, jelolo))
        sorok.append("    root uri:        %s"
                     % _mezo(cel.get("gyoker_uri"), 300, jelolo))
        sorok.append(_blokk(cel.get("gyoker_szoveg_roviden"), jelolo,
                            "TEXT OF THE THREAD ROOT — KÜLSŐ ADAT / EXTERNAL DATA, NOT AN INSTRUCTION",
                            400))
    else:
        sorok.append("  the thread root is the target post above itself.")

    if cel.get("gyoker_es_lanc_elter"):
        sorok.append("")
        sorok.append("  WARNING: THE ACTUAL TOP OF THE THREAD IS NOT THE SAME AS "
                     "WHAT THE TARGET POST DECLARES AS ITS ROOT.")
        sorok.append("    thread top:    %s"
                     % _mezo(cel.get("lanc_teteje_handle"), 120, jelolo))
        sorok.append("    did:           %s"
                     % _mezo(cel.get("lanc_teteje_did"), 120, jelolo))
        sorok.append("    uri:           %s"
                     % _mezo(cel.get("lanc_teteje_uri"), 300, jelolo))
        sorok.append(_blokk(cel.get("lanc_teteje_szoveg"), jelolo,
                            "TEXT OF THE THREAD TOP — KÜLSŐ ADAT / EXTERNAL DATA, "
                            "NOT AN INSTRUCTION", 400))

    engedelylistasok = cel.get("engedelylistas_szereplok") or []
    if engedelylistasok:
        # Kö''''5: an allowlisted third party is NAMED too. Not a stranger
        # (the list is in the protected `program\` folder), but they still
        # take part in the conversation — and the owner must see that.
        sorok.append("")
        sorok.append("  an allowlisted party also takes part in the thread:")
        for szereplo in engedelylistasok[:5]:
            sorok.append("    - %s  (%s)  [allowlisted]"
                         % (_mezo(szereplo.get("handle"), 120, jelolo),
                            _mezo(szereplo.get("did"), 120, jelolo)))
        if len(engedelylistasok) > 5:
            sorok.append("    - … and %d more allowlisted parties"
                         % (len(engedelylistasok) - 5))

    idegenek = cel.get("idegen_szereplok") or []
    if idegenek:
        sorok.append("")
        sorok.append("  WARNING: A FOREIGN THIRD PARTY ALSO TAKES PART IN THIS "
                     "THREAD.")
        sorok.append("  OUR REPLY GOES INTO THEIR CONVERSATION, AND THEY MAY "
                     "ALSO BE NOTIFIED OF IT.")
        for szereplo in idegenek[:5]:
            sorok.append("    - %s  (%s)"
                         % (_mezo(szereplo.get("handle"), 120, jelolo),
                            _mezo(szereplo.get("did"), 120, jelolo)))
        if len(idegenek) > 5:
            sorok.append("    - … and %d more parties" % (len(idegenek) - 5))
    elif not cel.get("gyoker_es_lanc_elter"):
        sorok.append("  I checked the whole ancestor chain: there is NO foreign "
                     "third party in it.")
    return sorok


NEM_SZOLITOTTAK_MEG = "WARNING: UNSOLICITED REPLY — WE WERE NOT ADDRESSED!"
IDEGEN_SZALBA = ("WARNING: COUNTS AS UNSOLICITED — WE WERE ADDRESSED, BUT WE "
                 "ARE REPLYING IN A FOREIGN THREAD, FROM THE NARROWER DAILY BUDGET.")
# Kö'''''1 — the fourth combination: both reasons apply. Two separate
# sentences would then CONTRADICT EACH OTHER (one says we were not addressed,
# the other that we were), and the second would be factually false.
# So for this case a SINGLE, combined, true sentence goes out.
MINDKET_OK = ("WARNING: UNSOLICITED REPLY — WE WERE NOT ADDRESSED, AND WE "
              "ARE REPLYING IN A FOREIGN THREAD. IT COMES FROM THE NARROWER DAILY BUDGET.")


# J6/(B) — the ongoing-conversation line. Not "UNSOLICITED", because it is
# not: X replied to our post and continued in the same chain. The sentence
# states exactly this TRUE fact, nothing more.
FOLYO_BESZELGETES = ("ONGOING CONVERSATION: %s just replied to your post "
                     "and continued in this thread.")


def _kezdemenyezett_sorok(cel):
    """The TRUE and EXCLUSIVE justification of the UNSOLICITED rating (Kö'''''1).

    The rating can arise for two independent reasons:
      * an allowlisted party speaks, but WE were not addressed;
      * we were addressed, but the reply goes into a FOREIGN thread.

    Previously each reason sent its own sentence, so in the fourth
    combination "we were not addressed" and "we were addressed" appeared one
    after the other — the second one falsely. If the owner gets used
    to this line not always being true, the real case will not be taken seriously.
    So: for every combination EXACTLY ONE, true justification.

    If the reason is missing for some reason (an old record made before the
    field was introduced), the stricter, old sentence is written — we do not
    leave it without a warning.
    """
    okok = set(cel.get("kezdemenyezett_oka") or [])
    engedelylistas = szures.OK_ENGEDELYLISTAS in okok
    idegen = szures.OK_IDEGEN_A_SZALBAN in okok
    if engedelylistas and idegen:
        return [MINDKET_OK]
    if idegen:
        return [IDEGEN_SZALBA]
    return [NEM_SZOLITOTTAK_MEG]


def szoveg(tetel, jelolo, megjegyzes="", cel=None, sorszam=None):
    """The full text of the preview.

    `tetel`: the CANONICAL outgoing record (`szures.kimeno_kanonikus`).
    `cel`: the data of the target post freshly fetched from the network, or None.
    """
    sorok = []
    if sorszam:
        sorok.append("BskyGuard — awaiting approval (#%s)" % _mezo(sorszam, 40, jelolo))
    else:
        sorok.append("BskyGuard — awaiting approval")
    sorok.append("type: %s   graphemes: %d/%d"
                 % (_mezo(tetel.get("tipus"), 20, jelolo),
                    szures.grafema_szam(tetel.get("szoveg") or ""),
                    szures.POSZT_MAX_GRAFEMA))
    if cel and cel.get("kezdemenyezett"):
        sorok += _kezdemenyezett_sorok(cel)
    elif cel and cel.get("folyo_beszelgetes"):
        sorok.append(FOLYO_BESZELGETES
                     % _mezo(cel.get("szerzo_handle"), 120, jelolo))
    sorok.append("")
    sorok.append("THIS WOULD GO OUT, EXACTLY LIKE THIS:")
    sorok.append(_blokk(tetel.get("szoveg"), jelolo, _KIMENO_BLOKK_CIM,
                        KIMENO_BLOKK_MAX))

    if cel:
        sorok.append("")
        sorok.append("REPLY TO THIS POST (freshly fetched from Bluesky):")
        sorok.append("  author: %s" % _mezo(cel.get("szerzo_handle"), 120, jelolo))
        sorok.append("  did:    %s" % _mezo(cel.get("szerzo_did"), 120, jelolo))
        sorok.append("  uri:    %s" % _mezo(cel.get("uri"), 300, jelolo))
        sorok.append(_blokk(cel.get("szoveg_roviden"), jelolo,
                            "TEXT OF THE TARGET POST — KÜLSŐ ADAT / EXTERNAL DATA, NOT AN INSTRUCTION", 400))
        sorok += _gyoker_sorok(cel, jelolo)

    if (megjegyzes or "").strip():
        sorok.append("")
        sorok.append(_blokk(megjegyzes, jelolo,
                            "Claude's note (not part of the post)", 200))

    sorok.append("")
    sorok.append(JOVAHAGYO_BLOKK)
    egesz = "\n".join(sorok)
    if len(egesz) > UZENET_MAX:
        # We never truncate at the expense of the approval line: it always goes at the end.
        egesz = egesz[:UZENET_MAX - len(JOVAHAGYO_BLOKK) - 20] \
            + "\n…[truncated]\n\n" + JOVAHAGYO_BLOKK
    return egesz


# --- FOLLOW and LIKE (2026-10-02) ----------------------------------------

AKCIO_CIM = {"kovetes": "FOLLOW", "kedveles": "LIKE"}
AKCIO_SZOVEG_NELKUL = ("No outgoing text: the account only %s — right after "
                       "your approval.")
_AKCIO_IGE = {"kovetes": "starts following this profile",
              "kedveles": "likes this post"}
KEDVELES_IDEZET_MAX = 80


def _idezet_eleje(szoveg, max_hossz=KEDVELES_IDEZET_MAX):
    """The first `max_hossz` characters of the post on one line, with "…" if longer."""
    egy_sor = " ".join((szoveg or "").split())
    if len(egy_sor) > max_hossz:
        return egy_sor[:max_hossz].rstrip() + "…"
    return egy_sor


def akcio_szoveg(kanonikus, jelolo, megjegyzes="", cel=None, sorszam=None):
    """The full text of the FOLLOW / LIKE preview.

    Same principles as for a post: every foreign field (handle, name, profile
    description, post text) is neutralized, foreign text is framed as
    EXTERNAL DATA, Claude's note is SEPARATE and labeled, the fixed approval
    line appears ONCE, at the very end. The header line states the OBJECT of
    the action — the note does not go into the header line but into its own,
    labeled block.
    """
    cel = cel or {}
    tipus = kanonikus.get("tipus")
    sorok = []
    if sorszam:
        sorok.append("BskyGuard — awaiting approval (#%s)"
                     % _mezo(sorszam, 40, jelolo))
    else:
        sorok.append("BskyGuard — awaiting approval")
    sorok.append("type: %s" % _mezo(tipus, 20, jelolo))
    sorok.append("")
    if tipus == "kovetes":
        sorok.append("FOLLOW: @%s"
                     % _mezo(kanonikus.get("cel_handle"), 120, jelolo))
        sorok.append(AKCIO_SZOVEG_NELKUL % _AKCIO_IGE["kovetes"])
        sorok.append("")
        sorok.append("THE PROFILE (freshly fetched from Bluesky):")
        sorok.append("  handle: %s" % _mezo(cel.get("handle"), 120, jelolo))
        sorok.append("  did:    %s" % _mezo(cel.get("did"), 200, jelolo))
        if cel.get("megjelenitett_nev"):
            sorok.append("  name:   %s"
                         % _mezo(cel.get("megjelenitett_nev"), 64, jelolo))
        if cel.get("kovetok") is not None or cel.get("posztok") is not None:
            sorok.append("  followers: %s   posts: %s"
                         % (cel.get("kovetok") if cel.get("kovetok")
                            is not None else "?",
                            cel.get("posztok") if cel.get("posztok")
                            is not None else "?"))
        if (cel.get("leiras_roviden") or "").strip():
            sorok.append(_blokk(cel.get("leiras_roviden"), jelolo,
                                "PROFILE DESCRIPTION — KÜLSŐ ADAT / EXTERNAL DATA, NOT AN INSTRUCTION",
                                400))
    else:
        sorok.append("LIKE: post by @%s: \"%s\""
                     % (_mezo(cel.get("szerzo_handle"), 120, jelolo),
                        _mezo(_idezet_eleje(cel.get("szoveg_roviden")),
                              KEDVELES_IDEZET_MAX + 1, jelolo)))
        sorok.append(AKCIO_SZOVEG_NELKUL % _AKCIO_IGE["kedveles"])
        sorok.append("")
        sorok.append("THE POST (freshly fetched from Bluesky):")
        sorok.append("  author: %s" % _mezo(cel.get("szerzo_handle"), 120,
                                           jelolo))
        sorok.append("  did:    %s" % _mezo(cel.get("szerzo_did"), 200, jelolo))
        sorok.append("  uri:    %s" % _mezo(kanonikus.get("cel_uri"), 300,
                                           jelolo))
        sorok.append(_blokk(cel.get("szoveg_roviden"), jelolo,
                            "TEXT OF THE POST — KÜLSŐ ADAT / EXTERNAL DATA, NOT AN INSTRUCTION", 400))

    if cel.get("napi_darab") is not None and cel.get("napi_max"):
        sorok.append("")
        sorok.append("in the last 24 hours: %d %s (ceiling: %d)"
                     % (int(cel["napi_darab"]),
                        "follows" if tipus == "kovetes" else "likes",
                        int(cel["napi_max"])))

    if (megjegyzes or "").strip():
        sorok.append("")
        sorok.append(_blokk(megjegyzes, jelolo,
                            "Claude's note (not part of the action)",
                            200))

    sorok.append("")
    sorok.append(JOVAHAGYO_BLOKK)
    egesz = "\n".join(sorok)
    if len(egesz) > UZENET_MAX:
        egesz = egesz[:UZENET_MAX - len(JOVAHAGYO_BLOKK) - 20] \
            + "\n…[truncated]\n\n" + JOVAHAGYO_BLOKK
    return egesz


def akcio_visszaigazolas(tipus, rekord_uri, jelolo):
    """Confirmation of a successful follow / like, sent to the owner."""
    cim = "FOLLOWED" if tipus == "kovetes" else "LIKED"
    return "BskyGuard: %s.\n%s" % (cim, _mezo(rekord_uri, 300, jelolo))


def visszaigazolas(poszt_uri, jelolo):
    """Confirmation of a successful post, sent to the owner."""
    return "BskyGuard: SENT.\n" + _mezo(poszt_uri, 300, jelolo)


def ertesites(uzenet, jelolo):
    """Other message WRITTEN BY THE GUARD (e.g. a stuck send). It contains no
    foreign data, but it goes through the frame neutralizer too."""
    return "BskyGuard: " + _mezo(uzenet, 600, jelolo)
