"""motor.py — the guard's PIPELINE. Every network and Telegram call goes through
an ADAPTER, so the whole pipeline is testable, and a test run never touches
either the network or Telegram.

The two halves of the pipeline:
  * OUTGOING: draft → strict schema → rules → live target check →
    caps from the network → PREVIEW to the owner → (after approval) posting from
    the STORED canonical record;
  * INCOMING: notifications → sanitizing → `kozos\\be\\inbox.jsonl`.

Principle everywhere: FAIL-CLOSED. If anything is uncertain, nothing goes out.
"""

import os
import secrets
import time

import allapot
import beallitasok
import bejovo
import elonezet
import kimeno
import piszkozat
import szures
import tarolas_or
import telegram_or


class UzemHiba(RuntimeError):
    """Operational obstacle (network, key file) — we do not post."""


# =========================================================================
# 1. ADAPTERS
# =========================================================================

class EloHalo:
    """Bluesky adapter. The password appears only here."""

    def __init__(self, kornyezet):
        self.kornyezet = kornyezet
        self._session = None
        self._session_ts = 0.0

    def _s(self):
        # The Bluesky login token EXPIRES in ~2 hours (after that every call
        # returns HTTP 400). The guard, however, runs for days in one process —
        # so we renew the session based on its age. Deliberately TIME-driven,
        # not error-driven: this way a persistent network error cannot exhaust
        # the login rate limit.
        if (self._session is not None and
                (time.time() - self._session_ts) >= beallitasok.SESSION_MAX_KOR):
            self._session = None
        if self._session is None:
            import bsky_api
            kulcs = tarolas_or.kulcs_ertek_betolt(
                self.kornyezet.bsky_kulcs, ("BSKY_HANDLE", "BSKY_APP_PASSWORD"))
            self._session = bsky_api.belepes(kulcs["BSKY_HANDLE"],
                                             kulcs["BSKY_APP_PASSWORD"])
            self._session_ts = time.time()
        return self._session

    def sajat_did(self):
        return self._s().get("did") or ""

    def sajat_handle(self):
        return self._s().get("handle") or ""

    def poszt(self, uri):
        import bsky_api
        return bsky_api.poszt_lekerese(self._s()["accessJwt"], uri)

    def sajat_feed(self, limit=beallitasok.FEED_LIMIT):
        """Our own feed up to the end of the 24-hour window, PAGINATED (M''3).

        The cap comes from the network truth; that only holds if we really
        went through the whole window. The `filter` parameter is set
        explicitly so that our REPLIES are included as well — we cannot rely
        on a service-provider default for that.
        """
        import bsky_api
        import time as _ido
        s = self._s()

        def oldal(kurzor):
            return bsky_api.szerzo_posztjai(s["accessJwt"], s["did"], limit,
                                            kurzor=kurzor)

        return kimeno.feed_lapozas(oldal, _ido.time())

    def ertesitesek(self, limit=50):
        import bsky_api
        return bsky_api.ertesitesek(self._s()["accessJwt"], limit)

    def idovonal(self, limit):
        import bsky_api
        return bsky_api.idovonal(self._s()["accessJwt"], limit)

    def letrehoz(self, kanonikus, letrehozva, reply_engedelyezve):
        import bsky_api
        s = self._s()
        return bsky_api.poszt_letrehozas(
            s["accessJwt"], s["did"], kanonikus, letrehozva,
            reply_engedelyezve=reply_engedelyezve)

    # --- FOLLOW and LIKE (2026-10-02) -----------------------------------

    def profil(self, actor):
        """`getProfile` — `None` if the profile does not exist."""
        import bsky_api
        return bsky_api.profil_lekerese(self._s()["accessJwt"], actor)

    def sajat_rekordok(self, gyujtemeny,
                       limit=beallitasok.AKCIO_REKORD_LIMIT):
        """`listRecords` on OUR OWN repo — for the follow/like cap."""
        import bsky_api
        s = self._s()
        return bsky_api.sajat_rekordok(s["accessJwt"], s["did"], gyujtemeny,
                                       limit)

    def kovet(self, cel_did, letrehozva):
        import bsky_api
        s = self._s()
        return bsky_api.kovetes_letrehozas(s["accessJwt"], s["did"], cel_did,
                                           letrehozva)

    def kedvel(self, cel_uri, cel_cid, letrehozva):
        import bsky_api
        s = self._s()
        return bsky_api.kedveles_letrehozas(s["accessJwt"], s["did"], cel_uri,
                                            cel_cid, letrehozva)


class EloBot:
    """The GUARD'S OWN Telegram bot. The token appears only here."""

    def __init__(self, kornyezet):
        self.kornyezet = kornyezet
        self._kulcs = None

    def _k(self):
        if self._kulcs is None:
            self._kulcs = tarolas_or.kulcs_ertek_betolt(
                self.kornyezet.or_bot_kulcs,
                ("OR_BOT_TOKEN", "OWNER_USER_ID", "OWNER_CHAT_ID"))
        return self._kulcs

    def owner_user_id(self):
        return int(self._k()["OWNER_USER_ID"])

    def owner_chat_id(self):
        return int(self._k()["OWNER_CHAT_ID"])

    def kuld(self, szoveg):
        return telegram_or.uzenet_kuldes(
            self._k()["OR_BOT_TOKEN"], self.owner_chat_id(), szoveg)

    def frissitesek(self, offset):
        return telegram_or.frissitesek(self._k()["OR_BOT_TOKEN"], offset)


class SzarazBot:
    """In `--szaraz` (dry-run) mode it sends NOTHING: it prints what it would send."""

    def __init__(self, kiir=print):
        self.kiir = kiir
        self._kovetkezo = 1
        self.kuldott = []

    def owner_user_id(self):
        return 0

    def owner_chat_id(self):
        return 0

    def kuld(self, szoveg):
        self.kuldott.append(szoveg)
        self.kiir("--- DRY RUN: I would send this to the owner on Telegram ---")
        self.kiir(szoveg)
        self.kiir("--- (end) ---")
        azonosito = self._kovetkezo
        self._kovetkezo += 1
        return azonosito

    def frissitesek(self, offset):
        return []


def halozat(muvelet, fuggveny, *args, **kwargs):
    """Wrapper for network calls: FAIL-CLOSED and SECRET-FREE.

    ONLY the type of the exception gets out — neither the URL, nor the request
    body, nor the password or the bot token can leak to the console or the log.
    """
    try:
        return fuggveny(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — deliberately stripped down
        # The message of `bsky_api._biztonsagos_hiba` is built to be
        # secret-free: it contains only the operation name, the error type and
        # the HTTP code. We let this one message of known shape through,
        # because without it the cause of the error (wrong password, timeout,
        # rate limiting) cannot be determined.
        reszlet = type(e).__name__
        szoveg = str(e)
        # Both the English prefix (current) and the Hungarian one (older builds) are accepted.
        _elotag = next((p for p in ("Bluesky call failed: ", "Bluesky hívás sikertelen: ")
                        if szoveg.startswith(p)), None)
        if (isinstance(e, RuntimeError) and _elotag
                and len(szoveg) < 120 and "://" not in szoveg and "bot" not in szoveg.lower()):
            reszlet = szoveg[len(_elotag):]
        raise UzemHiba("network error at the %s step (%s) — fail-closed."
                       % (muvelet, reszlet)) from None


# =========================================================================
# 2. OUTGOING: draft → preview
# =========================================================================

# Temporary reasons: with these the item stays PENDING and still approvable.
HALASZTHATO_OKOK = frozenset((
    "napi_limit", "perc_limit", "kezdemenyezett_napi_limit",
    "nincs_halozati_ellenorzes", "fuggo_limit",
    # 2026-10-02: the own rolling 24-hour cap of follows / likes.
    "kovetes_napi_limit", "kedveles_napi_limit",
))


def _feldolgozottba(kornyezet, utvonal, cimke):
    """Moving an already processed draft under `allapot\\feldolgozott\\`.

    At this point the file is ALREADY in the protected `allapot\\varo\\`
    folder, so this step is only housekeeping: a rename within the same
    volume, with `os.replace`. The fallback branch of the earlier
    `shutil.move` used `copy2`, which in principle would have copied CONTENT —
    we dropped it on purpose (Kö1).
    """
    tarolas_or.mappa_keszit(kornyezet.feldolgozott)
    nev = "%d_%s_%s" % (int(time.time()), cimke, os.path.basename(utvonal))
    cel = os.path.join(kornyezet.feldolgozott, nev)
    try:
        os.replace(utvonal, cel)
    except OSError:
        # If the rename fails, we delete it instead: the file must NEVER
        # stay there to be processed again.
        try:
            os.remove(utvonal)
        except OSError:
            pass
    return cel


def _eredeti_nev(varo_nev):
    """From the file name in the queue, the ORIGINAL name given by Claude."""
    darabok = varo_nev.split("_", 2)
    return darabok[2] if len(darabok) == 3 else varo_nev


def varo_fajlok(kornyezet):
    """The drafts in the protected queue, in ARRIVAL order."""
    try:
        nevek = sorted(os.listdir(kornyezet.varo))
    except OSError:
        return []
    return [os.path.join(kornyezet.varo, n) for n in nevek
            if n.lower().endswith(".json")]


def _kihagyando_betolt(kornyezet, most):
    """Entries in the drop folder that cannot be taken over: `{name: (attempts, last_ts)}`.

    A''''1: every entry has a TIMESTAMP and expires after a week. The old,
    number-only form is accepted too (the first round after the update
    rewrites it): in that case the current time is taken as the start.
    """
    adat = tarolas_or.json_olvas(kornyezet.kihagyando, {}) or {}
    if not isinstance(adat, dict):
        return {}
    tabla = {}
    for nev, ertek in adat.items():
        if not isinstance(nev, str):
            continue
        if isinstance(ertek, int) and not isinstance(ertek, bool):
            tabla[nev] = (ertek, int(most))      # old form
        elif isinstance(ertek, dict):
            proba = ertek.get("proba")
            ido = ertek.get("utoljara_ts")
            if not isinstance(proba, int) or isinstance(proba, bool):
                continue
            ido = int(ido) if isinstance(ido, int) else int(most)
            if (most - ido) > beallitasok.KIHAGYANDO_ELAVULAS:
                continue                          # expired — forget it
            tabla[nev] = (proba, ido)
    return tabla


def _kihagyando_ment(kornyezet, tabla):
    """Writing out the skip list, with an UPPER LIMIT.

    The untrusted side must not be able to grow the guard's state without
    bound. When the limit is reached, we keep the MOST RECENT entries: the old
    ones would expire anyway, and previously it was exactly the permanently
    skipped ones that got stuck in it.
    """
    if len(tabla) > beallitasok.KIHAGYANDO_MAX:
        rendezett = sorted(tabla.items(), key=lambda p: (-p[1][1], p[0]))
        tabla = dict(rendezett[:beallitasok.KIHAGYANDO_MAX])
    tarolas_or.json_ir(kornyezet.kihagyando,
                       {nev: {"proba": proba, "utoljara_ts": int(ido)}
                        for nev, (proba, ido) in tabla.items()})


def _bedobott_hashek_betolt(kornyezet, most):
    """Content hashes of files already taken over but not deletable from the drop folder."""
    adat = tarolas_or.json_olvas(kornyezet.bedobott_hashek, {}) or {}
    if not isinstance(adat, dict):
        return {}
    return {kulcs: int(ido) for kulcs, ido in adat.items()
            if isinstance(kulcs, str) and isinstance(ido, int)
            and (most - ido) <= beallitasok.BEDOBOTT_HASH_ELAVULAS}


def _bedobott_hashek_ment(kornyezet, tabla):
    if len(tabla) > beallitasok.BEDOBOTT_HASH_MAX:
        rendezett = sorted(tabla.items(), key=lambda p: (-p[1], p[0]))
        tabla = dict(rendezett[:beallitasok.BEDOBOTT_HASH_MAX])
    tarolas_or.json_ir(kornyezet.bedobott_hashek, tabla)


def _bedobott_kudarc(kornyezet, tabla, nev, ok, uzenetek):
    """Booking one failed takeover (Kö'''2).

    A FOLDER placed in the drop folder, a locked file, and a file whose owner
    (the untrusted user) denied us access, used to cause a retry and a
    pointless message in every round, while eating into the per-round budget
    of fifty. After three failed rounds the name is skipped permanently, and
    this is reported in the state file.
    """
    proba = tabla.get(nev, (0, 0))[0] + 1
    tabla[nev] = (proba, int(time.time()))
    if proba >= beallitasok.BEDOBO_PROBA_MAX:
        uzenetek.append(
            "could not take over the entry in the drop folder even after %d "
            "rounds (%s, %s) — FROM NOW ON I SKIP IT. If this was a real draft, "
            "delete it from the drop folder and drop it in again under another "
            "name. (The skip expires by itself after a week.)"
            % (proba, szures.egysoros(nev, 80), ok))
        allapot.naploz(kornyezet, {"esemeny": "bedobott_veglegesen_kihagyva",
                                   "piszkozat": nev, "ok": [ok]})
    else:
        uzenetek.append("could not take over the dropped entry (%s, %s) — "
                        "skipping it in this round."
                        % (szures.egysoros(nev, 80), ok))


def _bedobott_eltavolitas(utvonal):
    """Removing an irregular entry. `True` if it succeeded.

    On a DIRECTORY `os.remove` always fails on Windows — so we also try
    `os.rmdir` (Kö'''2). A non-empty directory is deliberately NOT deleted
    recursively: the drop folder is the untrusted side's territory, and we do
    not clean up there anything we do not understand.
    """
    try:
        os.remove(utvonal)
        return True
    except OSError:
        pass
    try:
        os.rmdir(utvonal)
        return True
    except OSError:
        return False


def bevetel(kornyezet, uzenetek, most):
    """TAKING OVER the dropped drafts into the protected queue (Kö1, M''''2).

    This is the ONLY operation the guard performs in the untrusted side's drop
    folder. From here on it reads exclusively from its own, protected area:
    processing and approval no longer take a single byte from there.

    Returns: the outward-showable rows of the drafts that already failed the
    strict schema here — their content never even reached the disk.

    The takeover is NOT a move (M''''2). Within the same volume, Windows
    `MoveFileEx` preserves the file's access control list AND its owner, so
    the moved draft would have sat in the protected `allapot\\varo\\` folder
    still owned by the untrusted user, with that user's inherited rights — it
    would not even have inherited the new parent's rights. Hence the sequence:

        safe open and read → write a NEW file into the queue
        (fsync'ed, with `O_EXCL`) → and ONLY THEN delete the original.

    If the deletion fails (the untrusted side keeps it open or revokes the
    delete right from us), we store the CONTENT fingerprint and never take
    over the same content again — so a stuck file cannot generate a new
    preview in every round.
    """
    tarolas_or.mappa_keszit(kornyezet.varo)
    tarolas_or.mappa_keszit(kornyezet.feldolgozott)
    if not piszkozat.bedobo_valodi_mappa_e(kornyezet.ki):
        uzenetek.append("the drop folder is not a real directory (mount point "
                        "or link) — I take NOTHING from it.")
        allapot.naploz(kornyezet, {"esemeny": "bedobo_mappa_nem_valodi"})
        return []
    elutasitottak = []
    varakozo = len(varo_fajlok(kornyezet))
    if varakozo >= beallitasok.VARO_MAX:
        uzenetek.append("the draft queue is full (%d items) — not taking in "
                        "new drafts right now." % varakozo)
        return elutasitottak
    kihagyando = _kihagyando_betolt(kornyezet, most)
    atvett_hashek = _bedobott_hashek_betolt(kornyezet, most)
    hashek_valtoztak = False
    valtozott = False
    veglegesen = [nev for nev, (szam, _ido) in kihagyando.items()
                  if szam >= beallitasok.BEDOBO_PROBA_MAX]
    if veglegesen:
        uzenetek.append("the drop folder has %d entries that cannot be taken "
                        "over; skipping them permanently." % len(veglegesen))
    for utvonal in piszkozat.piszkozat_fajlok(
            kornyezet.ki, kihagyando=set(veglegesen)):
        nev = os.path.basename(utvonal)
        rendben, ok = piszkozat.szabalyos_bedobott_fajl(utvonal)
        if not rendben:
            allapot.naploz(kornyezet, {"esemeny": "bedobott_fajl_kihagyva",
                                       "piszkozat": nev, "ok": [ok]})
            if _bedobott_eltavolitas(utvonal):
                uzenetek.append("dropped entry skipped (%s): %s"
                                % (szures.egysoros(nev, 80), ok))
                if nev in kihagyando:
                    del kihagyando[nev]
                    valtozott = True
            else:
                _bedobott_kudarc(kornyezet, kihagyando, nev, ok, uzenetek)
                valtozott = True
            continue
        bajtok, olvasasi_ok = piszkozat.biztonsagos_beolvasas(
            utvonal, bedobo=kornyezet.ki)
        if bajtok is None:
            _bedobott_kudarc(kornyezet, kihagyando, nev, olvasasi_ok, uzenetek)
            valtozott = True
            continue
        tartalom_hash = allapot.tartalom_ujjlenyomat(kornyezet, bajtok)
        if tartalom_hash in atvett_hashek:
            # We already took over this CONTENT; only the deletion failed back
            # then. If it succeeds now, the entry can go too.
            if _bedobott_eltavolitas(utvonal):
                del atvett_hashek[tartalom_hash]
                hashek_valtoztak = True
                # Kö'''''5 — this used to happen SILENTLY. If Claude
                # deliberately sent the same text once more (for example
                # after a botched round), the draft vanished without a trace.
                # Now we say that we skipped it, and why.
                uzenetek.append(
                    "I skipped the dropped draft because its CONTENT was "
                    "already taken over in an earlier round (%s). I managed to "
                    "delete it from the drop folder now, so the same text can "
                    "be dropped in again."
                    % szures.egysoros(nev, 80))
                allapot.naploz(kornyezet,
                               {"esemeny": "bedobott_azonos_tartalom",
                                "piszkozat": nev})
            else:
                # Kö'''''4 — until now this branch did `continue` without
                # booking, so the name ate into the per-round budget in every
                # round, but never ended up among the permanently skipped
                # ones. With fifty such files the drop folder stayed full.
                _bedobott_kudarc(kornyezet, kihagyando, nev,
                                 "azonos_tartalom_bent_ragadt", uzenetek)
                valtozott = True
            continue

        # K'''''1/b — THE FULL STRICT SCHEMA, STILL IN MEMORY.
        # A raw, unchecked byte is never written anywhere the untrusted side
        # can read. Of whatever fails the schema, not a single byte reaches
        # the disk: only a short, CONTENT-FREE reason code goes into the
        # outward state file.
        tetel, sema_okok = piszkozat.bajtokbol(bajtok)
        if sema_okok:
            allapot.naploz(kornyezet, {"esemeny": "piszkozat_elutasitva",
                                       "piszkozat": nev, "ok": sema_okok})
            elutasitottak.append(allapot.elutasitott_tetel(nev, sema_okok))
            if not _bedobott_eltavolitas(utvonal):
                _bedobott_kudarc(kornyezet, kihagyando, nev,
                                 "elutasitott_nem_torolheto", uzenetek)
                valtozott = True
            elif nev in kihagyando:
                del kihagyando[nev]
                valtozott = True
            continue

        # What goes into the queue is the CANONICALLY RE-SERIALIZED record,
        # not the original bytes.
        cel = os.path.join(kornyezet.varo, "%d_%s_%s"
                           % (int(most), secrets.token_hex(3), nev))
        try:
            tarolas_or.uj_fajl_bajtokkal(cel,
                                         piszkozat.kanonikus_bajtok(tetel))
        except OSError as e:
            # Writing into our own protected folder failed — this is an
            # operational error, not the fault of the dropped file. We do not
            # delete the original.
            _bedobott_kudarc(kornyezet, kihagyando, nev, type(e).__name__,
                             uzenetek)
            valtozott = True
            continue
        if not _bedobott_eltavolitas(utvonal):
            # The content is already safe in the queue; the original got
            # stuck. The hash prevents processing it ONCE MORE.
            atvett_hashek[tartalom_hash] = int(most)
            hashek_valtoztak = True
            uzenetek.append(
                "I took over a dropped draft, but could not delete it from the "
                "drop folder (%s) — I will not process its content again. "
                "Delete it by hand." % szures.egysoros(nev, 80))
        if nev in kihagyando:
            del kihagyando[nev]
            valtozott = True
        varakozo += 1
        if varakozo >= beallitasok.VARO_MAX:
            break
    if valtozott:
        _kihagyando_ment(kornyezet, kihagyando)
    if hashek_valtoztak:
        _bedobott_hashek_ment(kornyezet, atvett_hashek)
    return elutasitottak


def _varo_takaritas(kornyezet, uzenetek, most):
    """Closing out stale drafts stuck in the queue."""
    for utvonal in varo_fajlok(kornyezet):
        nev = os.path.basename(utvonal)
        try:
            beerkezett = int(nev.split("_", 1)[0])
        except (ValueError, IndexError):
            beerkezett = 0
        if beerkezett and (most - beerkezett) <= beallitasok.VARO_ELAVULAS:
            continue
        uzenetek.append("stale draft closed: %s"
                        % szures.egysoros(_eredeti_nev(nev), 80))
        allapot.naploz(kornyezet, {"esemeny": "piszkozat_elavult",
                                   "piszkozat": _eredeti_nev(nev)})
        _feldolgozottba(kornyezet, utvonal, "elavult")


class LancKeret(object):
    """PER-ROUND budget for thread-chain lookups (A'''''1).

    One object lives for one round. Before every `getPosts` call we consume
    one unit of it; when it runs out, we raise `UzemHiba`, which `kimeno_menet`
    treats as a postponement: the draft stays in the protected queue, and the
    next round continues with a fresh budget.
    """

    def __init__(self, keret=None):
        self.maradek = int(beallitasok.LANC_KERET_KORONKENT
                           if keret is None else keret)

    def fogyaszt(self):
        if self.maradek <= 0:
            raise UzemHiba("the per-round chain-lookup budget is used up — "
                           "postponing the remaining drafts to the next round.")
        self.maradek -= 1


def _cel_es_kanonikus(kornyezet, halo, tetel, sajat_did, lanc_keret=None,
                      most=None):
    """Live check of the reply target, and the canonical record. `(kan, cel, okok)`."""
    lanc_keret = lanc_keret or LancKeret()
    cel = None
    if tetel["tipus"] == "valasz":
        uri = tetel["valasz_cel_uri"]
        lanc_keret.fogyaszt()
        poszt = halozat("getPosts", halo.poszt, uri)
        engedelylista = szures.engedelylista_betolt(kornyezet.szivesen_latnak)

        def szulo_lekero(cim):
            lanc_keret.fogyaszt()
            return halozat("getPosts (parent)", halo.poszt, cim)

        cel, okok = kimeno.cel_ellenorzes(
            poszt, uri, sajat_did, engedelylista, szulo_lekero, most)
        if okok:
            return None, None, ["optin:" + o for o in okok]
    kanonikus = szures.kimeno_kanonikus(
        tetel["tipus"], tetel["szoveg"], cel, tetel["langs"],
        bool(cel and cel.get("kezdemenyezett")))
    okok = szures.kanonikus_ervenyes(kanonikus)
    return kanonikus, cel, okok


def piszkozat_feldolgozas(kornyezet, halo, bot, mintak, sajat_did,
                          sajat_posztok, most, rekordok, utvonal,
                          lanc_keret=None):
    """Processing ONE draft. `(new_record_or_None, okok)`.

    `utvonal` ALREADY points into the protected `allapot\\varo\\` folder:
    `bevetel` read the bytes of the dropped file and wrote them here as a NEW
    file (Kö1, M''''2). So the file belongs to the guard — it inherits the
    target folder's access control list and its owner is the guard — and is
    not a file moved over from the drop folder with foreign rights.
    """
    nev = _eredeti_nev(os.path.basename(utvonal))
    tetel, okok = piszkozat.fajlbol(utvonal)
    if okok:
        return None, okok

    if tetel["tipus"] in kimeno.AKCIO_TIPUSOK:
        # 2026-10-02: follow / like — the same gate, without text.
        return akcio_feldolgozas(kornyezet, halo, bot, sajat_did, most,
                                 rekordok, utvonal, nev, tetel, lanc_keret)

    okok = kimeno.szoveg_szabalyok(tetel["szoveg"], mintak)
    if okok:
        return None, okok

    okok = kimeno.fuggo_plafon(len(allapot.fuggo(rekordok)))
    if okok:
        return None, okok

    kanonikus, cel, okok = _cel_es_kanonikus(kornyezet, halo, tetel, sajat_did,
                                             lanc_keret, most)
    if okok:
        return None, okok

    okok = kimeno.plafon_ellenorzes(
        sajat_posztok, most, sajat_did, szures.dedup_kulcs(tetel["szoveg"]),
        szulo_uri=(cel or {}).get("uri"),
        kezdemenyezett=bool(cel and cel.get("kezdemenyezett")))
    if okok:
        return None, okok

    # The record is written out FIRST (without message_id, in the
    # `elokeszites` (preparing) state), and only then is the preview sent. If
    # the send or the write after it fails, the record stays unapprovable —
    # there will be no "dangling", invisibly approvable item (lesson of M'5).
    rekord = allapot.uj_rekord(kanonikus, cel, nev,
                               tetel["megjegyzes_tulajnak"], most)
    rekord["allapot"] = "elokeszites"
    allapot.ment(kornyezet, rekord)
    # Kö'''4 — the draft file is moved ALREADY HERE, BEFORE the preview is
    # sent. The record is by now in the protected `elonezetek` folder, the
    # draft file is no longer needed. Had the run been interrupted BETWEEN
    # sending the preview and moving the file (power cut, manual stop), the
    # same draft would have produced a SECOND preview in the next round.
    _feldolgozottba(kornyezet, utvonal, "elonezet")

    jelolo = elonezet.jelolo_keszit()
    uzenet = elonezet.szoveg(kanonikus, jelolo,
                             megjegyzes=tetel["megjegyzes_tulajnak"],
                             cel=cel, sorszam=rekord["azonosito"][:6])
    # If for any reason the outgoing text does not appear in the preview
    # exactly like this (rewriting, truncation), the preview is NOT EVEN SENT.
    if not elonezet.kimeno_blokk_pontos_e(uzenet, kanonikus.get("szoveg")):
        rekord["allapot"] = allapot.ELUTASITVA
        rekord["ok"] = ["elonezet_nem_bajtpontos"]
        allapot.ment(kornyezet, rekord)
        return None, ["elonezet_nem_bajtpontos"]

    try:
        message_id = bot.kuld(uzenet)
    except telegram_or.TelegramHiba:
        rekord["allapot"] = allapot.ELUTASITVA
        rekord["ok"] = ["elonezet_nem_ment_ki"]
        allapot.ment(kornyezet, rekord)
        return None, ["elonezet_nem_ment_ki"]

    rekord["message_id"] = message_id
    rekord["allapot"] = allapot.VAR
    allapot.ment(kornyezet, rekord)
    return rekord, []


# --- FOLLOW and LIKE (2026-10-02) ----------------------------------------

def _akcio_cel(halo, tetel, sajat_did, lanc_keret):
    """Live check of the follow/like target. `(cel, okok)`.

    Network error → `UzemHiba` (the caller postpones). The network request
    consumes from the per-round budget, like the reply-chain lookups.
    """
    if tetel["tipus"] == "kovetes":
        actor = tetel.get("cel_did") or tetel.get("cel_handle")
        lanc_keret.fogyaszt()
        profil = halozat("getProfile", halo.profil, actor)
        return kimeno.kovetes_cel_ellenorzes(
            profil, tetel.get("cel_handle"), tetel.get("cel_did"), sajat_did)
    lanc_keret.fogyaszt()
    poszt = halozat("getPosts", halo.poszt, tetel["cel_uri"])
    return kimeno.kedveles_cel_ellenorzes(poszt, tetel["cel_uri"], sajat_did)


def _akcio_plafon(halo, tipus, most, lanc_keret=None):
    """The follow/like cap from the NETWORK truth. `(darab, okok)`."""
    if lanc_keret is not None:
        lanc_keret.fogyaszt()
    valasz = halozat("listRecords", halo.sajat_rekordok,
                     kimeno.GYUJTEMENY[tipus])
    return kimeno.akcio_plafon(valasz, most, tipus)


def akcio_feldolgozas(kornyezet, halo, bot, sajat_did, most, rekordok,
                      utvonal, nev, tetel, lanc_keret=None):
    """Processing ONE follow / like draft. `(rekord, okok)`.

    The same gate as for a post, without the text rules (there is no outgoing
    text): pending cap → live target check → canonical record → excluding a
    pending item with the same target → cap from the network → record to disk
    FIRST (`elokeszites`) → draft into the processed folder → preview → `var`.
    """
    lanc_keret = lanc_keret or LancKeret()
    tipus = tetel["tipus"]

    okok = kimeno.fuggo_plafon(len(allapot.fuggo(rekordok)))
    if okok:
        return None, okok

    cel, okok = _akcio_cel(halo, tetel, sajat_did, lanc_keret)
    if okok:
        return None, okok

    kanonikus = kimeno.akcio_kanonikus(tipus, cel)
    okok = kimeno.akcio_kanonikus_ervenyes(kanonikus)
    if okok:
        return None, okok

    # (A second pending item with the same target is refused: the owner would
    # approve the same thing twice; on the second send it would turn into
    # `mar_kovetve`/`mar_kedvelve` anyway).
    for regi in allapot.fuggo(rekordok):
        if kimeno.akcio_azonos_cel(regi.get("kanonikus") or {}, kanonikus):
            return None, ["mar_jovahagyasra_var"]

    darab, okok = _akcio_plafon(halo, tipus, most, lanc_keret)
    if okok:
        return None, okok
    cel["napi_darab"] = darab
    cel["napi_max"] = kimeno.akcio_plafon_max(tipus)

    rekord = allapot.uj_rekord(kanonikus, cel, nev,
                               tetel["megjegyzes_tulajnak"], most)
    rekord["allapot"] = "elokeszites"
    allapot.ment(kornyezet, rekord)
    # Kö'''4 — just like for a post: the draft goes into the processed folder
    # BEFORE the preview is sent, so that an interruption cannot produce a
    # second preview.
    _feldolgozottba(kornyezet, utvonal, "elonezet")

    jelolo = elonezet.jelolo_keszit()
    uzenet = elonezet.akcio_szoveg(kanonikus, jelolo,
                                   megjegyzes=tetel["megjegyzes_tulajnak"],
                                   cel=cel, sorszam=rekord["azonosito"][:6])
    try:
        message_id = bot.kuld(uzenet)
    except telegram_or.TelegramHiba:
        rekord["allapot"] = allapot.ELUTASITVA
        rekord["ok"] = ["elonezet_nem_ment_ki"]
        allapot.ment(kornyezet, rekord)
        return None, ["elonezet_nem_ment_ki"]

    rekord["message_id"] = message_id
    rekord["allapot"] = allapot.VAR
    allapot.ment(kornyezet, rekord)
    return rekord, []


def kimeno_menet(kornyezet, halo, bot, mintak, sajat_did, sajat_posztok,
                 most, rekordok, uzenetek):
    """Processing the whole queue. Returns: outward-showable items.

    Order (Kö1): FIRST we take the dropped files over into the protected
    queue, and we read only FROM THERE. With a postponable reason the draft
    stays in the queue — it does not go back to the untrusted side.
    """
    # Schema rejection ALREADY happens at takeover (K'''''1/b): the dropped
    # bytes fail in memory, nothing of them reaches the disk.
    kifele = list(bevetel(kornyezet, uzenetek, most) or [])
    _varo_takaritas(kornyezet, uzenetek, most)
    lanc_keret = LancKeret()
    for utvonal in varo_fajlok(kornyezet):
        nev = _eredeti_nev(os.path.basename(utvonal))
        try:
            rekord, okok = piszkozat_feldolgozas(
                kornyezet, halo, bot, mintak, sajat_did, sajat_posztok,
                most, rekordok, utvonal, lanc_keret)
        except UzemHiba as e:
            # Network obstacle: the file STAYS, we retry later.
            uzenetek.append("draft postponed (%s): %s" % (nev, str(e)))
            continue
        if okok:
            halaszthato = all(o in HALASZTHATO_OKOK for o in okok)
            if halaszthato:
                uzenetek.append("draft postponed (%s): %s"
                                % (nev, ", ".join(okok)))
                continue
            allapot.naploz(kornyezet, {"esemeny": "piszkozat_elutasitva",
                                       "piszkozat": nev, "ok": okok})
            kifele.append(allapot.elutasitott_tetel(nev, okok))
            _feldolgozottba(kornyezet, utvonal, "elutasitva")
            continue
        rekordok.append(rekord)
        allapot.naploz(kornyezet, {"esemeny": "elonezet_kikuldve",
                                   "elonezet": rekord["azonosito"],
                                   "piszkozat": nev})
        # The file was ALREADY moved, BEFORE the preview was sent
        # (Kö'''4) — nothing more to do with it here.
    return kifele


# =========================================================================
# 3. APPROVAL AND POSTING
# =========================================================================

def beragadtak_kezelese(kornyezet, bot, rekordok, uzenetek):
    """An item stuck in the `kuldes_alatt` (sending) state: we NEVER resend.

    We notify the owner and record it in the state file; the item is set to
    manual check, and we keep working with the OTHER items (one stuck row
    must not paralyze the whole guard).
    """
    for rekord in list(allapot.beragadt(rekordok)):
        rekord["allapot"] = allapot.KEZI_ELLENORZES
        rekord["ok"] = ["kuldes_alatt_beragadt"]
        allapot.ment(kornyezet, rekord)
        allapot.naploz(kornyezet, {"esemeny": "kuldes_alatt_beragadt",
                                   "elonezet": rekord["azonosito"]})
        uzenet = ("a send was interrupted (%s). I will NOT resend it. Check on "
                  "Bluesky whether it went out." % rekord["azonosito"][:6])
        uzenetek.append(uzenet)
        try:
            bot.kuld(elonezet.ertesites(uzenet, elonezet.jelolo_keszit()))
        except telegram_or.TelegramHiba:
            pass


def lejartak_kezelese(kornyezet, rekordok, most):
    for rekord in allapot.fuggo(rekordok):
        if telegram_or.lejart_e(rekord.get("letrehozva_ts"), most):
            rekord["allapot"] = allapot.LEJART
            rekord["ok"] = ["lejart_48_ora"]
            allapot.ment(kornyezet, rekord)
            allapot.naploz(kornyezet, {"esemeny": "elonezet_lejart",
                                       "elonezet": rekord["azonosito"]})


def _elokeszitesben_ragadtak(kornyezet, rekordok, most, uzenetek):
    """`elokeszites` state: the run was interrupted while sending the preview.

    We cannot know whether the owner received the message — the record has no
    message_id, so it could not be approved anyway. We close it.
    """
    for rekord in rekordok:
        if rekord.get("allapot") != "elokeszites":
            continue
        if (most - (rekord.get("letrehozva_ts") or 0)) < 300:
            continue  # it may be in preparation right now
        rekord["allapot"] = allapot.ELUTASITVA
        rekord["ok"] = ["elonezet_kezbesitese_bizonytalan"]
        allapot.ment(kornyezet, rekord)
        uzenetek.append("sending a preview was interrupted (%s) — closed it."
                        % (rekord.get("azonosito") or "?")[:6])


def _ujraellenorzes(kornyezet, halo, rekord, sajat_did, sajat_posztok, most):
    """Once more BEFORE posting: caps + live target check."""
    kanonikus = rekord["kanonikus"]
    cel_uri = (kanonikus.get("valasz_cel") or {}).get("parent_uri")
    if kanonikus.get("tipus") == "valasz":
        poszt = halozat("getPosts", halo.poszt, cel_uri)
        engedelylista = szures.engedelylista_betolt(kornyezet.szivesen_latnak)
        friss, okok = kimeno.cel_ellenorzes(
            poszt, cel_uri, sajat_did, engedelylista,
            lambda u: halozat("getPosts (parent)", halo.poszt, u), most)
        if okok:
            return ["optin:" + o for o in okok]
        # The approved target must not have changed (cid change = different content).
        regi = kanonikus.get("valasz_cel") or {}
        if friss["uri"] != regi.get("parent_uri") or \
                friss["cid"] != regi.get("parent_cid"):
            return ["celpont_megvaltozott"]
        # K''1 — the THREAD ROOT is just as much part of the addressing as the
        # parent. `cel_ellenorzes` freshly verified it from the network; here
        # we check that it is still the root stored at approval time.
        if not friss.get("gyoker_igazolva"):
            return ["gyoker_nem_igazolhato"]
        if friss.get("gyoker_uri") != regi.get("root_uri") or \
                friss.get("gyoker_cid") != regi.get("root_cid"):
            return ["gyoker_megvaltozott"]
        if bool(friss.get("kezdemenyezett")) != bool(
                kanonikus.get("kezdemenyezett")):
            return ["kezdemenyezett_elter"]
        # The owner may only send out what they approved, i.e. what they saw
        # in the preview: the same ancestors and the same foreign
        # participants. If the chain changed in the meantime, we do not send.
        tarolt_cel = rekord.get("cel") or {}
        if list(friss.get("lanc_uri_lista") or []) != \
                list(tarolt_cel.get("lanc_uri_lista") or []):
            return ["felmeno_lanc_megvaltozott"]
        if sorted(sz.get("did") or "" for sz in
                  (friss.get("idegen_szereplok") or [])) != \
                sorted(sz.get("did") or "" for sz in
                       (tarolt_cel.get("idegen_szereplok") or [])):
            return ["idegen_szereplok_megvaltoztak"]
        # Kö'''3 — the HANDLE can be swapped before posting. The anchor is the
        # DID, but the owner saw the handle in the preview.
        # If the handle changed, we STOP and report.
        if (friss.get("szerzo_handle") or "") != \
                (tarolt_cel.get("szerzo_handle") or ""):
            return ["handle_megvaltozott"]
        if (friss.get("gyoker_szerzo_handle") or "") != \
                (tarolt_cel.get("gyoker_szerzo_handle") or ""):
            return ["gyoker_handle_megvaltozott"]
        # J6/(B) — the ONGOING CONVERSATION classification is recomputed too
        # (whether the target replied to our post); if it changed in the
        # meantime (for example X deleted the reply addressed to us, or the
        # anchor is no longer the same), we STOP.
        if bool(friss.get("folyo_beszelgetes")) != bool(
                tarolt_cel.get("folyo_beszelgetes")):
            return ["folyo_beszelgetes_elter"]
        if (friss.get("folyo_horgony_uri") or "") != \
                (tarolt_cel.get("folyo_horgony_uri") or ""):
            return ["folyo_horgony_elter"]
    return kimeno.plafon_ellenorzes(
        sajat_posztok, most, sajat_did,
        szures.dedup_kulcs(kanonikus.get("szoveg") or ""),
        szulo_uri=cel_uri, kezdemenyezett=bool(kanonikus.get("kezdemenyezett")))


def posztolas(kornyezet, halo, bot, rekord, sajat_did, sajat_posztok, most,
              uzenetek):
    """Sending out the approved item from the STORED canonical record."""
    if (rekord.get("kanonikus") or {}).get("tipus") in kimeno.AKCIO_TIPUSOK:
        # 2026-10-02: follow / like — its own executor, with the same
        # write-ahead and no-retry rule.
        return akcio_vegrehajtas(kornyezet, halo, bot, rekord, sajat_did,
                                 most, uzenetek)
    if not allapot.rekord_ep_e(rekord):
        rekord["allapot"] = allapot.KEZI_ELLENORZES
        rekord["ok"] = ["tarolt_rekord_serult"]
        allapot.ment(kornyezet, rekord)
        uzenetek.append("the stored record is damaged (%s) — not sending it."
                        % rekord["azonosito"][:6])
        return False

    try:
        okok = _ujraellenorzes(kornyezet, halo, rekord, sajat_did,
                               sajat_posztok, most)
    except UzemHiba as e:
        uzenetek.append("sending postponed (%s): %s"
                        % (rekord["azonosito"][:6], str(e)))
        return False
    if okok:
        if all(o in HALASZTHATO_OKOK for o in okok):
            uzenetek.append("sending postponed (%s): %s — can be approved "
                            "again later." % (rekord["azonosito"][:6],
                                              ", ".join(okok)))
            return False
        rekord["allapot"] = allapot.ELUTASITVA
        rekord["ok"] = okok
        allapot.ment(kornyezet, rekord)
        allapot.naploz(kornyezet, {"esemeny": "kuldes_elutasitva",
                                   "elonezet": rekord["azonosito"], "ok": okok})
        uzenetek.append("the approved item cannot go out after all (%s): %s"
                        % (rekord["azonosito"][:6], ", ".join(okok)))
        # Kö'''3 — if the addressee's HANDLE was swapped since approval, it is
        # not enough to only write it into the state file: we also tell the
        # owner via the bot, because the decision was theirs.
        if any(o.endswith("handle_megvaltozott") for o in okok):
            uzenet = ("the addressee changed their handle since the approval "
                      "(%s). I did NOT send it. If you still want it, "
                      "drop the draft in again — you will get a fresh preview."
                      % rekord["azonosito"][:6])
            uzenetek.append(uzenet)
            try:
                bot.kuld(elonezet.ertesites(uzenet, elonezet.jelolo_keszit()))
            except telegram_or.TelegramHiba:
                pass
        return False

    # WRITE-AHEAD: BEFORE the network call we record that a send is in progress.
    rekord["allapot"] = allapot.KULDES_ALATT
    allapot.ment(kornyezet, rekord)

    kanonikus = rekord["kanonikus"]
    letrehozva = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(most))
    try:
        eredmeny = halo.letrehoz(kanonikus, letrehozva,
                                 reply_engedelyezve=bool(
                                     kanonikus.get("valasz_cel")))
    except Exception as e:  # noqa: BLE001 — NO automatic retry
        rekord["allapot"] = allapot.KEZI_ELLENORZES
        rekord["ok"] = ["kuldes_hiba:" + type(e).__name__]
        allapot.ment(kornyezet, rekord)
        allapot.naploz(kornyezet, {"esemeny": "kuldes_hiba",
                                   "elonezet": rekord["azonosito"],
                                   "hiba_tipus": type(e).__name__})
        uzenetek.append("error while sending (%s) — I will NOT retry."
                        % type(e).__name__)
        return False

    rekord["poszt_uri"] = szures.egysoros((eredmeny or {}).get("uri") or "", 300)
    rekord["allapot"] = allapot.ELKULDVE
    allapot.ment(kornyezet, rekord)
    allapot.naploz(kornyezet, {"esemeny": "elkuldve",
                               "elonezet": rekord["azonosito"],
                               "poszt_uri": rekord["poszt_uri"]})
    try:
        bot.kuld(elonezet.visszaigazolas(rekord["poszt_uri"],
                                         elonezet.jelolo_keszit()))
    except telegram_or.TelegramHiba:
        uzenetek.append("the post went out, but the confirmation did not.")
    return True


def _akcio_ujraellenorzes(halo, rekord, sajat_did, most):
    """Once more BEFORE execution: live target check + cap. Reasons."""
    kanonikus = rekord["kanonikus"]
    tarolt_cel = rekord.get("cel") or {}
    tipus = kanonikus.get("tipus")
    if tipus == "kovetes":
        profil = halozat("getProfile", halo.profil, kanonikus.get("cel_did"))
        friss, okok = kimeno.kovetes_cel_ellenorzes(
            profil, None, kanonikus.get("cel_did"), sajat_did)
        if okok:
            return okok
        if (friss.get("handle") or "") != (kanonikus.get("cel_handle") or ""):
            return ["handle_megvaltozott"]
    else:
        poszt = halozat("getPosts", halo.poszt, kanonikus.get("cel_uri"))
        friss, okok = kimeno.kedveles_cel_ellenorzes(
            poszt, kanonikus.get("cel_uri"), sajat_did)
        if okok:
            return okok
        if friss.get("cid") != kanonikus.get("cel_cid"):
            return ["celpont_megvaltozott"]
        if (friss.get("szerzo_handle") or "") != \
                (tarolt_cel.get("szerzo_handle") or ""):
            return ["handle_megvaltozott"]
    _darab, okok = _akcio_plafon(halo, tipus, most)
    return okok


def akcio_vegrehajtas(kornyezet, halo, bot, rekord, sajat_did, most,
                      uzenetek):
    """Executing the approved FOLLOW / LIKE from the STORED canonical
    record. The same sequence as `posztolas`: integrity → fresh
    re-check → write-ahead `kuldes_alatt` → ONE write call, without
    retry → `elkuldve` + the uri of the created record into the log."""
    tipus = (rekord.get("kanonikus") or {}).get("tipus")
    rovid = (rekord.get("azonosito") or "?")[:6]
    if not allapot.rekord_ep_e(rekord):
        rekord["allapot"] = allapot.KEZI_ELLENORZES
        rekord["ok"] = ["tarolt_rekord_serult"]
        allapot.ment(kornyezet, rekord)
        uzenetek.append("the stored record is damaged (%s) — not executing it."
                        % rovid)
        return False

    try:
        okok = _akcio_ujraellenorzes(halo, rekord, sajat_did, most)
    except UzemHiba as e:
        uzenetek.append("execution postponed (%s): %s" % (rovid, str(e)))
        return False
    if okok:
        if all(o in HALASZTHATO_OKOK for o in okok):
            uzenetek.append("execution postponed (%s): %s — can be approved "
                            "again later." % (rovid, ", ".join(okok)))
            return False
        rekord["allapot"] = allapot.ELUTASITVA
        rekord["ok"] = okok
        allapot.ment(kornyezet, rekord)
        allapot.naploz(kornyezet, {"esemeny": "kuldes_elutasitva",
                                   "elonezet": rekord["azonosito"],
                                   "tipus": tipus, "ok": okok})
        uzenetek.append("the approved item cannot be executed after all (%s): %s"
                        % (rovid, ", ".join(okok)))
        if "handle_megvaltozott" in okok:
            uzenet = ("the target changed their handle since the approval "
                      "(%s). I did NOT execute it. If you still want it, "
                      "drop the draft in again — you will get a fresh preview."
                      % rovid)
            uzenetek.append(uzenet)
            try:
                bot.kuld(elonezet.ertesites(uzenet, elonezet.jelolo_keszit()))
            except telegram_or.TelegramHiba:
                pass
        return False

    # WRITE-AHEAD: BEFORE the network call we record that a send is in progress.
    rekord["allapot"] = allapot.KULDES_ALATT
    allapot.ment(kornyezet, rekord)

    kanonikus = rekord["kanonikus"]
    letrehozva = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(most))
    try:
        if tipus == "kovetes":
            eredmeny = halo.kovet(kanonikus["cel_did"], letrehozva)
        else:
            eredmeny = halo.kedvel(kanonikus["cel_uri"], kanonikus["cel_cid"],
                                   letrehozva)
    except Exception as e:  # noqa: BLE001 — NO automatic retry
        rekord["allapot"] = allapot.KEZI_ELLENORZES
        rekord["ok"] = ["kuldes_hiba:" + type(e).__name__]
        allapot.ment(kornyezet, rekord)
        allapot.naploz(kornyezet, {"esemeny": "kuldes_hiba",
                                   "elonezet": rekord["azonosito"],
                                   "tipus": tipus,
                                   "hiba_tipus": type(e).__name__})
        uzenetek.append("error during execution (%s) — I will NOT retry."
                        % type(e).__name__)
        return False

    uri_mezo = "follow_uri" if tipus == "kovetes" else "like_uri"
    rekord[uri_mezo] = szures.egysoros((eredmeny or {}).get("uri") or "", 300)
    rekord["allapot"] = allapot.ELKULDVE
    allapot.ment(kornyezet, rekord)
    allapot.naploz(kornyezet, {"esemeny": "elkuldve",
                               "elonezet": rekord["azonosito"],
                               "tipus": tipus,
                               uri_mezo: rekord[uri_mezo]})
    try:
        bot.kuld(elonezet.akcio_visszaigazolas(tipus, rekord[uri_mezo],
                                               elonezet.jelolo_keszit()))
    except telegram_or.TelegramHiba:
        uzenetek.append("the action was executed, but the confirmation did not "
                        "go out.")
    return True


def jovahagyasok_menet(kornyezet, halo, bot, rekordok, sajat_did,
                       sajat_posztok, most, uzenetek):
    """Processing the bot's messages. Returns: how many posts went out."""
    offset = allapot.offset_olvas(kornyezet)
    try:
        frissitesek = bot.frissitesek(offset)
    except telegram_or.TelegramHiba as e:
        uzenetek.append("Telegram is not reachable (%s)" % str(e))
        return 0
    kikuldve = 0
    for frissites in frissitesek or []:
        update_id = frissites.get("update_id")
        if isinstance(update_id, int):
            allapot.offset_ir(kornyezet, update_id + 1)
        dontes, valasz_id, sajat_id = telegram_or.dontes(
            frissites, bot.owner_user_id(), bot.owner_chat_id())
        if dontes == telegram_or.FIGYELMEN_KIVUL:
            continue  # stranger, command, other text → not interested
        if allapot.felhasznalt_e(kornyezet, sajat_id):
            continue  # this message was already processed (double YES)
        rekord = allapot.message_id_szerint(rekordok, valasz_id)
        if rekord is None or rekord.get("allapot") != allapot.VAR:
            continue  # not our preview, or already closed
        if telegram_or.lejart_e(rekord.get("letrehozva_ts"), most):
            rekord["allapot"] = allapot.LEJART
            rekord["ok"] = ["lejart_48_ora"]
            allapot.ment(kornyezet, rekord)
            continue

        allapot.felhasznalt_rogzit(kornyezet, sajat_id, rekord["azonosito"],
                                   dontes)
        if dontes == telegram_or.ELVET:
            rekord["allapot"] = allapot.ELVETVE
            rekord["ok"] = ["owner_elvetette"]
            allapot.ment(kornyezet, rekord)
            allapot.naploz(kornyezet, {"esemeny": "elvetve",
                                       "elonezet": rekord["azonosito"]})
            continue

        if posztolas(kornyezet, halo, bot, rekord, sajat_did, sajat_posztok,
                     most, uzenetek):
            kikuldve += 1
            if rekord["kanonikus"].get("tipus") in kimeno.AKCIO_TIPUSOK:
                # A follow / like is NOT a post: it does not count towards the
                # post cap or the duplicate-post filter.
                continue
            # The freshly sent post counts towards the cap immediately.
            sajat_posztok.append({
                "ts": most,
                "szoveg": rekord["kanonikus"].get("szoveg") or "",
                "szulo_uri": (rekord["kanonikus"].get("valasz_cel")
                              or {}).get("parent_uri") or "",
                "uri": rekord.get("poszt_uri") or "",
            })
    return kikuldve


# =========================================================================
# 4. ONE FULL ROUND
# =========================================================================

def egy_kor(kornyezet, halo, bot, idovonal_darab=0, bejovo_is=True,
            indulasi_uzenetek=()):
    """One full round: stuck items → incoming → outgoing → approvals → state.

    `indulasi_uzenetek`: the startup warnings (e.g. the untrusted user's
    administrator group membership). They are added to the outward state file
    in EVERY round, so they are visible without a console as well (K''4).

    Returns: the state dictionary written outward.
    """
    most = time.time()
    uzenetek = list(indulasi_uzenetek)
    rekordok = allapot.betolt_mind(kornyezet)
    serult = allapot.serult(rekordok)
    if serult:
        uzenetek.append("damaged state file(s): %d — manual check needed."
                        % len(serult))
        rekordok = [r for r in rekordok if not r.get("_serult")]

    beragadtak_kezelese(kornyezet, bot, rekordok, uzenetek)
    _elokeszitesben_ragadtak(kornyezet, rekordok, most, uzenetek)
    lejartak_kezelese(kornyezet, rekordok, most)

    sajat_did = halozat("login", halo.sajat_did)
    if not sajat_did:
        raise UzemHiba("the login did not return our own DID — not posting.")
    feed = halozat("getAuthorFeed", halo.sajat_feed)
    # M''3 — FAIL-CLOSED: if pagination did not reach the end of the 24-hour
    # window, the cap may not be true. In that case we do NOT post.
    if not (feed or {}).get("ablak_lefedve"):
        raise UzemHiba("paginating our own feed did not reach the end of the "
                       "24-hour window — the cap cannot be verified, fail-closed.")
    sajat_posztok = kimeno.sajat_posztok_a_feedbol(feed, sajat_did)
    ismeretlen_ideju = kimeno.ismeretlen_ideju_darab(sajat_posztok)
    if ismeretlen_ideju:
        uzenetek.append("in our own feed the time of %d post(s) cannot be "
                        "parsed — counted towards the daily cap, not the "
                        "ten-minute limit." % ismeretlen_ideju)

    if bejovo_is:
        valasz = halozat("listNotifications", halo.ertesitesek)
        ido = (halozat("getTimeline", halo.idovonal, idovonal_darab)
               if idovonal_darab else None)
        uj, gyanus, forgattunk = bejovo.irasa(
            kornyezet, bejovo.gyujtes(valasz, ido))
        uzenetek.append("incoming: %d new item(s) (%d with suspicion flags)%s"
                        % (uj, gyanus, ", inbox rotated" if forgattunk else ""))

    mintak, megjegyzes = szures.tilto_mintak_biztos(kornyezet.tilto_mintak)
    if megjegyzes:
        uzenetek.append(megjegyzes)
    # Kö5: a damaged allowlist does not stop the guard, but it becomes VISIBLE.
    _, engedelylista_megjegyzes = szures.engedelylista_biztos(
        kornyezet.szivesen_latnak)
    if engedelylista_megjegyzes:
        uzenetek.append(engedelylista_megjegyzes)

    elutasitottak = kimeno_menet(kornyezet, halo, bot, mintak, sajat_did,
                                 sajat_posztok, most, rekordok, uzenetek)
    kikuldve = jovahagyasok_menet(kornyezet, halo, bot, rekordok, sajat_did,
                                  sajat_posztok, most, uzenetek)
    if kikuldve:
        uzenetek.append("sent / executed: %d item(s)" % kikuldve)

    # Kö6 — maintenance: old, CLOSED records go to the archive, the log of
    # used approvals is trimmed. This way the state does not grow without bound.
    archivalt = allapot.archivalas(kornyezet, most)
    if archivalt:
        uzenetek.append("archived: %d old, closed item(s)" % archivalt)
    allapot.felhasznalt_nyeses(kornyezet, most)

    tetelek = [allapot.kifele_tetel(r) for r in allapot.betolt_mind(kornyezet)
               if not r.get("_serult")]
    tetelek += elutasitottak
    adat = allapot.kifele_allapot(kornyezet, most, "fut", tetelek, uzenetek)
    allapot.kifele_ir(kornyezet, adat)
    return adat
