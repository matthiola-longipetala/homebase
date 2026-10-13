# -*- coding: utf-8 -*-
"""Associative recall ("emlék" = memory) — the shared part: sources, chunking, author tag, secret filter.

Unit: a paragraph (a block delimited by empty lines); in a long list, one list item; a block that is
too long is cut at sentence boundaries into ~1200-character chunks. The heading above a block goes into
the embedding (as context) but not into the output.

Configuration (all optional): a JSON file in EMLEK_CONFIG, or emlek_config.json next to this file
(see emlek_config.example.json). Without it: the memory folder is CLAUDE_MEMORY_DIR, else MEMORIA_ROOT (or ../memoria); env vars win over the config file,
the sources are its naplo/, kontextus/ and projektek/ folders ("journal", "context", "projects"),
and every chunk's author is "Claude" (journal) or "shared" (everything else).
"""
import hashlib
import json
import os
import re
from datetime import datetime

ITT = os.path.dirname(os.path.abspath(__file__))


def _config():
    ut = os.environ.get("EMLEK_CONFIG") or os.path.join(ITT, "emlek_config.json")
    try:
        with open(ut, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


CFG = _config()
MEMORIA = os.path.expanduser(os.environ.get("CLAUDE_MEMORY_DIR") or os.environ.get("MEMORIA_ROOT") or CFG.get("memory_dir")
                             or os.path.join(os.path.dirname(ITT), "memoria"))
# Claude Code's own auto-memory folder (~/.claude/projects/<slug>/memory) — optional extra source.
AUTOMEM = os.path.expanduser(os.environ.get("CLAUDE_AUTOMEM_DIR") or CFG.get("automemory_dir") or "")
INDEX_DIR = os.path.expanduser(os.environ.get("EMLEK_INDEX_DATA") or CFG.get("index_dir")
                               or os.path.join(ITT, "adat"))   # "adat" = data

# (layer, folder, recursive?) — the base layer. A layer name of "naplo" (journal) or "archiv"
# switches on the journal author rules below.
FORRASOK_ALAP = [
    (r["layer"], os.path.join(MEMORIA, r["folder"]), False)
    for r in CFG.get("sources", [
        {"layer": "naplo", "folder": "naplo"},
        {"layer": "kontextus", "folder": "kontextus"},
        {"layer": "projektek", "folder": "projektek"},
    ])
]
if AUTOMEM:
    FORRASOK_ALAP.append(("automem", AUTOMEM, False))
# A separate, switchable layer (--archiv): archived old journals.
FORRASOK_ARCHIV = [
    ("archiv", os.path.join(MEMORIA, CFG.get("archive_folder", os.path.join("archiv", "raw", "naplo"))), False),
]

# Files that are not memories: handover transcripts (rewritten chat copies, they duplicate the journal),
# backups, the index file.
KIZART_MINTAK = [
    re.compile(r"^atado_", re.I),
    re.compile(r"\.bak", re.I),
    re.compile(r"_backup", re.I),
    re.compile(r"^MEMORY\.md$", re.I),
] + [re.compile(m, re.I) for m in CFG.get("exclude_patterns", [])]

# Secret filter: a chunk containing any of these is left out (and counted).
TITOK_MINTAK = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}\b"),        # Telegram bot token shape
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\b(?:xox[abp]|AKIA)[A-Za-z0-9-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|passwd|jelsz[oó])\s*[:=]\s*\S{4,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
]

DATUM_RE = re.compile(r"(20\d\d-\d\d-\d\d)")
FELSOROLAS_RE = re.compile(r"^\s{0,3}(?:[-*•]|\d+[.)])\s+")
CIMSOR_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
MAX_DARAB = 1200
MIN_DARAB = 30

# Authorship ("who lived this"), from the config:
#   "session_authors":  {"<first 8 chars of a session id>": "Name"}   (frontmatter originSessionId)
#   "journal_rules":    [{"pattern": "_night", "author": "Night Claude", "since": "2026-01-01"}, ...]
#                       first match on the journal file name wins; "since" is optional
#   "journal_default":  "Claude"
#   "frontmatter_rules": [{"contains": ["night claude"], "author": "Night Claude"}, ...]
#                       matched against the created/author/source frontmatter fields
#   "default_author":   "shared"
SESSION_SZERZO = CFG.get("session_authors", {})
NAPLO_SZABALYOK = CFG.get("journal_rules", [])
NAPLO_ALAP = CFG.get("journal_default", "Claude")
FM_SZABALYOK = CFG.get("frontmatter_rules", [])
KOZOS = CFG.get("default_author", "shared")


def kizart(fajlnev):
    return any(m.search(fajlnev) for m in KIZART_MINTAK)


def titkos(szoveg):
    return any(m.search(szoveg) for m in TITOK_MINTAK)


def frontmatter(sorok):
    """(fm_dict, index of the first content line). Loose YAML: key: value lines."""
    if not sorok or sorok[0].strip() != "---":
        return {}, 0
    fm = {}
    for i in range(1, min(len(sorok), 60)):
        s = sorok[i]
        if s.strip() == "---":
            return fm, i + 1
        m = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*)$", s)
        if m:
            fm[m.group(1).lower()] = m.group(2).strip().strip('"')
    return {}, 0


def fajl_datum(fajlnev, fm, utvonal):
    m = DATUM_RE.search(fajlnev)
    if m:
        return m.group(1)
    for k in ("date", "datum", "created", "modified"):
        m = DATUM_RE.search(fm.get(k, ""))
        if m:
            return m.group(1)
    return datetime.fromtimestamp(os.path.getmtime(utvonal)).strftime("%Y-%m-%d")


def szerzo(reteg, fajlnev, fm, datum):
    sid = fm.get("originsessionid", "")[:8]
    if sid in SESSION_SZERZO:
        return SESSION_SZERZO[sid]
    if reteg in ("naplo", "archiv"):
        for r in NAPLO_SZABALYOK:
            if re.search(r.get("pattern", "$^"), fajlnev, re.I) and datum >= r.get("since", ""):
                return r.get("author", NAPLO_ALAP)
        return NAPLO_ALAP
    jel = " ".join(fm.get(k, "") for k in ("created", "szerzo", "author", "forras", "source")).lower()
    for r in FM_SZABALYOK:
        if any(x.lower() in jel for x in r.get("contains", [])):
            return r.get("author", KOZOS)
    return KOZOS


def _vag(szoveg):
    """Cuts a long text at sentence boundaries into ~MAX_DARAB chunks."""
    if len(szoveg) <= MAX_DARAB:
        return [szoveg]
    mondatok = re.split(r"(?<=[.!?…])\s+", szoveg)
    ki, akt = [], ""
    for m in mondatok:
        while len(m) > MAX_DARAB:            # a giant line without sentences (a table etc.)
            if akt:
                ki.append(akt)
                akt = ""
            ki.append(m[:MAX_DARAB])
            m = m[MAX_DARAB:]
        if len(akt) + len(m) + 1 > MAX_DARAB and akt:
            ki.append(akt)
            akt = m
        else:
            akt = (akt + " " + m).strip()
    if akt:
        ki.append(akt)
    return ki


def _blokkok(sorok, kezd):
    """(first line number from 1, [lines]) blocks delimited by empty lines."""
    blokk, eleje = [], None
    for i in range(kezd, len(sorok)):
        if sorok[i].strip():
            if eleje is None:
                eleje = i + 1
            blokk.append(sorok[i])
        elif blokk:
            yield eleje, blokk
            blokk, eleje = [], None
    if blokk:
        yield eleje, blokk


def _felsorolas_pontok(eleje, blokk):
    """Splits a list block into its items (with their continuation lines)."""
    pontok, akt, akt_eleje = [], [], eleje
    for j, s in enumerate(blokk):
        if FELSOROLAS_RE.match(s) and akt:
            pontok.append((akt_eleje, akt))
            akt, akt_eleje = [], eleje + j
        akt.append(s)
    if akt:
        pontok.append((akt_eleje, akt))
    return pontok


def darabol(utvonal, reteg):
    """The chunks of one file: a list of dict(sor, cimsor, szoveg) (line, heading, text) + the count left out as secret."""
    with open(utvonal, encoding="utf-8", errors="replace") as f:
        sorok = f.read().splitlines()
    fm, kezd = frontmatter(sorok)
    fajlnev = os.path.basename(utvonal)
    datum = fajl_datum(fajlnev, fm, utvonal)
    ki_szerzo = szerzo(reteg, fajlnev, fm, datum)
    darabok, titok_db, cimsor = [], 0, ""
    for eleje, blokk in _blokkok(sorok, kezd):
        # headings at the start of a block update the context
        while blokk and CIMSOR_RE.match(blokk[0]):
            cimsor = CIMSOR_RE.match(blokk[0]).group(1).strip()
            blokk = blokk[1:]
            eleje += 1
        if not blokk:
            continue
        szoveg = " ".join(s.strip() for s in blokk)
        pontos = sum(1 for s in blokk if FELSOROLAS_RE.match(s))
        egysegek = (_felsorolas_pontok(eleje, blokk)
                    if pontos >= 2 and len(szoveg) > 600 else [(eleje, blokk)])
        for e_eleje, e_sorok in egysegek:
            t = " ".join(s.strip() for s in e_sorok)
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) < MIN_DARAB:
                continue
            if titkos(t):
                titok_db += 1
                continue
            for resz in _vag(t):
                darabok.append({"sor": e_eleje, "cimsor": cimsor[:120], "szoveg": resz})
    meta = {"reteg": reteg, "fajl": utvonal, "datum": datum, "szerzo": ki_szerzo}
    return meta, darabok, titok_db


def forras_fajlok(archiv=False):
    retegek = FORRASOK_ALAP + (FORRASOK_ARCHIV if archiv else [])
    for reteg, mappa, _ in retegek:
        if not os.path.isdir(mappa):
            continue
        for nev in sorted(os.listdir(mappa)):
            if nev.lower().endswith(".md") and not kizart(nev):
                yield reteg, os.path.join(mappa, nev)


def szoveg_hash(modell, szoveg):
    return hashlib.sha1((modell + "\x00" + szoveg).encode("utf-8")).hexdigest()


def rovid_ut(utvonal):
    """A short, display path: relative to the memory folder (or automem/…)."""
    u = utvonal.replace("\\", "/")
    elok = [MEMORIA.replace("\\", "/") + "/"] + ([AUTOMEM.replace("\\", "/") + "/"] if AUTOMEM else [])
    for elo in elok:
        if u.startswith(elo):
            return ("automem/" if AUTOMEM and elo.startswith(AUTOMEM.replace("\\", "/")) else "") + u[len(elo):]
    return u


def json_sor(obj):
    return json.dumps(obj, ensure_ascii=False)
