"""beallitasok.py — the guard's paths and constants. NO logic.

The guard lives under the `C:\\BskyGuard\\` tree. The tests build the same
structure in a temporary folder, so every path comes from ONE object
(`Kornyezet`), and there is no hard-coded, module-level absolute path
anywhere.
"""

import os

# --- the untrusted side ---------------------------------------------------
# The Windows user that runs the agent. The MAIN actor of the threat model:
# it can write anything this user can write. The guard checks that this user
# has NO rights on the protected folders.
# Set it with the BSKY_AGENT_USER environment variable (in the guard's own
# environment). The placeholder default matches no real account, so a missing
# value makes the permission check fail closed (the guard refuses to start).
NEM_MEGBIZHATO_FELHASZNALO = os.environ.get("BSKY_AGENT_USER", "agent-user")

# The guard's own (trusted) Windows user; override with BSKY_GUARD_USER.
OR_FELHASZNALO = os.environ.get("BSKY_GUARD_USER", "bsky-guard")

# The ALLOWLIST of the permission check. Anyone not here is an "outsider":
# if they can write the protected folder (or read the secret), the guard does
# NOT start. Windows shows the same group under different names in Hungarian
# and English, so both forms are listed. The list may be EXTENDED, but
# narrowing it is safe.
# Hungarian language data: "RENDSZER" (SYSTEM) and "Rendszergazdák" /
# "Rendszergazdak" (Administrators) are the Hungarian-locale group names; for
# another Windows display language, add that locale's names here.
ENGEDETT_IDENTITASOK = (
    "SYSTEM", "RENDSZER",
    "Administrators", "Rendszergazdák", "Rendszergazdak",
    "TrustedInstaller",
    OR_FELHASZNALO,
)

# --- ceilings (all computed from the NETWORK truth) -----------------------
NAPI_MAX = 5                    # max 5 posts / 24 hours
KEZDEMENYEZETT_NAPI_MAX = 2     # of which max 2 unsolicited
PERC_KOZ = 10 * 60              # min. 10 minutes between two posts
ABLAK_24 = 24 * 3600
FEED_LIMIT = 50                 # how many of our own posts we fetch on ONE page

# Pagination of `getAuthorFeed`. The ceiling is only true if we paged back to
# the END of the 24-hour window. If even this many pages are not enough,
# FAIL-CLOSED: we do not post.
FEED_MAX_OLDAL = 10
# We do not rely on the service's default: our replies must be in the feed
# too, otherwise the ceiling would not see the replies.
FEED_SZURO = "posts_with_replies"

# At most this many previews may await approval at the same time.
# (2026-10-02: follow and like previews also count toward this budget.)
FUGGO_MAX = 3

# --- FOLLOW and LIKE (2026-10-02) ----------------------------------------
# Two new actions, WITHOUT TEXT: an `app.bsky.graph.follow` and an
# `app.bsky.feed.like` record. They pass through the same gate as a post:
# draft → network check → preview → owner approval.
#
# The ceilings are ROLLING 24-hour ones (`ABLAK_24`), like the post ceiling,
# and are likewise computed from the NETWORK truth: from the
# `com.atproto.repo.listRecords` response of our own account (newest records
# first), not from a local log. A record with an unparseable time counts
# (the stricter direction). The two ceilings are INDEPENDENT of each other
# and of the post ceiling.
KOVETES_NAPI_MAX = 5            # max 5 new follows / 24 hours
KEDVELES_NAPI_MAX = 10          # max 10 likes / 24 hours
# Page size of `listRecords`. Always larger than the ceilings above, so one
# page always settles the question; if not (full page, every record inside
# the window), the guard reports the ceiling as reached, fail-closed.
AKCIO_REKORD_LIMIT = 100

# The target post's ANCESTOR CHAIN (towards the top of the thread) is walked
# for at most this many steps (K'''2). If we do not reach the top within
# that, the item is REJECTED: a malicious or faulty service must not be able
# to force us into an endless chain, and no ancestor may remain unchecked.
#
# Kö''''4: raised from eight to twenty-five. On Bluesky conversations deeper
# than ten are ordinary, and with the earlier limit a reply to such a thread
# got a FINAL rejection. The direction of rejection stays (that is the point
# of the chain check); only the threshold became realistic. Cost: one
# `getPosts` call per step on deeper threads — which is why we raise it no
# further.
FELMENO_MAX_LEPES = 25

# J6/(B) — the window of the "ONGOING CONVERSATION" rule. If X replied to OUR
# post and then keeps writing under their OWN reply in the same chain, that
# is not an unsolicited move on our part but the middle of a conversation
# already started. The gate must not stay open forever, though: X's reply
# addressed to us may be at most this old.
FOLYO_BESZELGETES_ABLAK = 24 * 3600
# A network timestamp can be in the future (the `createdAt` field is written
# by the author). We do not believe a future offset larger than this: the
# rule is then NOT satisfied.
IDO_JOVOBELI_TURES = 5 * 60

# A'''''1 — PER-ROUND upper limit on chain fetches. `FELMENO_MAX_LEPES`
# limits per draft, and `FUGGO_MAX` only catches the SUCCESSFUL items. Fifty
# drafts, each pointing to a malicious thread of twenty-five steps, would thus
# have caused over a thousand network calls per round: not a leak, but the
# round's run time stretches, and with it approval monitoring is delayed too.
# Above the budget the remaining drafts are postponed to the next round —
# they stay in the queue, nothing is lost.
LANC_KERET_KORONKENT = 200

# A preview expires after this much time.
ELONEZET_ERVENYESSEG = 48 * 3600

# --- draft ----------------------------------------------------------------
PISZKOZAT_MAX_BAJT = 8 * 1024
# The UPPER limit for the note field according to the specification.
MEGJEGYZES_MAX = 200
# The limit actually accepted (Kö2): the note is ONE line, and short, so that
# it cannot draw a "paragraph" directly above the approval line.
MEGJEGYZES_SOR_MAX = 80

# This many NEW drafts are taken in from the drop folder per round (LOW 3).
PISZKOZAT_KORONKENT_MAX = 50
# At most this many drafts may be in the protected queue at once; above that
# nothing new is taken in until the queue empties.
VARO_MAX = 50
# A draft stuck in the queue is closed after this much time.
VARO_ELAVULAS = 7 * 24 * 3600

# Kö'''2: a FOLDER put into the drop folder (or a locked, denied file) must
# not get stuck or use up the per-round budget. After this many failed
# attempts the name is marked as permanently skipped, and this is reported in
# the state file.
BEDOBO_PROBA_MAX = 3
# Upper limit of the skip list: the untrusted side must not be able to grow
# the guard's state without bound.
KIHAGYANDO_MAX = 200
# A''''1: skip-list entries EXPIRE. Previously, when the list filled up, it
# was exactly the PERMANENTLY skipped ones (tried the most times) that stayed
# in, and there was no expiry either — so the untrusted side could have made
# two hundred file names unusable forever, which only an administrator could
# have repaired. After one week every entry disappears on its own.
KIHAGYANDO_ELAVULAS = 7 * 24 * 3600
# Content fingerprints (M''''2) live this long; a dropped file older than
# this would have expired in the queue anyway.
BEDOBOTT_HASH_ELAVULAS = 7 * 24 * 3600
BEDOBOTT_HASH_MAX = 200

# --- state maintenance (Kö6) ----------------------------------------------
# A CLOSED preview record older than this goes under `allapot\archiv\`.
ARCHIVALAS_KOR = 70 * 24 * 3600
# Dedup window of the log of used approvals. Because of the 48-hour expiry,
# a message older than this could not approve anything anyway.
FELHASZNALT_ABLAK = 7 * 24 * 3600

# --- incoming -------------------------------------------------------------
BEJOVO_PERIODUS_ALAP = 10 * 60   # 10 minutes
BEJOVO_PERIODUS_MIN = 5 * 60     # never more often than this
INBOX_MAX_BAJT = 4 * 1024 * 1024  # rotate above this
INBOX_FORGATAS_DARAB = 3          # this many old files are kept

# --- Telegram -------------------------------------------------------------
TELEGRAM_LONGPOLL = 25           # getUpdates timeout in seconds
TELEGRAM_MAX_VALASZ = 2 * 1024 * 1024


class Kornyezet:
    """All of the guard's paths in one place.

    `gyoker` defaults to `C:\\BskyGuard`. The tests pass a temporary folder —
    so a test run never touches the production tree.
    """

    def __init__(self, gyoker=None):
        self.gyoker = os.path.abspath(gyoker or r"C:\BskyGuard")
        self.titok = os.path.join(self.gyoker, "titok")
        self.program = os.path.join(self.gyoker, "program")
        self.allapot = os.path.join(self.gyoker, "allapot")
        self.kozos = os.path.join(self.gyoker, "kozos")
        self.be = os.path.join(self.kozos, "be")
        self.ki = os.path.join(self.kozos, "ki")

        # titok/ — readable ONLY by the guard's user
        self.bsky_kulcs = os.path.join(self.titok, "bsky_kulcs.env")
        self.or_bot_kulcs = os.path.join(self.titok, "or_bot.env")

        self.tilto_mintak = os.path.join(self.program, "tilto_mintak.json")
        self.szivesen_latnak = os.path.join(self.program, "szivesen_latnak.json")

        # allapot/ — the guard's own, ANCHOR state
        self.elonezetek = os.path.join(self.allapot, "elonezetek")
        self.feldolgozott = os.path.join(self.allapot, "feldolgozott")
        # A dropped draft is FIRST carried over here (Kö1): the guard reads it
        # only from here, its own protected area. So the untrusted side cannot
        # touch the file between reading and processing.
        # M''''2: taking over is a COPY, not a move. On Windows a move would
        # have kept the access control list inherited from the drop folder and
        # the untrusted user's ownership; a new file created here, by
        # contrast, inherits from THIS folder, and its owner is the guard.
        self.varo = os.path.join(self.allapot, "varo")
        self.archiv = os.path.join(self.allapot, "archiv")
        # K'''''1/c — CLOSED folder. The rest of `allapot` CAN be read by the
        # untrusted user (deliberately: that is where it gets its bearings).
        # But everything derived from the content of DROPPED files goes here,
        # and the untrusted user has NO right at all on this folder (its own
        # `icacls` line, without inheritance).
        self.zart = os.path.join(self.allapot, "zart")
        # HMAC key of the content fingerprints. Without it a plain sha256
        # could be reversed for a short secret (dictionary attack), and the
        # fingerprint would have ended up in a readable place.
        self.bedobott_kulcs = os.path.join(self.zart, "ujjlenyomat_kulcs.json")
        self.naplo = os.path.join(self.allapot, "naplo.jsonl")
        self.offset = os.path.join(self.allapot, "telegram_offset.json")
        self.felhasznalt = os.path.join(self.allapot, "felhasznalt_jovahagyasok.jsonl")
        # Names of drop-folder entries that cannot be taken over (folder,
        # locked or denied file) and their number of failed attempts (Kö'''2).
        self.kihagyando = os.path.join(self.allapot, "kihagyando_bedobott.json")
        # M''''2: the CONTENT fingerprint of dropped drafts that were already
        # taken over into the queue but could not be deleted from the drop
        # folder. Without this, a stuck file would produce a new preview every
        # round.
        # K'''''1/c: in the CLOSED folder, with HMAC — see `self.zart`.
        self.bedobott_hashek = os.path.join(self.zart,
                                            "bedobott_hashek.json")

        self.inbox = os.path.join(self.be, "inbox.jsonl")
        self.allapot_kifele = os.path.join(self.be, "allapot.json")

    # The folders the guard may create (it does NOT build the protected tree).
    # `varo`, `feldolgozott` and `zart` are created by the installer, with
    # their own `icacls` line without inheritance. If the guard created them,
    # they would inherit the permissions of the `allapot` folder, where the
    # untrusted user has read access — exactly the opposite of what these
    # folders are for. If they are missing, `jogok.jog_ellenorzes` stops the
    # startup before anything gets created.
    def sajat_mappak(self):
        return (self.allapot, self.elonezetek, self.archiv, self.be)

    def szaraz_homokozo(self):
        """The same environment, but with the STATE in a separate sandbox.

        The `--szaraz` run calls it AFTER the permission gate. While the dry
        run used the production folders, it actually TOOK OVER a dropped draft
        (deleted it from the drop folder and recorded a preview with a made-up
        Telegram id in the production state), and overwrote
        `kozos/be/allapot.json`; the production guard then waited 48 hours for
        approval of a preview that was never sent. So in dry mode:
          * every state path goes under `allapot_szaraz/` (records, queue,
            processed, closed folder with its own key, log, lock file);
          * the `kozos/be` output also goes into the sandbox (the production
            status report and incoming mailbox stay untouched);
          * instead of the drop folder (`kozos/ki`), a COPY of the drafts is
            the input — the dry run does not touch the original files.
        The secret and program paths (keys, allowlist, block filter) are
        unchanged: those are exactly what the dry test exercises.
        """
        h = Kornyezet.__new__(Kornyezet)
        h.__dict__.update(self.__dict__)
        h.eles_ki = self.ki
        h.allapot = os.path.join(self.gyoker, "allapot_szaraz")
        h.be = os.path.join(h.allapot, "be")
        h.ki = os.path.join(h.allapot, "ki_masolat")
        h.elonezetek = os.path.join(h.allapot, "elonezetek")
        h.feldolgozott = os.path.join(h.allapot, "feldolgozott")
        h.varo = os.path.join(h.allapot, "varo")
        h.archiv = os.path.join(h.allapot, "archiv")
        h.zart = os.path.join(h.allapot, "zart")
        h.bedobott_kulcs = os.path.join(h.zart, "ujjlenyomat_kulcs.json")
        h.naplo = os.path.join(h.allapot, "naplo.jsonl")
        h.offset = os.path.join(h.allapot, "telegram_offset.json")
        h.felhasznalt = os.path.join(h.allapot,
                                     "felhasznalt_jovahagyasok.jsonl")
        h.kihagyando = os.path.join(h.allapot, "kihagyando_bedobott.json")
        h.bedobott_hashek = os.path.join(h.zart, "bedobott_hashek.json")
        h.inbox = os.path.join(h.be, "inbox.jsonl")
        h.allapot_kifele = os.path.join(h.be, "allapot.json")
        return h

    def letezo_mappak(self):
        return (self.titok, self.program, self.allapot, self.be, self.ki)

# The Bluesky session token lives ~2 hours; the guard logs in again every 90
# minutes (~16 logins a day — far below the service's daily limit).
SESSION_MAX_KOR = 90 * 60
