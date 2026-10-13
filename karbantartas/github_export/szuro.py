"""szuro.py ("szűrő" = filter) — personal-data and secret filter for the GitHub package (fail-closed).

Goal: publish the skeleton of a personal, home Claude setup so that nothing personal goes out with it.

The BLOCKLIST (names, places, identifiers) is itself personal data, so it does NOT live in the repo but in a local
file (default: %LOCALAPPDATA%\\github_export\\tiltolista.json; "tiltólista" = blocklist). The exporter reads it from
there and never copies it out. The report does not quote the matched word either: only its kind, the line and a
masked form (first letter + asterisks).
"""
import json, os, re

ALAP_TILTOLISTA = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "github_export",
                               "tiltolista.json")

# Secret patterns
TITOK = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("google-key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("telegram-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack/aws", re.compile(r"\b(?:xox[abp]|AKIA)[A-Za-z0-9-]{12,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password line", re.compile(r"(?i)\b(?:password|passwd|jelsz[oó])\s*[:=]\s*['\"]?\S{4,}")),
    ("key line", re.compile(r"(?i)\b(?:api[_-]?key|token|secret|xi-api-key)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}")),
    ("twilio-sid", re.compile(r"\bAC[0-9a-f]{32}\b")),
]
# General personal-data patterns
ALTALANOS = [
    ("e-mail", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("IPv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("long identifier", re.compile(r"(?<![\w.-])\d{8,12}(?![\w.-])")),
    ("Windows user path", re.compile(r"(?i)[A-Z]:[\\/]+Users[\\/]+(?!\{)[^\\/\s\"'`]+")),
    ("Linux-home", re.compile(r"/home/(?!\{)[a-z_][a-z0-9_-]*")),
    ("phone number", re.compile(r"(?<!\d)(?:\+36|06)[ -]?\d{1,2}[ -]?\d{3}[ -]?\d{3,4}(?!\d)")),
    ("mDNS hostname", re.compile(r"\b[a-z0-9-]+\.local\b")),
]
# Harmless exceptions (the code itself uses them)
ENGEDETT = [
    re.compile(r"^(?:127\.0\.0\.1|0\.0\.0\.0|255\.255\.255\.\d+|\d+\.\d+\.\d+\.0)$"),
    # RFC 5737 documentation ranges: safe for example IPs in config.example files
    re.compile(r"^(?:192\.0\.2|198\.51\.100|203\.0\.113)\.\d{1,3}$"),
    # public DNS resolvers (used only to pick the local network interface)
    re.compile(r"^(?:8\.8\.8\.8|8\.8\.4\.4|1\.1\.1\.1|1\.0\.0\.1|9\.9\.9\.9)$"),
    # well-known example values: the 7-day background timeout in ms, example phone numbers
    re.compile(r"^(?:604800000|1234567890|14155551234)$"),
    re.compile(r"^noreply@anthropic\.com$"),
    re.compile(r"^[a-z0-9._-]+@example\.(?:com|org)$"),
]


# Accent-insensitive matching: every vowel matches all its accented/long forms, so a blocklist word
# also catches its inflected forms where a final vowel lengthens (e.g. "Kata" → "Katáé") and
# spellings with or without accents.
_EKEZET = {}
for _csoport in ("aáà", "eéè", "iíì", "oóöőò", "uúüűù"):
    for _b in _csoport:
        _EKEZET[_b] = _csoport
        _EKEZET[_b.upper()] = _csoport.upper()


def _ekezet_minta(szo):
    """A regex for `szo` where each vowel is a character class of its accent variants."""
    return "".join(f"[{_EKEZET[c]}]" if c in _EKEZET else re.escape(c) for c in szo)


def tiltolista_betolt(ut=None):
    """{'szavak': [...], 'regexek': [...]} ("words", "regexes") — if the file is missing, the filter does NOT run (fail-closed)."""
    ut = ut or ALAP_TILTOLISTA
    if not os.path.isfile(ut):
        raise FileNotFoundError(f"no blocklist: {ut} (the filter does not run without it)")
    d = json.load(open(ut, encoding="utf-8"))
    szavak = [s for s in d.get("szavak", []) if s.strip()]
    if not szavak:
        raise ValueError("empty blocklist — the filter does not run without it")
    minta = [("blocklist", re.compile(r"(?i)(?<![\wáéíóöőúüű])(?:" +
                                      "|".join(_ekezet_minta(s) for s in sorted(szavak, key=len, reverse=True)) +
                                      r")"))]   # NO word-end boundary on purpose: Hungarian suffixes ("…ban", "…nak") must match too
    for i, r in enumerate(d.get("regexek", [])):
        minta.append((f"blocklist-regex#{i}", re.compile(r)))
    return minta


def maszk(s):
    s = s.strip()
    return (s[:1] + "*" * min(len(s) - 1, 8)) if s else "*"


def _engedett(talalat):
    return any(e.match(talalat) for e in ENGEDETT)


def vizsgal(szoveg, tilto):
    """List of (line number, kind, masked) — an empty list = clean."""
    lelet = []
    for n, sor in enumerate(szoveg.splitlines(), 1):
        for fajta, m in TITOK + ALTALANOS + tilto:
            for t in m.finditer(sor):
                if not _engedett(t.group(0)):
                    f = fajta
                    if fajta == "long identifier" and re.fullmatch(r"1[78]\d{8}(?:\d{3})?", t.group(0)):
                        f = "long identifier (timestamp?)"   # a separate kind to reduce noise, but it still blocks
                    lelet.append((n, f, maszk(t.group(0))))
    return lelet


def tilto_talal(sor, tilto):
    return any(m.search(sor) for _, m in tilto)
