"""szures.py — the security rules. EVERY rule is a separate, testable function.

This module knows NOTHING about the network, the files or Telegram:
it only computes from text (and from already fetched data structures). This makes
it easy to test and easy to read.

Principle: if a rule is ambiguous, we choose the STRICTER interpretation.
"""

import hashlib
import json
import re
import unicodedata

# --- 0. HOMOGLYPH MAPPING -----------------------------------------------
#
# Sanitization removes the INVISIBLE characters. Against misleading but visible
# homoglyphs (Cyrillic "а", Greek "ο", full-width "＝") a separate,
# CHARACTER-BY-CHARACTER (i.e. length-preserving) mapping is needed. Length
# preservation matters: the frame protection searches in the NORMALIZED form
# but replaces in the ORIGINAL text, and for that the match positions must agree.
# (Homoglyph table: language-independent data; extend it with further
# look-alike characters as needed.)

_CONFUSABLE_PAROK = [
    # Cyrillic → Latin
    ("аеорсухАВЕКМНОРСТУХіѕјԛԝ", "aeopcyxABEKMHOPCTYXisjqw"),
    # Greek → Latin
    ("οαερτυνικχΑΒΕΖΗΙΚΜΝΟΡΤΥΧ", "oaeptvvikxABEZHIKMNOPTYX"),
    # equals-sign look-alikes
    ("＝⁼₌﹦═゠⹀", "======="),
    # hyphen/dash look-alikes
    ("‐‑‒–—―﹘﹣－", "---------"),
    # other full-width punctuation
    ("：；，．／＼＇＂｜！？＃＠", ":;,./\\'\"|!?#@"),
]

_CONFUSABLE_TABLA = {}
for _forras, _cel in _CONFUSABLE_PAROK:
    assert len(_forras) == len(_cel)
    for _a, _b in zip(_forras, _cel):
        _CONFUSABLE_TABLA[ord(_a)] = _b


def homograf_normalizal(szoveg):
    """Character-by-character (length-PRESERVING) homoglyph mapping.

    Not NFKC — that does not preserve length. This function is used where the
    positions must match the original (frame protection).
    """
    if not szoveg:
        return ""
    return szoveg.translate(_CONFUSABLE_TABLA)


def osszehasonlito_alak(szoveg):
    """AGGRESSIVE normalization used for comparison (dedup, deny filter).

    NFKC (a no-break space also becomes an ordinary space) + homoglyph mapping.
    It does NOT preserve length — so it is only suitable where nothing is
    replaced back.
    """
    if not szoveg:
        return ""
    szoveg = unicodedata.normalize("NFKC", szoveg)
    szoveg = homograf_normalizal(szoveg)
    # every space-category character becomes an ordinary space
    szoveg = "".join(" " if unicodedata.category(ch) == "Zs" else ch
                     for ch in szoveg)
    return szoveg


# --- 1. SANITIZATION ----------------------------------------------------

# Code points that the specification requires to be removed (zero-width,
# bidi controls, BOM). The whole Cf (format) category is dropped anyway, but
# these are also listed separately so the rule is readable and testable.
TILTOTT_KODPONTOK = set()
for _a, _b in [
    (0x200B, 0x200F),  # zero-width space .. right-to-left mark
    (0x202A, 0x202E),  # bidi embedding/override
    (0x2066, 0x2069),  # bidi isolation
    (0xFEFF, 0xFEFF),  # BOM / zero-width no-break space
]:
    for _c in range(_a, _b + 1):
        TILTOTT_KODPONTOK.add(_c)

SZOVEG_MAX = 1000  # upper limit of incoming text, in characters
EGYSOROS_MAX = 200  # upper limit of metadata fields


def szanitalas(szoveg, max_hossz=SZOVEG_MAX):
    """Cleans incoming (foreign) text.

    - Unicode NFC normalization (it does not solve homoglyph tricks, but it
      merges scattered combining marks).
    - Removes zero-width, bidi and other format (Cf) characters.
    - Removes control characters; line breaks are kept, tabs become spaces.
    - Line and paragraph separators (U+2028/U+2029/U+0085) replaced by a space.
    - Length limit: max_hossz characters (the rest is simply dropped).

    Always returns a string, even if it did not receive one.
    """
    if szoveg is None:
        return ""
    if not isinstance(szoveg, str):
        szoveg = str(szoveg)

    szoveg = unicodedata.normalize("NFC", szoveg)
    # every line-break variant becomes a uniform \n (\r, \r\n, U+0085)
    szoveg = (szoveg.replace("\r\n", "\n").replace("\r", "\n")
              .replace("", "\n"))

    darabok = []
    for ch in szoveg:
        kod = ord(ch)
        if kod in TILTOTT_KODPONTOK:
            continue
        if ch == "\n":
            darabok.append("\n")
            continue
        if ch == "\t":
            darabok.append(" ")
            continue
        kat = unicodedata.category(ch)
        if kat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            # control (ANSI escape too!), format, surrogate, private, unassigned
            continue
        if kat in ("Zl", "Zp"):  # U+2028 / U+2029
            darabok.append(" ")
            continue
        darabok.append(ch)

    tiszta = "".join(darabok)
    if len(tiszta) > max_hossz:
        tiszta = tiszta[:max_hossz]
    return tiszta


def egysoros(szoveg, max_hossz=EGYSOROS_MAX):
    """SINGLE-LINE metadata field (handle, did, time, uri, cid, alt).

    For the text body keeping line breaks is correct; for metadata fields it is
    NOT: a line break there could bring a fake frame line to the screen (finding M4).
    Sanitization → every whitespace run becomes a single space → frame neutralization.
    """
    tiszta = szanitalas(szoveg, max_hossz)
    tiszta = re.sub(r"\s+", " ", tiszta).strip()
    return keret_veszelytelenites(tiszta)


# A permissive but SHAPE-BOUND pattern for an AT-URI: anything else is not a
# uri, so it is not put on the screen either.
_URI_MINTA = re.compile(r"^at://[A-Za-z0-9._:%~-]+(?:/[A-Za-z0-9._:%~-]+){0,4}$")
_CID_MINTA = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def uri_veszelytelenites(uri, max_hossz=300):
    """Shape check: anything that is not AT-URI-shaped becomes an EMPTY string."""
    tiszta = egysoros(uri, max_hossz)
    return tiszta if _URI_MINTA.match(tiszta) else ""


def cid_veszelytelenites(cid, max_hossz=200):
    """Shape check for the content identifier (alphanumeric only)."""
    tiszta = egysoros(cid, max_hossz)
    return tiszta if _CID_MINTA.match(tiszta) else ""


# --- 2. FRAME PROTECTION -------------------------------------------------

# mutat.py puts foreign text into a hard frame. If the text contains the frame
# itself (or a "===" line), the content could "close" the frame and then write
# text that looks like an instruction. So the frame markers are neutralized
# in the content beforehand.
#
# The patterns are searched in the HOMOGLYPH-NORMALIZED form (a full-width "＝"
# or a Cyrillic "К" does not help), but the replacement is done in the
# ORIGINAL — hence the need for the length-preserving mapping.
_EGYENLOSEG = re.compile(r"={2,}")
# the phrase "külső adat [vége]" (Hungarian: "external data [end]") with any
# accents/case. Hungarian language data: this is the keyword used in the frame
# text of mutat.py (and of the guard's preview); if the frame is changed to another language,
# extend this regex with the new keyword (e.g. external\s+data(\s*end)?).
_KERETSZO = re.compile(r"k[üuÜU]ls[őoŐO]\s+adat(\s*[-:]?\s*v[ée]ge)?"
                       r"|(end\s+of\s+)?external\s+data(\s*[-:]?\s*end)?",
                       re.IGNORECASE)


def keret_veszelytelenites(szoveg, nonce=None):
    """Neutralizes the frame markers in foreign text.

    `==` → `[=]`, "külső adat (vége)" → its spaces replaced by a middle dot, so
    the frame pattern no longer matches, but the reader sees what was there.
    The per-run random frame marker (`nonce`) is filtered out too: the attacker
    cannot guess it, but if they somehow obtained it, it fails here.
    """
    if not szoveg:
        return ""
    if nonce:
        szoveg = szoveg.replace(nonce, "[jelolo]")
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

# (reason_name, pattern) pairs. The reason name goes to the log — the TEXT of
# the match never does.
# Hungarian language data: the patterns ending in _hu and the Hungarian
# alternatives inside the others (e.g. "utasítás" = instruction, "jelszó" =
# password, "futtasd" = run it) catch Hungarian phrasings; for another
# language, add that language's alternatives next to the English ones.
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
    # imitation of the Telegram approval formula (M5)
    ("jovahagyo_formula", r"\b(igen|nem)\s*\d{6,8}\b|j[óo]v[áa]hagy[áa]s\s*:\s*v[áa]laszold"),
]

_INJECTION_FORDITOTT = [(nev, re.compile(m, re.IGNORECASE | re.MULTILINE))
                        for nev, m in INJECTION_MINTAK]

KODBLOKK_KUSZOB = 200  # a code block longer than this is suspicious


def injection_jelolek(szoveg):
    """Returns the list of suspicion reasons (empty list = no flag).

    IMPORTANT: this is only FLAGGING. It drops nothing — the filter is me (the
    agent), not the regular expression; but I should see the warning.
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


# --- 4. DENY FILTER FOR THE OUTGOING TEXT --------------------------------

# Finding A2 (M'2): emptying `tilto_mintak.json` silently switched the filter off.
# So the ESSENTIAL patterns live here, in the CODE. The JSON file can only
# EXTEND them (place names, family names, further regexes) — it cannot switch
# anything off.
# Hungarian language data: some patterns are Hungarian-specific (+36/06 phone
# format, street words such as "utca"/"út"/"tér", licence-plate formats,
# "kukac" = "at" in spelled-out e-mails, "jelszó" = password); for another
# country/language, add equivalent patterns (via the JSON or here).
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
# Hungarian language data: major Hungarian towns; for another country, replace
# or extend this list (or add towns via "iranyitoszam_varosok" in the JSON).
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
    """Loads tilto_mintak.json. Raises an exception on error (better that the
    process stops than that we post without a filter)."""
    with open(utvonal, "r", encoding="utf-8") as f:
        adat = json.load(f)
    if not isinstance(adat, dict):
        raise ValueError("tilto_mintak.json: expected an object")
    return adat


def tilto_mintak_biztos(utvonal):
    """The pattern set actually used: BASE (code) + the extensions from the JSON.

    Returns: `(mintak, megjegyzes)`. `megjegyzes` is None if the JSON loaded
    fine, otherwise a SECRET-FREE sentence explaining why only the base
    patterns are active. The caller prints it — but does NOT stop because of
    it, since the base patterns are in the code, so the filter still protects (M'2).
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
        return mintak, ("tilto_mintak.json cannot be read (%s) — ONLY the base "
                        "patterns built into the code are active, the family names are NOT."
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
    """Lower-case, accent-free, HOMOGLYPH-FREE form — for word-list matching."""
    alap = osszehasonlito_alak(szoveg)
    bontott = unicodedata.normalize("NFD", alap.casefold())
    return "".join(ch for ch in bontott if unicodedata.category(ch) != "Mn")


# Leetspeak classes: forbidden words are searched so that the typical digit
# substitutions do not help either (Kö3).
_LEET = {
    "a": "a4@", "b": "b8", "e": "e3", "g": "g9", "i": "i1l!|",
    "l": "l1i|", "o": "o0", "s": "s5$", "t": "t7", "z": "z2",
}


def _szo_minta(szo):
    """A leet-tolerant regex from a forbidden word."""
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


# Phone number regardless of format: 7+ consecutive digits, with only
# separators in between. This also catches the "06 20 123 45 67" grouping.
_SZAMJEGY_FUTAM = re.compile(r"\d(?:[\s\-./()]{0,2}\d){6,}")


def tilto_ellenorzes(szoveg, mintak):
    """Checks the outgoing text. Returns the list of REASONS.

    Empty list = may go. Not empty = REJECTION.

    The reasons contain ONLY the pattern name, never the matched text —
    so the log and the console do not leak what the filter is protecting.

    Every check runs on the HOMOGLYPH-NORMALIZED form (Kö3): a Cyrillic "а"
    or full-width punctuation does not switch the filter off.
    """
    okok = []
    if szoveg is None:
        szoveg = ""
    normalt = osszehasonlito_alak(szoveg)

    for elem in mintak.get("regexek", []):
        nev = elem.get("nev", "nevtelen_regex")
        try:
            if re.search(elem["minta"], normalt, re.IGNORECASE | re.MULTILINE):
                okok.append(nev)
        except re.error:
            # a broken pattern is counted as a match (stricter interpretation)
            okok.append("hibas_regex:" + nev)

    if _SZAMJEGY_FUTAM.search(normalt):
        okok.append("szamjegy_futam")

    lapos = _ekezet_nelkul(szoveg)
    for kulcs in ("csalad_nevek", "telepulesek", "szo_szerinti"):
        for szo in mintak.get(kulcs, []):
            if not szo:
                continue
            if _szo_minta_gyorsitott(szo).search(lapos):
                okok.append(kulcs)
                break

    # postcode + known place name (Kö5: a year + a capitalized word is NOT one)
    varosok = list(mintak.get("iranyitoszam_varosok", [])) + \
        list(mintak.get("telepulesek", []))
    if varosok:
        for m in re.finditer(r"\b[1-9]\d{3}\s+([^\s,.;!?]+)", normalt):
            szo = _ekezet_nelkul(m.group(1))
            if any(szo.startswith(_ekezet_nelkul(v)) for v in varosok if v):
                okok.append("iranyitoszam_telepules")
                break

    return sorted(set(okok))


# --- 5. LENGTH (graphemes) ----------------------------------------------

POSZT_MAX_GRAFEMA = 300
POSZT_MAX_BAJT = 3000  # Bluesky's byte limit is the same


def grafema_szam(szoveg):
    """APPROXIMATE number of grapheme clusters (the stdlib has no exact counter).

    It merges: combining marks, variation selectors, ZWJ-joined emojis and
    flag pairs. In exotic cases the approximation errs low, so the length
    check also looks at the byte limit.
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
        if ch == "‍":  # zero-width joiner: the next one belongs here too
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


# --- 6. HASH AND CANONICAL OUTGOING RECORD ------------------------------

def szoveg_hash(szoveg):
    """Fingerprint of the outgoing TEXT (for dedup and against text swapping)."""
    return hashlib.sha256((szoveg or "").encode("utf-8")).hexdigest()


def dedup_kulcs(szoveg):
    """The dedup key: a normalized form, so that a no-break space or a homoglyph
    does not yield a "new" text (Kö2). The APPROVAL hash is independent of
    this: it covers the exact text."""
    alap = osszehasonlito_alak(szoveg or "")
    alap = re.sub(r"\s+", " ", alap).strip().casefold()
    return hashlib.sha256(alap.encode("utf-8")).hexdigest()


def kimeno_kanonikus(tipus, szoveg, valasz_cel, langs, kezdemenyezett):
    """The canonical form of the COMPLETE outgoing record.

    This is what gets hashed on approval, and the record to be posted is built
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
    `valasz_cel` — so a `reply` block structurally cannot get into the record (K1).
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

# The CLOSED set of queue states. What is CLOSED does not use quota; everything
# else (even an unknown or misspelled state name) does — the stricter direction (Kö1).
LEZART_ALLAPOTOK = ("elkuldve", "elvetve", "lejart", "elutasitva", "hiba",
                    "piszkozat", "kezi_ellenorzes")
NYITOTT_ALLAPOTOK = ("var", "kuldes_alatt")

# CHANGE 1: an initiated reply (one not triggered by being addressed) has a
# separate, tighter daily cap — WITHIN the overall daily 5.
KEZDEMENYEZETT_NAP_MAX = 2


def engedelylista_betolt(utvonal):
    """Loads szivesen_latnak.json. Missing file = EMPTY list
    (so an initiated reply cannot go to anyone)."""
    try:
        with open(utvonal, "r", encoding="utf-8") as f:
            adat = json.load(f)
    except FileNotFoundError:
        return []
    fiokok = adat.get("fiokok", []) if isinstance(adat, dict) else []
    return [f for f in fiokok if isinstance(f, dict)]


def engedelylistas_did_e(did, engedelylista):
    """Matches on DID only, NEVER on handle: a handle can change, a DID cannot."""
    if not did or not engedelylista:
        return False
    for fiok in engedelylista:
        listas = (fiok.get("did") or "").strip()
        if listas and listas == did.strip():
            return True
    return False


def valasz_cel_ellenorzes(inbox_tetelek, inbox_id, engedelylista=None):
    """The CHEAP, FIRST gate: is the target in the inbox, and can it be replied to.

    IMPORTANT: this is NOT a trust anchor (K3). inbox.jsonl is a plain file that
    the main actor of the threat model (a deceived Claude) writes itself. The
    FINAL word is said by `halozati_cel_ellenorzes`, from the post freshly
    fetched from Bluesky. This function only adds that inbox membership and
    the type are also required — one more gate.

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
        "inbox_id": inbox_id,
    }
    return cel, []


def _emlit_e_minket(rekord, sajat_did):
    """Is OUR DID among the facets of the post (mention)?"""
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

    the posting step (the guard) needs to know separately whether the right to reply comes from
    a MENTION or from the "replied to us" branch — because for the latter the
    parent must also be verified on the NETWORK (Kö'2: the parent uri can be forged).
    """
    return _emlit_e_minket(rekord, sajat_did)


def _nekunk_valaszol_e(rekord, sajat_did):
    """Is the post a DIRECT reply to one of our posts?"""
    if not sajat_did:
        return False
    szulo = ((rekord or {}).get("reply") or {}).get("parent") or {}
    uri = szulo.get("uri") or ""
    return uri.startswith("at://" + sajat_did + "/")


def halozati_cel_ellenorzes(poszt, uri, cid, sajat_did, engedelylista):
    """The REAL opt-in gate: based on the target post FRESHLY fetched from the network (K3).

    `poszt`: one element of `app.bsky.feed.getPosts` (or None if there is none).
    Returns: (cel_dict, okok_lista). Empty reasons = the reply may go.

    It is not inbox.jsonl, nor szivesen_latnak.json that decides, but what
    Bluesky says now: whether the post exists, whether the cid matches, who
    the author is, and whether we were addressed. The allowlist only opens the
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
    if not megszolitas:
        if engedelylistas_did_e(szerzo_did, engedelylista or []):
            kezdemenyezett = True
        else:
            return None, ["halozati_nem_szolitottak_meg"]

    gyoker = ((rekord.get("reply") or {}).get("root") or {})
    cel = {
        "uri": halo_uri,
        "cid": halo_cid,
        "gyoker_uri": uri_veszelytelenites(gyoker.get("uri")) or halo_uri,
        "gyoker_cid": cid_veszelytelenites(gyoker.get("cid")) or halo_cid,
        "szerzo_handle": egysoros(szerzo.get("handle", ""), 120),
        "szerzo_did": egysoros(szerzo_did, 120),
        "szoveg_roviden": keret_veszelytelenites(
            szanitalas(rekord.get("text", ""), 300)),
        "kezdemenyezett": kezdemenyezett,
        "forras": "halozat",
    }
    if not cel["uri"] or not cel["cid"]:
        return None, ["halozati_hianyos_cel"]
    return cel, []


def kezdemenyezett_limit_ellenorzes(most_ts, korabbi_ts_lista,
                                    nap_max=KEZDEMENYEZETT_NAP_MAX,
                                    nap_ablak=RATE_NAP_ABLAK):
    """The separate, tighter daily cap of initiated replies."""
    okok = []
    napon_belul = [t for t in korabbi_ts_lista if most_ts - t < nap_ablak]
    if len(napon_belul) >= nap_max:
        okok.append("kezdemenyezett_napi_limit")
    return okok
