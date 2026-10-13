"""or.py — the BSKY-GUARD. Task Scheduler starts it on behalf of the separate
Windows user (`bsky-guard`).

    python or.py --ellenoriz     # ONLY the permission self-check
    python or.py --ki-vagyok     # from.id of the first incoming Telegram message
    python or.py --szaraz        # full run, but NOTHING goes out
    python or.py --egyszer       # one round, then exit
    python or.py                 # continuous run (this is what the scheduler runs)

Security gates at startup (all FAIL-CLOSED):
  * NTFS permission check — if the secret is readable, or the program/state is
    writable, by the untrusted user, the guard does NOT start;
  * a single instance (lock file in the `allapot\\` folder).

NO secret goes to the console: no token, no password, no chat_id, no
user_id (`--ki-vagyok` is the ONLY exception, and even it writes only to the
console, never to a file).
"""

import argparse
import os
import shutil
import sys
import time

import allapot
import beallitasok
import elonezet
import jogok
import motor
import tarolas_or
import telegram_or


def _allapot_hiba(kornyezet, uzenetek):
    """Write a startup error to the shared state file (without secrets)."""
    try:
        allapot.kifele_ir(kornyezet, allapot.kifele_allapot(
            kornyezet, time.time(), "nem_indul", [], uzenetek))
    except OSError:
        pass


def jogok_kapu(kornyezet, kiir, lekero=None, admin_lekero=None,
               tulajdonos_lekero=None):
    """`(rendben_e, figyelmeztetesek)` — (ok?, warnings).

    The warning does NOT only go to the console (K''4). In production the guard
    runs as a scheduled task, without a console — there `kiir` would write into
    the void. So the caller puts the returned warnings into the
    `kozos\\be\\allapot.json` file AND also sends them in the startup Telegram message.
    """
    try:
        hibak = jogok.jog_ellenorzes(
            kornyezet, lekero or jogok.icacls_lekero,
            tulajdonos_lekero=tulajdonos_lekero)
    except jogok.JogHiba as e:
        hibak = [str(e)]
    if hibak:
        kiir("THE GUARD WILL NOT START — permission error:")
        for h in hibak:
            kiir("  - " + h)
        kiir("Fix: INSTALL.md, chapter 3 (icacls commands).")
        _allapot_hiba(kornyezet, hibak)
        return False, hibak
    figyelmeztetesek = []
    figyelmeztetes = jogok.admin_tagsag_figyelmeztetes(admin_lekero)
    if figyelmeztetes:
        kiir(figyelmeztetes)
        figyelmeztetesek.append(figyelmeztetes)
    return True, figyelmeztetesek


def ki_vagyok(kornyezet, kiir):
    """from.id and chat.id of the first incoming message — ONLY to the console.

    The owner runs this once to learn their own ids and writes them
    into the `titok\\or_bot.env` file. NOTHING is written to a file.
    """
    kulcs = tarolas_or.kulcs_ertek_betolt(kornyezet.or_bot_kulcs,
                                          ("OR_BOT_TOKEN",))
    kiir("Now send a message (anything) to the NEW bot from your phone.")
    kiir("Waiting for at most 2 minutes…")
    hatarido = time.time() + 120
    while time.time() < hatarido:
        try:
            frissitesek = telegram_or.frissitesek(kulcs["OR_BOT_TOKEN"], None)
        except telegram_or.TelegramHiba as e:
            kiir("Telegram error: " + str(e))
            return 2
        for frissites in frissitesek or []:
            uzenet = frissites.get("message") or {}
            kuldo = uzenet.get("from") or {}
            chat = uzenet.get("chat") or {}
            if kuldo.get("id") is None:
                continue
            kiir("")
            kiir("OWNER_USER_ID=%s" % kuldo.get("id"))
            kiir("OWNER_CHAT_ID=%s" % chat.get("id"))
            kiir("(chat type: %s — it must be \"private\")" % chat.get("type"))
            kiir("Write these two lines into the titok\\or_bot.env file.")
            return 0
    kiir("No message arrived. Try again.")
    return 1


def indulasi_ertesites(bot, figyelmeztetesek, kiir):
    """The administrator warning is ALSO sent to the owner (K''4).

    No secret goes into it: the sentences produced by `jogok` contain no
    token, password, id, or full path.
    """
    if not figyelmeztetesek:
        return
    uzenet = ("startup — warning: " + " | ".join(figyelmeztetesek))
    try:
        bot.kuld(elonezet.ertesites(uzenet, elonezet.jelolo_keszit()))
    except telegram_or.TelegramHiba:
        kiir("the warning could not be sent over Telegram.")


def fokor(kornyezet, kiir, szaraz=False, egyszer=False, idovonal_darab=0,
          figyelmeztetesek=()):
    """The continuous run. The Telegram long-poll sets the pace."""
    halo = motor.EloHalo(kornyezet)
    bot = motor.SzarazBot(kiir) if szaraz else motor.EloBot(kornyezet)
    utolso_bejovo = 0.0
    periodus = max(beallitasok.BEJOVO_PERIODUS_ALAP,
                   beallitasok.BEJOVO_PERIODUS_MIN)
    indulasi_ertesites(bot, list(figyelmeztetesek), kiir)
    while True:
        most = time.time()
        bejovo_is = (most - utolso_bejovo) >= periodus
        try:
            adat = motor.egy_kor(kornyezet, halo, bot,
                                 idovonal_darab=idovonal_darab,
                                 bejovo_is=bejovo_is,
                                 indulasi_uzenetek=figyelmeztetesek)
        except motor.UzemHiba as e:
            kiir("OPERATIONAL OBSTACLE: %s" % str(e))
            _allapot_hiba(kornyezet, [str(e)])
            if egyszer:
                return 2
            time.sleep(60)
            continue
        if bejovo_is:
            utolso_bejovo = most
        for uzenet in adat.get("uzenetek", []):
            kiir(uzenet)
        if egyszer or szaraz:
            return 0
        # The live bot's long-poll has already "waited" ~25 s; the rest is spent here.
        time.sleep(5)


def szaraz_homokozo_elokeszit(kornyezet, kiir):
    """Sandbox of the `--szaraz` (dry) run: a SNAPSHOT of the live state + a
    COPY of the dropped-in drafts. After this the run does not touch the live tree."""
    h = kornyezet.szaraz_homokozo()
    for mappa in (h.allapot, h.be, h.ki, h.elonezetek, h.feldolgozott, h.varo,
                  h.archiv, h.zart):
        tarolas_or.mappa_keszit(mappa)
    # Snapshot of the live records, so that the caps and pending previews look
    # the same as in production. (Always fresh: old sandbox records are deleted.)
    for nev in os.listdir(h.elonezetek):
        if nev.endswith(".json"):
            os.remove(os.path.join(h.elonezetek, nev))
    masolt = 0
    if os.path.isdir(kornyezet.elonezetek):
        for nev in sorted(os.listdir(kornyezet.elonezetek)):
            if nev.endswith(".json"):
                shutil.copyfile(os.path.join(kornyezet.elonezetek, nev),
                                os.path.join(h.elonezetek, nev))
                masolt += 1
    for nev in os.listdir(h.ki):
        os.remove(os.path.join(h.ki, nev))
    piszkozatok = 0
    if os.path.isdir(kornyezet.ki):
        for nev in sorted(os.listdir(kornyezet.ki)):
            forras = os.path.join(kornyezet.ki, nev)
            if nev.endswith(".json") and os.path.isfile(forras):
                shutil.copyfile(forras, os.path.join(h.ki, nev))
                piszkozatok += 1
    kiir("DRY MODE: the state is in the sandbox (%s); %d live records "
         "copied, a COPY of %d drafts is the input — I do not touch the live "
         "drop folder or state." % (h.allapot, masolt, piszkozatok))
    return h


def main(argv=None, kiir=print, kornyezet=None, jog_lekero=None,
         admin_lekero=None, tulajdonos_lekero=None):
    tarolas_or.konzol_utf8()
    ertelmezo = argparse.ArgumentParser(description="BskyGuard")
    ertelmezo.add_argument("--gyoker", default=None,
                           help="the guard's root folder (default: C:\\BskyGuard)")
    ertelmezo.add_argument("--ellenoriz", action="store_true",
                           help="only the permission self-check")
    ertelmezo.add_argument("--ki-vagyok", dest="ki_vagyok", action="store_true",
                           help="print from.id of the first incoming message to the console")
    ertelmezo.add_argument("--szaraz", action="store_true",
                           help="full run, but NOTHING goes out")
    ertelmezo.add_argument("--egyszer", action="store_true",
                           help="one round, then exit")
    ertelmezo.add_argument("--idovonal", type=int, default=0,
                           help="also the last N posts of followed accounts (default 0)")
    args = ertelmezo.parse_args(argv)

    kornyezet = kornyezet or beallitasok.Kornyezet(args.gyoker)

    rendben, figyelmeztetesek = jogok_kapu(kornyezet, kiir, jog_lekero,
                                           admin_lekero, tulajdonos_lekero)
    if not rendben:
        return 3
    if args.ellenoriz:
        kiir("Permissions are OK. The guard can start.")
        return 0

    tarolas_or.mappa_keszit(kornyezet.allapot)
    if args.szaraz:
        # The permission gate checked the LIVE tree (that is the point of the
        # dry run); from here on, however, all state goes to the sandbox, and
        # only a COPY is made from the drop folder. See `Kornyezet.szaraz_homokozo`.
        kornyezet = szaraz_homokozo_elokeszit(kornyezet, kiir)
    if args.ki_vagyok:
        try:
            return ki_vagyok(kornyezet, kiir)
        except (OSError, ValueError) as e:
            kiir("ERROR: " + str(e))
            return 2

    zar = os.path.join(kornyezet.allapot, "or.lock")
    try:
        with tarolas_or.iro_zar(zar):
            return fokor(kornyezet, kiir, szaraz=args.szaraz,
                         egyszer=args.egyszer,
                         idovonal_darab=args.idovonal,
                         figyelmeztetesek=figyelmeztetesek)
    except tarolas_or.ZarHiba as e:
        kiir("Not starting: " + str(e))
        return 4
    except (OSError, ValueError) as e:
        kiir("ERROR (%s) — the details are deliberately not shown."
             % type(e).__name__)
        _allapot_hiba(kornyezet, ["startup error: " + type(e).__name__])
        return 2


if __name__ == "__main__":
    sys.exit(main())
