"""szures.py — the security rules. EVERY rule is a separate, testable function.

This module knows NOTHING about the network, the files or Telegram:
it only computes from text (and from already-fetched data structures). That
makes it easy to test and easy to read.

Principle: if a rule is ambiguous, we choose the STRICTER interpretation.
"""

import hashlib
import json
import re
import unicodedata

# --- 0. HOMOGLYPH MAPPING -----------------------------------------------
#
# Sanitization removes INVISIBLE characters. Deceptive but visible homoglyphs
# (Cyrillic "а", Greek "ο", fullwidth "＝") need a separate, PER-CHARACTER
# (i.e. length-preserving) mapping. Preserving the length matters: the frame
# protection searches the NORMALIZED form but replaces in the ORIGINAL text,
# and for that the match positions must line up.
#
# Homoglyph table (language-independent data): (source chars, Latin lookalike)
# pairs; extend by adding further equal-length string pairs.

_CONFUSABLE_PAROK = [
    # Cyrillic → Latin
    ("аеорсухАВЕКМНОРСТУХіѕјԛԝ", "aeopcyxABEKMHOPCTYXisjqw"),
    # Greek → Latin
    ("οαερτυνικχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ", "oaeptvvikxABEZHIKMNOPTYX"),
    # equals-sign lookalikes
    ("＝⁼₌﹦═゠⹀", "======="),
    # hyphen lookalikes
    ("‐‑‒–—―﹘﹣－", "---------"),
    # other fullwidth punctuation
    ("：；，．／＼＇＂｜！？＃＠", ":;,./\\'\"|!?#@"),
]

_CONFUSABLE_TABLA = {}
for _forras, _cel in _CONFUSABLE_PAROK:
    assert len(_forras) == len(_cel)
    for _a, _b in zip(_forras, _cel):
        _CONFUSABLE_TABLA[ord(_a)] = _b


def homograf_normalizal(szoveg):
    """Per-character (length-PRESERVING) homoglyph mapping.

    Not NFKC — that does not preserve length. We use this function where the
    positions must match the original (frame protection).
    """
    if not szoveg:
        return ""
    return szoveg.translate(_CONFUSABLE_TABLA)


def osszehasonlito_alak(szoveg):
    """AGGRESSIVE normalization used for comparison (dedup, block filter).

    NFKC (the non-breaking space also becomes an ordinary space) + homoglyph
    mapping. It does NOT preserve length — so it is only suitable where we do
    not replace back.
    """
    if not szoveg:
        return ""
    szoveg = unicodedata.normalize("NFKC", szoveg)
    szoveg = homograf_normalizal(szoveg)
    # every space-category character becomes an ordinary space
    szoveg = "".join(" " if unicodedata.category(ch) == "Zs" else ch
                     for ch in szoveg)
    return szoveg


# --- 1. SANITIZATION -----------------------------------------------------

# The INVISIBLE code points. The logic is INVERTED (M''''1): we do not block a
# few known tricks, but every code point that is not visible by default —
# and the owner approves only what they can see. Even a single invisible code
# point is a covert channel: several bits per character, several hundred
# bytes in total in a 300-grapheme post.
#
# M'''''1 — THE LIST IS NOT A HAND-WRITTEN ENUMERATION. It comes from two
# sources:
#
#   1. CATEGORY RULE, from `unicodedata`, so ALWAYS up to date with Python's
#      Unicode table: `Cf` (format), `Cc` (control — newline and tab are
#      handled separately per the existing rule), `Cs` (surrogate half), `Co`
#      (private use), `Cn` (unassigned), `Zl`/`Zp` (line and paragraph
#      separator), plus non-ASCII `Zs` (space-like characters).
#   2. HARD-CODED RANGES: the FULL `Default_Ignorable_Code_Point` set as per
#      the Unicode `DerivedCoreProperties.txt`. Some of these (e.g. U+16FE4
#      Khitan filler, U+034F grapheme joiner, U+FE00–FE0F variation
#      selectors, U+17B4–17B5 Khmer vowels) are in category `Mn`, so the
#      category rule would NOT catch them — yet they do not show on screen.
#      We also added characters that are formally "visible" but in practice
#      draw empty space: the Braille blank (U+2800) and the Hangul filler
#      letters (U+115F–1160, U+3164, U+FFA0).
#
# EXCEPTIONS — and only these: the zero-width joiner (U+200D) and variation
# selector-16 (U+FE0F) in a strictly defined EMOJI CONTEXT, plus the tag
# characters of the THREE listed flag subsequences. The exceptions are handled
# at the start of `szanitalas`, BY NAME; in every other situation these also
# disappear. The amount of exceptions is limited by
# `rejtett_csatorna_ellenorzes`.
DEFAULT_IGNORABLE_TARTOMANYOK = (
    (0x00AD, 0x00AD),      # soft hyphen
    (0x034F, 0x034F),      # combining grapheme joiner
    (0x061C, 0x061C),      # Arabic letter mark
    (0x115F, 0x1160),      # Hangul filler (renders as blank)
    (0x17B4, 0x17B5),      # Khmer inherent vowels
    (0x180B, 0x180F),      # Mongolian variation selectors and vowel separator
    (0x200B, 0x200F),      # zero-width characters, LTR/RTL marks
    (0x202A, 0x202E),      # bidi embedding/override
    (0x2060, 0x206F),      # word joiner, invisible operators, bidi isolates
    (0x3164, 0x3164),      # Hangul filler letter
    (0xFE00, 0xFE0F),      # variation selectors (U+FE0F is an exception, see above)
    (0xFEFF, 0xFEFF),      # BOM / zero-width no-break space
    (0xFFA0, 0xFFA0),      # halfwidth Hangul filler
    (0xFFF0, 0xFFF8),      # unassigned, default ignorable
    (0x16FE4, 0x16FE4),    # Khitan filler (Unicode 13, category `Mn`)
    (0x1BCA0, 0x1BCA3),    # shorthand format controls
    (0x1D173, 0x1D17A),    # musical formatting symbols
    (0xE0000, 0xE0FFF),    # tags + ideographic variation selectors
)

# Characters that look visible but actually draw empty space. They are not
# part of the `Default_Ignorable` set, but the threat is the same.
LATSZOLAG_URES_TARTOMANYOK = (
    (0x2800, 0x2800),      # Braille blank
    (0xFFFC, 0xFFFC),      # object replacement character (Kö'''''3)
    (0xFFFD, 0xFFFD),      # replacement character: sign of an encoding error (Kö'''''3)
)

LATHATATLAN_TARTOMANYOK = (DEFAULT_IGNORABLE_TARTOMANYOK
                           + LATSZOLAG_URES_TARTOMANYOK)

# The categories that by themselves mean invisibility.
LATHATATLAN_KATEGORIAK = frozenset(("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"))

TILTOTT_KODPONTOK = set()
for _a, _b in LATHATATLAN_TARTOMANYOK:
    for _c in range(_a, _b + 1):
        TILTOTT_KODPONTOK.add(_c)


def lathatatlan_kodpont_e(kod):
    """Is this code point NOT visible by default?

    Based on the hard-coded ranges OR the category rule. Newline and tab are
    exceptions per the existing rule: `szanitalas` handles them separately
    (the newline is kept, the tab becomes a space).
    """
    if kod in TILTOTT_KODPONTOK:
        return True
    if kod < 0 or kod > 0x10FFFF:
        return True
    karakter = chr(kod)
    if karakter in ("\n", "\t"):
        return False
    kategoria = unicodedata.category(karakter)
    if kategoria in LATHATATLAN_KATEGORIAK:
        return True
    return kategoria == "Zs" and karakter != " "


def _nem_ascii_szokoz_e(ch):
    """Non-ASCII space-like character: category `Zs` EXCEPT the ordinary space.

    The non-breaking space and its relatives behave invisibly (they look like
    a space but are different code points), so they could bypass the block
    filter too.
    """
    return ch != " " and unicodedata.category(ch) == "Zs"


def lathatatlanok_nelkul(szoveg):
    """A copy of the text without ANY invisible code point.

    We also run the block filter on this form (Kö''''2), so that not even a
    single inserted invisible character can split a blocked word or an e-mail
    address. Here the exceptions are not exceptions: the ZWJ and the variation
    selector also disappear, because for comparison only the visible skeleton
    counts.
    """
    if not szoveg:
        return ""
    return "".join(ch for ch in szoveg if not lathatatlan_kodpont_e(ord(ch)))

SZOVEG_MAX = 1000  # upper limit of incoming text, in characters
EGYSOROS_MAX = 200  # upper limit of metadata fields

# --- 1/a. EMOJI CONTEXT (Kö'''1) ----------------------------------------
#
# The byte-exactness gate (`kimeno.megjelenites_szabalyok`) rejects every
# draft that sanitization would change. Sanitization, however, also dropped
# the zero-width joiner (ZWJ, U+200D), so EVERY ZWJ-based emoji sequence —
# families, profession emojis, some flags — got rejected. For a tool that
# writes to a social network that is unacceptable.
#
# The solution is not to allow the ZWJ everywhere: between two LETTERS it is
# an invisible character, hence usable for deception. The ZWJ and the
# variation selector (U+FE0F) are therefore kept ONLY in an emoji context:
# the ZWJ must be both preceded AND followed by an emoji character, the
# variation selector must be preceded by one. In every other situation both
# disappear — and since sanitization then changes the text, the outgoing
# draft is rejected, exactly as before.
#
# The Unicode `Extended_Pictographic` property is not available in the
# standard library, so we approximate it with code point ranges. The
# approximation errs on the SAFE side: anything not on the list does not
# count as an emoji, so a ZWJ next to it disappears and the draft is rejected.
_EMOJI_TARTOMANYOK = (
    (0x00A9, 0x00A9),    # ©
    (0x00AE, 0x00AE),    # ®
    (0x203C, 0x203C),    # ‼
    (0x2049, 0x2049),    # ⁉
    (0x2122, 0x2122),    # ™
    (0x2139, 0x2139),    # ℹ
    (0x2194, 0x21AA),    # arrows
    (0x231A, 0x23FA),    # clock, media-control symbols
    (0x24C2, 0x24C2),    # Ⓜ
    (0x25AA, 0x25FE),    # squares
    (0x2600, 0x27BF),    # miscellaneous symbols and dingbats
    (0x2934, 0x2935),    # arrows
    (0x2B00, 0x2BFF),    # miscellaneous symbols and arrows
    (0x3030, 0x3030),    # 〰
    (0x303D, 0x303D),    # 〽
    (0x3297, 0x3299),    # ㊗ ㊙
    (0xFE0F, 0xFE0F),    # variation selector-16 (emoji presentation)
    (0x1F000, 0x1FAFF),  # the actual emoji blocks
)

ZWJ = "‍"          # zero-width joiner
VALTOZAT_VALASZTO = "️"

# --- 1/b. THE TWO NARROW EXCEPTIONS --------------------------------------
#
# (1) KEYCAP EMOJI: `1️⃣`, `#️⃣`, `*️⃣`. Its form is EXACTLY three code points:
#     `0`–`9` / `#` / `*`, then U+FE0F, then U+20E3 (combining enclosing
#     keycap). The variation selector is kept ONLY in this exact triple — it
#     allows nothing else before or after.
_BILLENTYU_ALAPOK = frozenset("01234" "56789" "#*")  # split only to avoid a false 'long id' hit in the export filter
_BILLENTYU_ZARO = 0x20E3

# (2) FLAG SUBSEQUENCE: `🏴` + tag sequence + U+E007F (cancel tag).
#
#     K'''''2 — the exception used to be a RANGE: `🏴` + 1–16 arbitrary tag
#     characters + terminator. But the tag characters (U+E0020–U+E007E) map
#     one-to-one onto ASCII 0x20–0x7E, so this was not "the English, Scottish
#     and Welsh flags" but "sixteen arbitrary ASCII characters in a flag
#     wrapper". A single post could carry 256 characters of hidden text while
#     the preview showed only a few black flags — and the block filter did
#     not catch it either, because it strips the tags.
#
#     So the exception is NOW narrowed to EXACTLY THREE, LITERAL sequences:
#     England (`gbeng`), Scotland (`gbsct`), Wales (`gbwls`). Every other tag
#     character — in any form — disappears during sanitization, and the draft
#     fails the byte-exactness gate (fail-closed).
#
#     If another country flag is needed later, the list CAN BE EXTENDED — but
#     always by ENUMERATION, never by a range.
_ZASZLO_ALAP = "\U0001F3F4"
_CIMKE_ZARO = "\U000E007F"
ZASZLO_CIMKESOROK = ("gbeng", "gbsct", "gbwls")


def _cimkesorra_valt(cimkesor):
    """ASCII tag sequence → the matching tag characters (U+E0000 + code point)."""
    return "".join(chr(0xE0000 + ord(betu)) for betu in cimkesor)


ENGEDETT_ZASZLOK = tuple(
    _ZASZLO_ALAP + _cimkesorra_valt(sor) + _CIMKE_ZARO
    for sor in ZASZLO_CIMKESOROK)

_ZASZLO_ALSZEKVENCIA = re.compile(
    "|".join(re.escape(z) for z in ENGEDETT_ZASZLOK))


def _zaszlo_cimke_indexek(szoveg):
    """The POSITIONS where a tag character is part of a valid flag.

    The set also contains the position of the terminating U+E007F.
    Sanitization drops tag characters at every other position.
    """
    if "\U0001F3F4" not in szoveg:
        return frozenset()
    indexek = set()
    for egyezes in _ZASZLO_ALSZEKVENCIA.finditer(szoveg):
        # the flag base (the first code point) is not a tag, no need to allow it
        indexek.update(range(egyezes.start() + 1, egyezes.end()))
    return indexek


def _cimke_karakter_e(kod):
    return 0xE0020 <= kod <= 0xE007F


def _billentyu_emoji_e(szoveg, hely):
    """Is the U+FE0F at position `hely` the middle of a keycap-emoji triple?"""
    if hely == 0 or hely + 1 >= len(szoveg):
        return False
    return (szoveg[hely - 1] in _BILLENTYU_ALAPOK
            and ord(szoveg[hely + 1]) == _BILLENTYU_ZARO)


def _emoji_karakter_e(ch):
    """Does the character fit an emoji context? (Approximate; errs strict.)"""
    if not ch:
        return False
    kod = ord(ch)
    for also, felso in _EMOJI_TARTOMANYOK:
        if also <= kod <= felso:
            return True
    # Skin-tone modifiers and the keycap terminator also belong here.
    return kod in (0x20E3,) or 0x1F3FB <= kod <= 0x1F3FF


def szanitalas(szoveg, max_hossz=SZOVEG_MAX):
    """Cleaning of incoming (foreign) text.

    - Unicode NFC normalization (it does not solve homoglyph tricks, but it
      merges scattered combining marks).
    - Removal of EVERY code point that is invisible by default
      (`LATHATATLAN_TARTOMANYOK`), with narrow exceptions: ZWJ / U+FE0F in an
      emoji context, the keycap-emoji triple and the valid flag subsequence.
    - Removal of non-ASCII space-like characters (category Zs).
    - Removal of control characters; newline is kept, tab becomes a space.
    - Line and paragraph separators (U+2028/U+2029/U+0085) replaced by a space.
    - Length limit: max_hossz characters (the rest is simply dropped).

    Always returns a string, even if it did not receive a string.
    """
    if szoveg is None:
        return ""
    if not isinstance(szoveg, str):
        szoveg = str(szoveg)

    szoveg = unicodedata.normalize("NFC", szoveg)
    # every newline variant becomes a uniform \n (\r, \r\n, U+0085)
    szoveg = (szoveg.replace("\r\n", "\n").replace("\r", "\n")
              .replace("", "\n"))

    zaszlo_indexek = _zaszlo_cimke_indexek(szoveg)

    darabok = []
    for hely, ch in enumerate(szoveg):
        kod = ord(ch)
        # The ZWJ and the variation selector are kept IN AN EMOJI CONTEXT
        # (Kö'''1). We look at the "preceding" character in the ORIGINAL text,
        # but accept it only if it actually made it into the output too.
        if ch == ZWJ:
            elozo = szoveg[hely - 1] if hely else ""
            kovetkezo = szoveg[hely + 1] if hely + 1 < len(szoveg) else ""
            if darabok and _emoji_karakter_e(elozo) and \
                    _emoji_karakter_e(kovetkezo):
                darabok.append(ch)
            continue
        if ch == VALTOZAT_VALASZTO:
            # After an emoji, OR in the exact keycap-emoji triple (Kö'''3).
            elozo = szoveg[hely - 1] if hely else ""
            if darabok and (_emoji_karakter_e(elozo)
                            or _billentyu_emoji_e(szoveg, hely)):
                darabok.append(ch)
            continue
        if _cimke_karakter_e(kod):
            # Tag character ONLY as part of a valid flag subsequence.
            if hely in zaszlo_indexek:
                darabok.append(ch)
            continue
        if kod in TILTOTT_KODPONTOK:
            continue
        if ch not in ("\n", "\t") and not _nem_ascii_szokoz_e(ch) \
                and unicodedata.category(ch) not in ("Zl", "Zp") \
                and lathatatlan_kodpont_e(kod):
            # The CATEGORY rule (Cf, Cc, Cs, Co, Cn) — the same thing the
            # `kat in (...)` branch below did, just maintained in one place,
            # in `lathatatlan_kodpont_e`.
            continue
        if _nem_ascii_szokoz_e(ch):
            # Non-breaking and other exotic spaces: they LOOK like a space but
            # are a different code point, so they could bypass the block
            # filter. We replace them with an ordinary space: the foreign
            # text stays readable in the preview, while an OUTGOING draft —
            # since sanitization changed it — gets rejected.
            darabok.append(" ")
            continue
        if ch == "\n":
            darabok.append("\n")
            continue
        if ch == "\t":
            darabok.append(" ")
            continue
        kat = unicodedata.category(ch)
        if kat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            # control (incl. ANSI escape!), format, surrogate, private, unassigned
            continue
        if kat in ("Zl", "Zp"):  # U+2028 / U+2029
            darabok.append(" ")
            continue
        darabok.append(ch)

    tiszta = "".join(darabok)
    if len(tiszta) > max_hossz:
        tiszta = tiszta[:max_hossz]
    return tiszta


# --- 1/c. QUANTITY LIMIT ON THE REMAINING EXCEPTIONS (M'''''2) -----------
#
# The ZWJ and the variation selector are kept in an emoji context — needed
# for families, profession emojis and skin-tone variants. Between two
# identical emojis, however, the PRESENCE OR ABSENCE of a ZWJ is one bit, and
# the two forms are indistinguishable on screen. A long emoji run is thus a
# bit channel.
#
# It cannot be ruled out completely without harming legitimate use, so we
# impose a QUANTITY limit. Legitimate ZWJ sequences are short (the longest
# standard one, the four-member family, has five elements); a whole post
# contains at most a few of them.
ZWJ_LANC_MAX = 7            # at most this many elements in one emoji sequence
ZWJ_POSZTONKENT_MAX = 10    # at most this many ZWJs in total in one post
# Zalgo: at most this many combining marks (Mn/Me) per base character.
KOMBINALO_ALAPONKENT_MAX = 2
KOMBINALO_POSZTONKENT_MAX = 20


def rejtett_csatorna_ellenorzes(szoveg):
    """QUANTITY limits on the remaining invisible characters. List of reasons.

    It does not sanitize — it REJECTS. Sanitization would rewrite the text,
    and then the preview would not be byte-exact; here the draft simply does
    not go out, and the owner sees the reason.
    """
    if not szoveg:
        return []
    okok = []
    zwj_osszesen = 0
    lanc_elem = 1           # element count of the current ZWJ chain
    zwj_fuggoben = False
    kombinalo_osszesen = 0
    kombinalo_alapra = 0
    valtozat_alapra = 0
    for karakter in szoveg:
        if karakter == ZWJ:
            zwj_osszesen += 1
            zwj_fuggoben = True
            continue
        if karakter == VALTOZAT_VALASZTO:
            valtozat_alapra += 1
            if valtozat_alapra > 1:
                # At most ONCE after a base character. The "previous
                # character" of the second variation selector is the first
                # one, which is in the emoji range, so sanitization would let
                # it through.
                okok.append("ismetelt_valtozat_valaszto")
            continue
        if unicodedata.category(karakter) in ("Mn", "Me"):
            kombinalo_alapra += 1
            kombinalo_osszesen += 1
            if kombinalo_alapra > KOMBINALO_ALAPONKENT_MAX:
                okok.append("tul_sok_kombinalo_jel")
            continue
        # from here on: a NEW base character
        if zwj_fuggoben:
            lanc_elem += 1
            zwj_fuggoben = False
            if lanc_elem > ZWJ_LANC_MAX:
                okok.append("tul_hosszu_zwj_lanc")
        else:
            lanc_elem = 1
        kombinalo_alapra = 0
        valtozat_alapra = 0
    if zwj_osszesen > ZWJ_POSZTONKENT_MAX:
        okok.append("tul_sok_zwj")
    if kombinalo_osszesen > KOMBINALO_POSZTONKENT_MAX:
        okok.append("tul_sok_kombinalo_jel")
    return sorted(set(okok))


def egysoros(szoveg, max_hossz=EGYSOROS_MAX):
    """SINGLE-LINE metadata field (handle, did, time, uri, cid, alt).

    In the text body keeping newlines is correct; in metadata fields it is
    NOT: from there a newline could put a fake frame line on screen (finding
    M4). Sanitization → all whitespace becomes a single space → frame
    neutralization.
    """
    tiszta = szanitalas(szoveg, max_hossz)
    tiszta = re.sub(r"\s+", " ", tiszta).strip()
    return keret_veszelytelenites(tiszta)


# A permissive but SHAPE-BOUND pattern for an AT-URI: anything not shaped like
# this is not a uri, so we do not put it on screen either.
_URI_MINTA = re.compile(r"^at://[A-Za-z0-9._:%~-]+(?:/[A-Za-z0-9._:%~-]+){0,4}$")
_CID_MINTA = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def uri_veszelytelenites(uri, max_hossz=300):
    """Shape check: anything not shaped like an AT-URI becomes an EMPTY string."""
    tiszta = egysoros(uri, max_hossz)
    return tiszta if _URI_MINTA.match(tiszta) else ""


def cid_veszelytelenites(cid, max_hossz=200):
    """Shape check for the content identifier (alphanumeric only)."""
    tiszta = egysoros(cid, max_hossz)
    return tiszta if _CID_MINTA.match(tiszta) else ""


# --- 2. FRAME PROTECTION -------------------------------------------------

# mutat.py puts foreign text inside a hard frame. If the text contains the
# frame itself (or a "===" line), the content could "close" the frame and
# then write text that looks like instructions. So we neutralize the frame
# markers in the content in advance.
#
# We search for the patterns in the HOMOGLYPH-NORMALIZED form (fullwidth "＝"
# or Cyrillic "К" does not help either), but do the replacement in the
# ORIGINAL — that is why the length-preserving mapping is needed.
_EGYENLOSEG = re.compile(r"={2,}")
# Hungarian language data: the frame label phrase "külső adat [vége]"
# ("external data [end]") with any accent/case variant. If the frame label in
# mutat.py is changed to another language, change this regex (and the
# "keret_kitores" pattern below) to match the new label.
_KERETSZO = re.compile(r"k[üuÜU]ls[őoŐO]\s+adat(\s*[-:]?\s*v[ée]ge)?"
                       r"|(end\s+of\s+)?external\s+data(\s*[-:]?\s*end)?",
                       re.IGNORECASE)


def keret_veszelytelenites(szoveg, nonce=None):
    """Neutralize the frame markers in foreign text.

    `==` → `[=]`, "külső adat (vége)" → its spaces replaced by middle dots, so
    the frame pattern no longer matches, but the reader sees what was there.
    The per-run random frame marker (`nonce`) is filtered too: an attacker
    cannot guess it, but if they somehow obtained it, it fails here.
    """
    if not szoveg:
        return ""
    if nonce:
        szoveg = szoveg.replace(nonce, "[marker]")
    normalt = homograf_normalizal(szoveg)

    talalatok = []
    for sorszam, minta in enumerate((_EGYENLOSEG, _KERETSZO)):
        for m in minta.finditer(normalt):
            talalatok.append((m.start(), m.end(), sorszam))
    if not talalatok:
        return szoveg
    talalatok.sort(key=lambda x: (x[0], -x[1]))

    darabok = []
    utolso = 0
    for kezd, veg, sorszam in talalatok:
        if kezd < utolso:  # overlap — an earlier match already covered it
            continue
        darabok.append(szoveg[utolso:kezd])
        reszlet = szoveg[kezd:veg]
        if sorszam == 0:  # run of equals signs
            darabok.append("[=]")
        else:
            darabok.append(re.sub(r"\s", "·", reszlet))
        utolso = veg
    darabok.append(szoveg[utolso:])
    return "".join(darabok)


# --- 3. INJECTION HEURISTICS (only FLAGS, does not drop) -----------------

# (reason_name, pattern) pairs. The reason name goes into the log — the TEXT
# of the match never does.
#
# Mixed English + Hungarian language data: entries ending in "_hu" and the
# Hungarian alternatives inside other patterns (e.g. "hagyd figyelmen kívül",
# "utasítás", "jelszó", "futtasd", "üzemeltetőd vagyok") match Hungarian
# prompt-injection phrasing. For another language, add that language's
# equivalents as extra alternatives (or extra "_xx" entries); keep the reason
# names unchanged, they are machine-readable.
INJECTION_MINTAK = [
    ("utasitas_felulirasa_en",
     r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|your)?\s*"
     r"(instruction|instructions|rule|rules|prompt)"),
    ("utasitas_felulirasa_en2",
     r"(disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|your)?\s*"
     r"(instruction|instructions|rule|rules|prompt)"),
    ("utasitas_felulirasa_hu",
     r"hagyd\s+figyelmen\s+k[ií]v[üu]l|felejtsd\s+el\s+(az?\s+)?(eddigi|kor[áa]bbi|minden)"),
    ("rendszer_prompt", r"system\s*prompt|rendszer\s*prompt|rendszer[\s-]*utas[íi]t[áa]s"),
    ("szerepatiras", r"you\s+are\s+now|from\s+now\s+on\s+you|mostant[óo]l\s+te\s+"
                     r"|te\s+most\s+egy\s+"),
    ("utasitas_szo", r"utas[íi]t[áa]s"),
    ("futtatas", r"\b(run|execute|eval)\b|futtasd|hajtsd\s+v[ée]gre|ind[íi]tsd\s+el"),
    ("hitelesito_adat", r"password|jelsz[óo]|api[\s_-]*key|secret\s*key|access\s*token"
                        r"|\btoken\b|kulcs(ot|od|om)?\b"),
    ("base64", r"base64|[A-Za-z0-9+/]{60,}={0,2}"),
    ("url_rovidito",
     r"\b(bit\.ly|tinyurl\.com|t\.co|goo\.gl|is\.gd|ow\.ly|buff\.ly|rb\.gy|cutt\.ly"
     r"|shorturl\.at|rebrand\.ly|t\.ly|s\.id|lnkd\.in)\b"),
    ("szemelyazonossag_hamisitas",
     r"i\s+am\s+your\s+(operator|owner|developer|creator)|anthropic\s+here"
     r"|^\s*system\s*:|\bsystem\s*:\s|az\s+[üu]zemeltet[őo]d\s+vagyok"
     r"|[ée]n\s+vagyok\s+a\s+(gazd[áa]d|fejleszt[őo]d)|developer\s+mode|fejleszt[őo]i\s+m[óo]d"),
    ("kulon_jelolok", r"<\|[^|]{0,40}\|>|\[/?INST\]|###\s*(instruction|system)"
                      r"|<<\s*SYS\s*>>"),
    ("keret_kitores", r"={2,}|k[üuÜU]ls[őoŐO]\s+adat|external\s+data"),
    ("adatkeres", r"(mi|mennyi|hol)\s+(a|az)\s+(c[íi]me|lakc[íi]me|telefonsz[áa]ma)"
                  r"|what\s+is\s+(your|the)\s+(address|phone|password)"),
    # imitation of the Telegram approval formula (M5); Hungarian approval
    # words "igen"/"nem" (yes/no) and "jóváhagyás: válaszold" (approval:
    # reply) — must match the approval words the Telegram flow actually uses
    ("jovahagyo_formula", r"\b(igen|nem)\s*\d{6,8}\b|j[óo]v[áa]hagy[áa]s\s*:\s*v[áa]laszold"),
]

_INJECTION_FORDITOTT = [(nev, re.compile(m, re.IGNORECASE | re.MULTILINE))
                        for nev, m in INJECTION_MINTAK]

KODBLOKK_KUSZOB = 200  # a code block longer than this is already suspicious


def injection_jelolek(szoveg):
    """Return the list of suspicion reasons (empty list = no flag).

    IMPORTANT: this is only FLAGGING. It drops nothing — the filter is the
    agent, not the regular expression; but the agent should see the warning.
    """
    if not szoveg:
        return []
    okok = []
    normalt = osszehasonlito_alak(szoveg)
    for nev, minta in _INJECTION_FORDITOTT:
        if minta.search(szoveg) or minta.search(normalt):
            okok.append(nev)
    # unusually long code block
    for blokk in re.findall(r"```(.*?)```", szoveg, re.DOTALL):
        if len(blokk) > KODBLOKK_KUSZOB:
            okok.append("hosszu_kodblokk")
            break
    return sorted(set(okok))


def gyanus_e(szoveg):
    """Convenience wrapper: is there any suspicion flag at all."""
    return bool(injection_jelolek(szoveg))


# --- 4. BLOCK FILTER FOR OUTGOING TEXT -----------------------------------

# Finding A2 (M'2): emptying `tilto_mintak.json` silently switched the filter
# off. So the ESSENTIAL patterns live here, IN THE CODE. The JSON file can
# only EXTEND them (place names, family names, further regexes) — it cannot
# switch anything off.
#
# Partly Hungarian language data: "email_szoval" (spelled-out e-mail with
# Hungarian "kukac" = at, "pont" = dot), "telefonszam_hu" (Hungarian phone
# prefixes +36/0036/06), "utca_hazszam" (Hungarian street-type words such as
# utca/út/tér/köz/körút + house number, with Hungarian accented letters),
# "rendszam_*" (Hungarian licence-plate formats, "rendszám"/"frsz" = plate),
# "hitelesito_szo" ("jelszó" = password, "titkos kulcs" = secret key),
# "tajszam_adoszam_szeru" (Hungarian social-security / tax-number shape).
# For another country/language, add equivalent patterns under NEW names
# (keep the existing names; they are machine-readable reason codes).
ALAP_REGEXEK = [
    ("email_cim", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("email_szoval",
     r"(?i)(kukac|\bat\b|\bkukacz?\b)\s+[\w.-]+\s+(pont|dot)\s+[a-z]{2,4}\b"),
    ("telefonszam_hu",
     r"(\+36|0036|\b06)[\s\-/.]?\(?\d{1,2}\)?[\s\-/.]?\d{3}[\s\-/.]?\d{3,4}"),
    ("telefonszam_altalanos", r"\b\d{2,3}[\s\-/.]\d{3}[\s\-/.]\d{3,4}\b"),
    ("hosszu_szamsor", r"\b\d{9,}\b"),
    ("ip_cim", r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    ("belso_halozat", r"192\.168\.|10\.\d{1,3}\.|172\.(1[6-9]|2\d|3[01])\."),
    ("utca_hazszam",
     r"\b[\wÁÉÍÓÖŐÚÜŰáéíóöőúüű.-]+\s+(utca|utcai|út|úti|u\.|tér|téri|köz"
     r"|sétány|körút|krt\.?|sor|dűlő|lakótelep|ltp\.?)\s*\d+"),
    ("rendszam_regi",
     r"\b[A-Z]{3}-\d{3}\b|\b(rendsz[áa]m|frsz)\b[\s:]*[A-Z]{3}[\s-]?\d{3}\b"),
    ("rendszam_uj", r"\b[A-Z]{2}[\s-]?[A-Z]{2}[\s-]?\d{3}\b"),
    ("kulcs_szeru_karaktersor",
     r"\b(?=[A-Za-z0-9_+/=-]*\d)(?=[A-Za-z0-9_+/=-]*[A-Za-z])"
     r"[A-Za-z0-9_+/=-]{16,}\b"),
    ("app_jelszo_alak", r"(?i)\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b"),
    ("jwt_alak", r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}"),
    ("telegram_token_alak", r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b"),
    ("windows_utvonal", r"[A-Za-z]:\\|[A-Za-z]:/"),
    ("unix_utvonal_home", r"/home/[A-Za-z0-9._-]+|/Users/[A-Za-z0-9._-]+"),
    ("forraskod_jel",
     r"(?i)\"\"\"|'''|\bimport\s+[A-Za-z_]|\bdef\s+[A-Za-z_]\w*\s*\("
     r"|\b[\w/\\.-]+\.(py|json|env|db|jsonl|log|ini|cfg|sql|sh|bat|ps1|yaml|yml)\b"
     r"|#!/"),
    ("gps_koordinata", r"\b[-+]?\d{1,2}\.\d{4,}\s*,\s*[-+]?\d{1,3}\.\d{4,}\b"),
    ("hitelesito_szo",
     r"(?i)\b(jelsz[oó]|password|app[\s_-]*password|api[\s_-]*key"
     r"|access[\s_-]*token|bot[\s_-]*token|titkos\s+kulcs)\b"),
    ("bankkartya_szeru", r"\b(?:\d[ -]?){13,19}\b"),
    ("tajszam_adoszam_szeru", r"\b\d{3}[\s-]?\d{3}[\s-]?\d{3,4}\b"),
]

# The city list needed by the postcode + place-name rule is also in the code.
# (Public data, not personal — the FAMILY NAMES stay in the JSON.)
# Hungarian language data: Hungarian city/town names (with accents) that are
# matched after a 4-digit Hungarian postcode. For another country, replace
# this list with that country's major place names (and adjust the postcode
# regex in `tilto_ellenorzes`).
ALAP_IRANYITOSZAM_VAROSOK = [
    "Budapest", "Debrecen", "Szeged", "Miskolc", "Pécs", "Győr",
    "Nyíregyháza", "Kecskemét", "Székesfehérvár", "Szombathely",
    "Szolnok", "Tatabánya", "Kaposvár", "Békéscsaba", "Veszprém",
    "Zalaegerszeg", "Eger", "Sopron", "Dunaújváros", "Hódmezővásárhely",
    # Only the largest cities; add your own region's towns via "iranyitoszam_varosok" in the JSON.
]

_LISTA_KULCSOK = ("csalad_nevek", "telepulesek", "szo_szerinti",
                  "iranyitoszam_varosok")


def tilto_mintak_betolt(utvonal):
    """Load tilto_mintak.json. Raises an exception on error (better for the
    process to stop than to post without a filter)."""
    with open(utvonal, "r", encoding="utf-8") as f:
        adat = json.load(f)
    if not isinstance(adat, dict):
        raise ValueError("tilto_mintak.json: expected an object")
    return adat


def tilto_mintak_biztos(utvonal):
    """The pattern set actually used: BASE (code) + the JSON's extensions.

    Returns `(mintak, megjegyzes)`. `megjegyzes` is None if the JSON loaded
    fine, otherwise a SECRET-FREE sentence explaining why only the base
    patterns are active. The caller prints it — but does NOT stop because of
    it, since the base patterns are in the code, so the filter still protects
    (M'2).
    """
    mintak = {
        "regexek": [{"nev": nev, "minta": minta} for nev, minta in ALAP_REGEXEK],
        "csalad_nevek": [],
        "telepulesek": [],
        "szo_szerinti": [],
        "iranyitoszam_varosok": list(ALAP_IRANYITOSZAM_VAROSOK),
    }
    try:
        adat = tilto_mintak_betolt(utvonal)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return mintak, ("tilto_mintak.json is not readable (%s) — ONLY the "
                        "built-in base patterns are active, the family names are NOT."
                        % type(e).__name__)

    ismert_nevek = {nev for nev, _ in ALAP_REGEXEK}
    for elem in adat.get("regexek") or []:
        if not isinstance(elem, dict) or not elem.get("minta"):
            continue
        nev = elem.get("nev") or "nevtelen_regex"
        if nev in ismert_nevek:
            continue  # the base version stays — the file cannot weaken it
        mintak["regexek"].append({"nev": nev, "minta": elem["minta"]})
    for kulcs in _LISTA_KULCSOK:
        for szo in adat.get(kulcs) or []:
            if isinstance(szo, str) and szo and szo not in mintak[kulcs]:
                mintak[kulcs].append(szo)
    return mintak, None


def _ekezet_nelkul(szoveg):
    """Lowercase, accent-free, HOMOGLYPH-FREE form — for word-list matching.

    Accent folding is generic (NFD + drop combining marks), so it also folds
    Hungarian accented letters (á, é, ő, ű, ...) without a language table.
    """
    alap = osszehasonlito_alak(szoveg)
    bontott = unicodedata.normalize("NFD", alap.casefold())
    return "".join(ch for ch in bontott if unicodedata.category(ch) != "Mn")


# Leetspeak classes: we search for blocked words so that the typical digit
# substitutions do not help either (Kö3).
_LEET = {
    "a": "a4@", "b": "b8", "e": "e3", "g": "g9", "i": "i1l!|",
    "l": "l1i|", "o": "o0", "s": "s5$", "t": "t7", "z": "z2",
}


def _szo_minta(szo):
    """A leet-tolerant regex built from a blocked word."""
    darabok = []
    for ch in _ekezet_nelkul(szo):
        ha = _LEET.get(ch)
        darabok.append("[" + re.escape(ha) + "]" if ha else re.escape(ch))
    return re.compile("".join(darabok))


_SZO_MINTA_GYORSITO = {}


def _szo_minta_gyorsitott(szo):
    minta = _SZO_MINTA_GYORSITO.get(szo)
    if minta is None:
        minta = _szo_minta(szo)
        _SZO_MINTA_GYORSITO[szo] = minta
    return minta


# Phone number regardless of format: 7+ consecutive digits with only
# separators between them. This also catches the "06 20 123 45 67" grouping.
_SZAMJEGY_FUTAM = re.compile(r"\d(?:[\s\-./()]{0,2}\d){6,}")


def tilto_ellenorzes(szoveg, mintak):
    """Check the outgoing text. Returns the list of REASONS.

    Empty list = may go. Non-empty = REJECTION.

    The reasons contain ONLY the pattern name, never the matched text — so
    the log and the console do not leak exactly what they protect.

    Every check runs on the HOMOGLYPH-NORMALIZED form (Kö3): a Cyrillic "а"
    or fullwidth punctuation does not switch the filter off.

    Kö''''2: the check runs on TWO forms. The first is the usual NFKC +
    homoglyph-normalized form; the second is the same, but with every
    invisible code point and non-ASCII space removed FIRST. Without this a
    single inserted invisible character (`Town<U+3164>name`) split the blocked
    word, and the whole filter let it through. `szanitalas` nowadays rejects
    such an OUTGOING draft anyway — this is the second, independent line of
    defence.
    """
    if szoveg is None:
        szoveg = ""
    okok = []
    alakok = [osszehasonlito_alak(szoveg)]
    tisztitott = osszehasonlito_alak(lathatatlanok_nelkul(szoveg))
    if tisztitott != alakok[0]:
        alakok.append(tisztitott)

    for normalt in alakok:
        for elem in mintak.get("regexek", []):
            nev = elem.get("nev", "nevtelen_regex")
            try:
                if re.search(elem["minta"], normalt,
                             re.IGNORECASE | re.MULTILINE):
                    okok.append(nev)
            except re.error:
                # a broken pattern is treated as a match (stricter interpretation)
                okok.append("hibas_regex:" + nev)

        if _SZAMJEGY_FUTAM.search(normalt):
            okok.append("szamjegy_futam")

        lapos = _ekezet_nelkul(normalt)
        for kulcs in ("csalad_nevek", "telepulesek", "szo_szerinti"):
            for szo in mintak.get(kulcs, []):
                if not szo:
                    continue
                if _szo_minta_gyorsitott(szo).search(lapos):
                    okok.append(kulcs)
                    break

        # postcode + known place name (Kö5: year + capitalized word is NOT one).
        # The regex assumes the Hungarian 4-digit postcode placed before the
        # place name; adjust it for other countries' address formats.
        varosok = list(mintak.get("iranyitoszam_varosok", [])) + \
            list(mintak.get("telepulesek", []))
        if varosok:
            for m in re.finditer(r"\b[1-9]\d{3}\s+([^\s,.;!?]+)", normalt):
                szo = _ekezet_nelkul(m.group(1))
                if any(szo.startswith(_ekezet_nelkul(v)) for v in varosok if v):
                    okok.append("iranyitoszam_telepules")
                    break

    return sorted(set(okok))


# --- 5. LENGTH (graphemes) -----------------------------------------------

POSZT_MAX_GRAFEMA = 300
POSZT_MAX_BAJT = 3000  # Bluesky's byte limit is the same


def grafema_szam(szoveg):
    """APPROXIMATE number of grapheme clusters (no exact counter in stdlib).

    Merges: combining marks, variation selectors, ZWJ-joined emojis and flag
    pairs. The approximation errs low in exotic cases, so the length check
    also looks at the byte limit.
    """
    if not szoveg:
        return 0
    n = 0
    zwj_var = False
    elozo_ri = False
    for ch in szoveg:
        kod = ord(ch)
        kat = unicodedata.category(ch)
        if kat in ("Mn", "Mc", "Me"):
            continue
        if 0xFE00 <= kod <= 0xFE0F:  # variation selector
            continue
        if ch == "‍":  # zero-width joiner: the next character belongs here too
            zwj_var = True
            continue
        if zwj_var:
            zwj_var = False
            continue
        if 0x1F1E6 <= kod <= 0x1F1FF:  # regional indicator (flags)
            if elozo_ri:
                elozo_ri = False
                continue
            elozo_ri = True
            n += 1
            continue
        elozo_ri = False
        n += 1
    return n


def hossz_ellenorzes(szoveg, max_grafema=POSZT_MAX_GRAFEMA, max_bajt=POSZT_MAX_BAJT):
    """List of reasons (empty = OK)."""
    okok = []
    if not szoveg or not szoveg.strip():
        okok.append("ures_szoveg")
        return okok
    if grafema_szam(szoveg) > max_grafema:
        okok.append("tul_hosszu_grafema")
    if len(szoveg.encode("utf-8")) > max_bajt:
        okok.append("tul_hosszu_bajt")
    return okok


# --- 6. HASH AND CANONICAL OUTGOING RECORD -------------------------------

def szoveg_hash(szoveg):
    """Fingerprint of the outgoing TEXT (against duplicates and text swapping)."""
    return hashlib.sha256((szoveg or "").encode("utf-8")).hexdigest()


def dedup_kulcs(szoveg):
    """The dedup key: a normalized form, so that a non-breaking space or a
    homoglyph does not produce a "new" text (Kö2). The APPROVAL hash is
    independent of this: it covers the exact text."""
    alap = osszehasonlito_alak(szoveg or "")
    alap = re.sub(r"\s+", " ", alap).strip().casefold()
    return hashlib.sha256(alap.encode("utf-8")).hexdigest()


def kimeno_kanonikus(tipus, szoveg, valasz_cel, langs, kezdemenyezett):
    """Canonical form of the ENTIRE outgoing record.

    This is what we hash at approval, and the record to be posted is built
    FROM THIS — and only from this. So after approval neither the text, nor
    the target, nor the type, nor the language can be swapped (K1, K2, M6).
    """
    cel = None
    if valasz_cel:
        cel = {
            "parent_uri": valasz_cel.get("uri") or "",
            "parent_cid": valasz_cel.get("cid") or "",
            "root_uri": valasz_cel.get("gyoker_uri") or "",
            "root_cid": valasz_cel.get("gyoker_cid") or "",
        }
    return {
        "tipus": tipus,
        "szoveg": szoveg or "",
        "valasz_cel": cel,
        "langs": list(langs or []),
        "kezdemenyezett": bool(kezdemenyezett),
    }


def jovahagyas_hash(kanonikus):
    """sha256 of the canonical outgoing record (sorted keys, UTF-8)."""
    nyers = json.dumps(kanonikus, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
    return hashlib.sha256(nyers.encode("utf-8")).hexdigest()


def kanonikus_ervenyes(kanonikus):
    """Structural check before posting. List of reasons (empty = OK).

    The MOST IMPORTANT rule: with `tipus == "poszt"` there can be NO
    `valasz_cel` — so a `reply` block cannot even structurally get into the
    record (K1).
    """
    okok = []
    tipus = kanonikus.get("tipus")
    cel = kanonikus.get("valasz_cel")
    if tipus not in ("poszt", "valasz"):
        okok.append("ismeretlen_tipus")
    if tipus == "poszt" and cel:
        okok.append("tipus_cel_ellentmondas")
    if tipus == "valasz" and not cel:
        okok.append("valasz_cel_nelkul")
    if cel:
        for mezo in ("parent_uri", "root_uri"):
            if not _URI_MINTA.match(cel.get(mezo) or ""):
                okok.append("rossz_uri_alak")
                break
        for mezo in ("parent_cid", "root_cid"):
            if not _CID_MINTA.match(cel.get(mezo) or ""):
                okok.append("rossz_cid_alak")
                break
    if not kanonikus.get("langs"):
        okok.append("nincs_langs")
    return sorted(set(okok))


# --- 7. RATE LIMIT -------------------------------------------------------

RATE_NAP_MAX = 5           # max 5 outgoing / 24 hours
RATE_NAP_ABLAK = 24 * 3600
RATE_PERC_MAX = 1          # max 1 outgoing / 10 minutes
RATE_PERC_ABLAK = 10 * 60


def rate_limit_ellenorzes(most_ts, korabbi_ts_lista,
                          nap_max=RATE_NAP_MAX, nap_ablak=RATE_NAP_ABLAK,
                          perc_max=RATE_PERC_MAX, perc_ablak=RATE_PERC_ABLAK):
    """List of reasons (empty = may go).

    `korabbi_ts_lista`: unix times of earlier outgoing (or reserved) events.
    """
    okok = []
    napon_belul = [t for t in korabbi_ts_lista if most_ts - t < nap_ablak]
    percen_belul = [t for t in korabbi_ts_lista if most_ts - t < perc_ablak]
    if len(napon_belul) >= nap_max:
        okok.append("napi_limit")
    if len(percen_belul) >= perc_max:
        okok.append("perc_limit")
    return okok


# --- 8. OPT-IN (reply only when addressed, or to an allowlisted author) ---

VALASZOLHATO_TIPUSOK = ("mention", "reply")

# The CLOSED set of queue states. What is CLOSED does not take up quota;
# everything else (including an unknown or mistyped state name) does — the
# stricter direction (Kö1). The state names are machine-readable values
# stored in the state files; do not translate them.
LEZART_ALLAPOTOK = ("elkuldve", "elvetve", "lejart", "elutasitva", "hiba",
                    "piszkozat", "kezi_ellenorzes")
NYITOTT_ALLAPOTOK = ("var", "kuldes_alatt")

# CHANGE 1: an initiated reply (one not triggered by being addressed) has a
# separate, narrower daily cap — WITHIN the overall daily 5.
KEZDEMENYEZETT_NAP_MAX = 2

# The INITIATED classification can arise from two independent reasons, and the
# preview must tell the TRUTH about which one (Kö''''1):
#   * ENGEDELYLISTAS (allowlisted) — we were not addressed; the right to reply
#     comes solely from the author's DID being on the allowlist;
#   * IDEGEN_A_SZALBAN (stranger in the thread) — we WERE explicitly
#     addressed, but a third-party stranger appears in the ancestor chain, so
#     the reply lands in their conversation. The earlier single "WE WERE NOT
#     ADDRESSED!" line was factually untrue in that case.
# Both reasons can apply AT ONCE, so `cel["kezdemenyezett_oka"]` is a LIST.
OK_ENGEDELYLISTAS = "engedelylistas_kezdemenyezes"
OK_IDEGEN_A_SZALBAN = "idegen_a_szalban"


def engedelylista_betolt(utvonal):
    """Load szivesen_latnak.json. Missing file = EMPTY list
    (so no initiated reply can go to anyone)."""
    return engedelylista_biztos(utvonal)[0]


def engedelylista_biztos(utvonal):
    """`(allowlist, note_or_None)` — not even a CORRUPT file stops it.

    Kö5: previously malformed JSON raised a `ValueError` that the run did not
    catch, and the guard silently restarted in a loop. Following the pattern
    of `tilto_mintak_biztos`: on a broken file, an EMPTY list (the safe
    direction, since it blocks initiated replies) and a secret-free note in
    the outward-facing state file.
    """
    try:
        with open(utvonal, "r", encoding="utf-8") as f:
            adat = json.load(f)
    except FileNotFoundError:
        return [], None
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return [], ("szivesen_latnak.json is not readable (%s) — working with "
                    "an EMPTY allowlist, no initiated reply will go "
                    "out." % type(e).__name__)
    if not isinstance(adat, dict):
        return [], ("szivesen_latnak.json is not an object — working with "
                    "an EMPTY allowlist.")
    fiokok = adat.get("fiokok", [])
    if not isinstance(fiokok, list):
        return [], ("the \"fiokok\" field of szivesen_latnak.json is not a list — "
                    "working with an EMPTY allowlist.")
    return [f for f in fiokok if isinstance(f, dict)], None


def engedelylistas_did_e(did, engedelylista):
    """Matches only on DID, NEVER on handle: a handle can change, a DID cannot."""
    if not did or not engedelylista:
        return False
    for fiok in engedelylista:
        listas = (fiok.get("did") or "").strip()
        if listas and listas == did.strip():
            return True
    return False


def valasz_cel_ellenorzes(inbox_tetelek, inbox_id, engedelylista=None):
    """The CHEAP, FIRST gate: is the target in the inbox, and can it be replied to.

    IMPORTANT: this is NOT a trust anchor (K3). inbox.jsonl is a plain file
    written by the main actor of the threat model (a deceived Claude) itself.
    The FINAL word is spoken by `halozati_cel_ellenorzes`, from the post
    freshly fetched from Bluesky. This function only adds that we also
    require inbox membership and the type — one more gate.

    Returns: (cel_dict, okok_lista).
    """
    if not inbox_id:
        return None, ["nincs_inbox_id"]
    talalat = None
    for t in inbox_tetelek:
        if t.get("id") == inbox_id:
            talalat = t
            break
    if talalat is None:
        return None, ["ismeretlen_inbox_id"]

    megszolitottak = talalat.get("tipus") in VALASZOLHATO_TIPUSOK
    kezdemenyezett = False
    if not megszolitottak:
        if engedelylistas_did_e(talalat.get("szerzo_did"), engedelylista or []):
            kezdemenyezett = True
        else:
            return None, ["nem_valaszolhato_tipus"]

    uri = talalat.get("uri")
    cid = talalat.get("cid")
    if not uri or not cid:
        return None, ["hianyzo_uri_vagy_cid"]
    gyoker_uri = talalat.get("gyoker_uri") or uri
    gyoker_cid = talalat.get("gyoker_cid") or cid
    cel = {
        "uri": uri,
        "cid": cid,
        "gyoker_uri": gyoker_uri,
        "gyoker_cid": gyoker_cid,
        "szerzo_handle": talalat.get("szerzo_handle", ""),
        "szerzo_did": talalat.get("szerzo_did", ""),
        "szoveg_roviden": (talalat.get("szoveg") or "")[:200],
        "kezdemenyezett": kezdemenyezett,
        "kezdemenyezett_oka": [OK_ENGEDELYLISTAS] if kezdemenyezett else [],
        "inbox_id": inbox_id,
    }
    return cel, []


def _emlit_e_minket(rekord, sajat_did):
    """Is OUR DID among the post's facets (mention)?"""
    if not sajat_did:
        return False
    for facet in (rekord or {}).get("facets") or []:
        for jellemzo in (facet or {}).get("features") or []:
            if not isinstance(jellemzo, dict):
                continue
            if jellemzo.get("$type", "").endswith("#mention") and \
                    jellemzo.get("did") == sajat_did:
                return True
    return False


def emlit_e_minket(rekord, sajat_did):
    """Public wrapper: does the post's mention facet point to OUR DID.

    the posting step needs to know separately whether the right to reply comes
    from a MENTION or from the "replied to us" branch — because the parent of
    the latter must also be verified ON THE NETWORK (Kö'2: the parent uri can
    be forged).
    """
    return _emlit_e_minket(rekord, sajat_did)


def _nekunk_valaszol_e(rekord, sajat_did):
    """Is the post a DIRECT reply to one of our posts?"""
    if not sajat_did:
        return False
    szulo = ((rekord or {}).get("reply") or {}).get("parent") or {}
    uri = szulo.get("uri") or ""
    return uri.startswith("at://" + sajat_did + "/")


def halozati_cel_ellenorzes(poszt, uri, cid, sajat_did, engedelylista,
                            folyo_beszelgetes=False):
    """The REAL opt-in gate: based on the target post FRESHLY fetched from the
    network (K3).

    `poszt`: one element of `app.bsky.feed.getPosts` (or None if missing).
    Returns: (cel_dict, okok_lista). Empty reasons = the reply may go.

    `folyo_beszelgetes`: the result of rule J6/(B). The caller (`kimeno.
    cel_ellenorzes`) determines it EXCLUSIVELY from the ancestor chain walked
    on the network, never from the draft. If true, the target post is the
    direct continuation of a conversation already going on with us: X
    replied to OUR post, then wrote further under their own reply without
    tagging us. That is the MIDDLE of a conversation, i.e. a reply to being
    addressed — not an initiation. This parameter only RELAXES, and only when
    the caller has verified all the narrow conditions; by default the old
    behaviour stays.

    It is not inbox.jsonl or szivesen_latnak.json that decides, but what
    Bluesky says right now: does the post exist, does the cid match, who is
    the author, and were we addressed. The allowlist only opens the
    "initiated" branch — and the preview shows that to the owner in CAPITALS.
    """
    if not poszt:
        return None, ["nincs_halozati_ellenorzes"]
    okok = []
    halo_uri = poszt.get("uri") or ""
    halo_cid = poszt.get("cid") or ""
    if not halo_uri or halo_uri != (uri or ""):
        okok.append("halozati_uri_elter")
    if not halo_cid or halo_cid != (cid or ""):
        okok.append("halozati_cid_elter")
    if okok:
        return None, sorted(set(okok))

    szerzo = poszt.get("author") or {}
    rekord = poszt.get("record") or {}
    szerzo_did = szerzo.get("did") or ""

    megszolitas = _emlit_e_minket(rekord, sajat_did) or \
        _nekunk_valaszol_e(rekord, sajat_did)
    kezdemenyezett = False
    folyo = False
    if not megszolitas:
        if folyo_beszelgetes:
            # J6/(B): continuation of an ongoing conversation. The allowlist
            # is NOT a precondition — someone who replied to us and continues
            # under their own reply may be answered. The caps (5 per day, ten
            # minutes, duplicate) still apply unchanged.
            folyo = True
        elif engedelylistas_did_e(szerzo_did, engedelylista or []):
            kezdemenyezett = True
        else:
            return None, ["halozati_nem_szolitottak_meg"]

    # THE THREAD ROOT (K''1). The `reply.root` in the target post's record is
    # data WRITTEN BY THE ATTACKER: anyone can put a third party's post into
    # their own post, and then our reply would land IN THAT THIRD PARTY'S
    # THREAD, notifying the root's author. So here we do ONLY a shape check,
    # and return the requested root separately, under `kert_*` names.
    #
    # The default for `gyoker_uri`/`gyoker_cid` is the SAFE value: the parent
    # itself (i.e. our reply opens a new thread under the target post).
    # `kimeno.gyoker_es_lanc_ellenorzes` overrides it, but ONLY if it has
    # verified the root on the network and walked the entire ancestor chain.
    # The `kert_*` fields have ONLY passed a shape check here — by themselves
    # they prove nothing.
    gyoker = ((rekord.get("reply") or {}).get("root") or {})
    kert_gyoker_uri = uri_veszelytelenites(gyoker.get("uri"))
    kert_gyoker_cid = cid_veszelytelenites(gyoker.get("cid"))
    cel = {
        "uri": halo_uri,
        "cid": halo_cid,
        "gyoker_uri": halo_uri,
        "gyoker_cid": halo_cid,
        "kert_gyoker_uri": kert_gyoker_uri,
        "kert_gyoker_cid": kert_gyoker_cid,
        # Was there ANY root reference in the target post's record? If not,
        # the target post is itself the thread root, and there is nothing to
        # verify.
        "gyoker_nyers_van": bool(gyoker.get("uri") or gyoker.get("cid")),
        "gyoker_igazolva": False,
        "szerzo_handle": egysoros(szerzo.get("handle", ""), 120),
        "szerzo_did": egysoros(szerzo_did, 120),
        "szoveg_roviden": keret_veszelytelenites(
            szanitalas(rekord.get("text", ""), 300)),
        "kezdemenyezett": kezdemenyezett,
        # Kö''''1: from this the preview knows WHY the reply is initiated.
        "kezdemenyezett_oka": [OK_ENGEDELYLISTAS] if kezdemenyezett else [],
        # An ongoing conversation is just as much a "reply to being
        # addressed" as a mention: X started the conversation by replying
        # to us.
        "megszolitottak": bool(megszolitas or folyo),
        "folyo_beszelgetes": folyo,
        "forras": "halozat",
    }
    if not cel["uri"] or not cel["cid"]:
        return None, ["halozati_hianyos_cel"]
    return cel, []


def kezdemenyezett_limit_ellenorzes(most_ts, korabbi_ts_lista,
                                    nap_max=KEZDEMENYEZETT_NAP_MAX,
                                    nap_ablak=RATE_NAP_ABLAK):
    """The separate, narrower daily cap for initiated replies."""
    okok = []
    napon_belul = [t for t in korabbi_ts_lista if most_ts - t < nap_ablak]
    if len(napon_belul) >= nap_max:
        okok.append("kezdemenyezett_napi_limit")
    return okok
