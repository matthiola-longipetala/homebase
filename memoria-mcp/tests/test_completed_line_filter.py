# -*- coding: utf-8 -*-
"""The "completed" filter (`_is_completed_line`) must match on WORD BOUNDARIES, and must not be silent.

Why it exists: get_deadlines() once silently dropped a real, future deadline
bullet. `_is_completed_line` looked for the Hungarian "kész" (done) marker as a
PLAIN SUBSTRING and found it inside "elkészíti" ("will prepare") in a
subordinate clause -- which was not even about the bullet's own status. Same
failure class as reading every ISO date as a deadline: guessing meaning from
prose with a substring blacklist. Hungarian agglutination makes that produce
false positives inevitably; English has the same trap ("undone", "closedown").

The Hungarian lines below are PARSING fixtures: the server recognises these
Hungarian completed-words, so they have to be tested in Hungarian. Each has an
English counterpart where English has one.

Run:  python -X utf8 test_completed_line_filter.py
"""
import re
from datetime import datetime, timedelta

import _support as S

C = S.Checker("Completed-line filter (word boundaries, not silent)")
ROOT = S.make_memory_root("completed_")
M = S.load_server(ROOT)

# (line, expected_completed?, why)
CASES = [
    # --- Hungarian markers (parsed by the server) ---
    ("- **2026-09-30: a pályázat beadási VÉGHATÁRIDEJE.** Ugyanezen a napon "
     "az iroda elkészíti az összesítő táblázatot.",
     False, "THE ORIGINAL BUG: 'elKÉSZíti' (will prepare) -> the 'kész' substring marked it closed"),
    ("- **2026-09-01: készül a dolgozat végső változata**", False, "'készül' (in progress) is not 'kész'"),
    ("- **2026-09-02: a minta-készítés határideje**", False, "'készítés' (making) is not 'kész'"),
    ("- **2026-09-03: készlet-leltár**", False, "'készlet' (stock) is not 'kész'"),
    ("- **2026-09-04: készséggel vállalt feladat**", False, "'készség' (readiness) is not 'kész'"),
    ("- **2026-09-05: elkészülte előtt még egy kör**", False, "'elkészülte' is a derived form"),
    ("- **2026-09-06: a dolgozat KÉSZ, nincs teendő**", True, "standalone word -> closed"),
    ("- **2026-09-07: a dolgozat kész.**", True, "standalone word, with a full stop"),
    ("- ✅ **2026-09-08: KARBANTARTÁS-BLOKK -- LEZÁRVA**", True, "✅ is position-independent, a strong marker"),
    ("- **2026-09-09: a pályázat leadva**", True, "'leadva' (handed in) standalone word"),
    ("- **2026-09-10: a mérés befejezve**", True, "'befejezve' (finished) standalone word"),
    ("- **2026-09-11: a kérdés megoldva**", True, "'megoldva' (solved) standalone word"),
    ("- **2026-09-12: a szál LEZÁRVA**", True, "'lezárva' (closed) marker"),
    ("- **2026-09-13: elkészült a terv**", True, "'elkészült' (got done) standalone word"),
    ("- **2026-09-14: teljesítve minden ZH**", True, "'teljesítve' (fulfilled) standalone word"),
    ("- **2026-09-15: sima határidő, semmi marker**", False, "control: no marker"),
    # ⚠️ KNOWN LIMIT, documented here on purpose: a marker written WITHOUT accents
    # is not recognised. If that ever changes, this line says where to look.
    ("- **2026-09-16: a dolgozat kesz (ekezet nelkul)**", False,
     "KNOWN LIMIT: an unaccented marker does not match"),
    # --- English markers ---
    ("- **2026-09-17: the report is done**", True, "'done' standalone word"),
    ("- **2026-09-18: application submitted**", True, "'submitted' standalone word"),
    ("- **2026-09-19: ticket CLOSED**", True, "'closed' standalone word, case-insensitive"),
    ("- **2026-09-20: thesis finished.**", True, "'finished' with a full stop"),
    ("- **2026-09-21: nothing may be left undone**", False, "'undone' is not 'done'"),
    ("- **2026-09-22: closedown of the lab**", False, "'closedown' is not 'closed'"),
    ("- **2026-09-23: unfinished business with the landlord**", False, "'unfinished' is not 'finished'"),
    ("- **2026-09-24: a donation drive starts**", False, "'donation' is not 'done'"),
    ("- **2026-09-25: plain deadline, no marker**", False, "control: no marker"),
]

C.section("1. Completed-marker word boundary ('kész' must NOT match inside 'elkészíti')")
fn = M._is_completed_line
for line, expected, why in CASES:
    got = fn(line)
    C.check(f"completed={expected!s:5} | {why}", got == expected, f"got {got} for {line[:70]!r}")

C.section("2. The filter is NOT silent (the `suppressed` bucket)")
future = (datetime.now() + timedelta(days=40)).strftime("%Y-%m-%d")
for word in ("KÉSZ", "DONE"):
    text = f"- **{future}: a real deadline, but its text says {word}**"
    supp = []
    found = M._scan_deadline_dates(text, set(), -7, suppressed=supp)
    C.check(f"[{word}] the completed-word line is NOT returned as a deadline", not found, found)
    C.check(f"[{word}] AND it shows up in the suppressed bucket ({future})",
            len(supp) == 1 and supp[0]["date"] == future, supp)
past = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
supp2 = []
M._scan_deadline_dates(f"- ✅ **{past}: this really is closed**", set(), -7, suppressed=supp2)
C.check("a closed line with a PAST date makes no noise (only future dates matter)", not supp2, supp2)

C.section("3. End-to-end: get_deadlines() on a synthetic context file")
# Replaces the old check against the real context file: the same shape of
# bullet (future deadline + a derived 'kész' form in the prose), plus the
# English counterparts, run through the whole get_deadlines() pipeline.
D1 = (datetime.now() + timedelta(days=18)).strftime("%Y-%m-%d")
D2 = (datetime.now() + timedelta(days=19)).strftime("%Y-%m-%d")
D3 = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
D4 = (datetime.now() + timedelta(days=21)).strftime("%Y-%m-%d")
for header in ("## ⏰ SURGOS / KOZELGO HATARIDOK", "## ⏰ URGENT / UPCOMING DEADLINES"):
    S.write(ROOT, M.CONTEXT_FILE, "\n".join([
        "# Current situation",
        "",
        header,
        f"- **{D1}: grant application deadline.** Ugyanezen a napon az iroda "
        "elkészíti az összesítőt.",
        f"- **{D2}: tax form** -- nothing may be left undone.",
        f"- **{D3}: the draft is DONE** -- reminder only.",
        f"- **{D4}: closedown of the lab, move the samples out.**",
        "",
        "## Notes",
        "- nothing here",
        "",
    ]))
    out = M.get_deadlines()
    dl_rows = [l for l in out.splitlines()
               if re.match(r"^(OVERDUE|\[!+\] \w+|LATER) \| \d{4}-\d{2}-\d{2}", l)]
    dl_dates = [l.split("|")[1].split()[0] for l in dl_rows]
    supp_part = out.split("Filtered out as completed")[-1] if "Filtered out as completed" in out else ""
    tag = "hu" if "SURGOS" in header else "en"
    C.check(f"[{tag}] the 'elkészíti' bullet passes the filter (it is a deadline)", D1 in dl_dates, out)
    C.check(f"[{tag}] the 'undone' bullet passes the filter", D2 in dl_dates, out)
    C.check(f"[{tag}] the 'closedown' bullet passes the filter", D4 in dl_dates, out)
    C.check(f"[{tag}] the DONE bullet is NOT a deadline", D3 not in dl_dates, out)
    C.check(f"[{tag}] ...but it is reported in the 'filtered out as completed' block",
            D3 in supp_part, out)

C.finish()
