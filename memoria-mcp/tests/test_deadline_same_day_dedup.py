# -*- coding: utf-8 -*-
"""Two separate deadlines on the same day (the head-date exception to date dedup).

Why this file exists: a REAL deadline due the next day once vanished from
get_deadlines without a trace, because another bullet had already "taken"
that date (a global, date-based, SILENT dedup). The fixtures are written so
that they FAIL if the exception slides back into blind dedup -- or if it
overshoots and lets prose repetitions through as well.

Run:  python -X utf8 test_deadline_same_day_dedup.py
"""
from datetime import datetime, timedelta

import _support as S

C = S.Checker("Same-day deadlines (head-date dedup exception)")
M = S.load_server(S.make_memory_root("dedup_"))

# RELATIVE dates: a fixed date must not be used in a fixture whose expected
# value depends on the clock (such an instrument silently rots).
D3 = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
D9 = (datetime.now() + timedelta(days=9)).strftime("%Y-%m-%d")


def scan(text, allow):
    seen = set()
    return M._scan_deadline_dates(text, seen, min_days=-7, allow_head_duplicates=allow)


C.section("1. TWO SEPARATE DEADLINES ON THE SAME DAY -> BOTH")
txt = (f"- **{D3}: end of the lab's summer SHUTDOWN.** Per the calendar it lasts until then.\n"
       f"- **{D3} (Sunday evening): FINALISE the argument draft** -- it goes in the next day.\n")
got = scan(txt, allow=True)
C.check("both bullets come back", len(got) == 2, f"got={len(got)}")
C.check("the second bullet's text is there too",
        any("argument draft" in d["description"] for d in got),
        str([d["description"][:40] for d in got]))

C.section("2. REGRESSION: a prose REPETITION still drops out")
txt2 = (f"- **{D3}: end of the lab's summer SHUTDOWN.** Per the calendar it lasts until then.\n"
        f"- After the shutdown ({D3}) the first working day is the next Monday, see above.\n"
        f"- Reminder: the {D3} line above is the canonical one, this is only a reference.\n")
got2 = scan(txt2, allow=True)
C.check("only ONE item comes back (the prose does not)", len(got2) == 1, f"got={len(got2)}")

C.section("3. Two mentions within one line do not double")
txt3 = f"- **{D9}: Make-up workday** -- because of the {D9} working-time budget.\n"
got3 = scan(txt3, allow=True)
C.check("one item", len(got3) == 1, f"got={len(got3)}")

C.section("4. allow=False (the scan OUTSIDE the section) -> old behaviour")
got4 = scan(txt, allow=False)
C.check("switched off it still deduplicates", len(got4) == 1, f"got={len(got4)}")

C.section("5. _is_head_date unit checks")
C.check("bold bullet head", M._is_head_date(f"- **{D3}: something**", D3))
C.check("bare bullet head", M._is_head_date(f"- {D3}: something", D3))
C.check("in prose it is NOT a head", not M._is_head_date(f"- After the shutdown ({D3})", D3))
C.check("another date in the head -> not this one", not M._is_head_date(f"- **{D9}: x** and {D3}", D3))

C.finish()
