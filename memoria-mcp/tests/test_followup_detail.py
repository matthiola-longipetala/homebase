# -*- coding: utf-8 -*-
"""`get_followup(ids)` -- regression test.

What it measures: a reconstruction of a real gap. With many open threads the
spill guard of `get_followups()` falls back to a TITLE-LINE VIEW and the notes
disappear; its own message pointed to `followups.json`, which is a single
~50 KB line (too big for a file-read tool, and line offsets are useless on it).
The test checks that the detail tool returns the details COMPLETELY and
NOT SILENTLY: unknown ids, unparseable tokens and cuts are all said out loud.

Negative control (optional): if a pre-patch
`server_backup_pre_followup_detail_*.py` is present, it proves the gap (the old
code has no `get_followup`). The backup is not shipped; without it that part is
skipped and the main checks still run.

Run:  python -X utf8 test_followup_detail.py
"""
import json
import re
from datetime import datetime, timedelta

import _support as S

C = S.Checker("get_followup(ids) -- detail view")

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
FUTURE = (datetime.now() + timedelta(days=9)).strftime("%Y-%m-%d")
LONG_NOTE = "X" * 20000
PRE_WEEKEND = "weekend evening, when the workshop is free"

FOLLOWUPS = {"followups": [
    {"id": 101, "item": "Reply from the agency", "status": "open", "expected_by": TODAY,
     "note": "SECRET-MARKER-ALPHA\nA multi-line note that the title-line view dropped.",
     "precondition": "", "history": []},
    {"id": 113, "item": "Continue the reading list", "status": "open", "expected_by": YESTERDAY,
     "note": "SECRET-MARKER-BETA", "precondition": PRE_WEEKEND,
     "history": [{"on": "2026-09-13", "field": "expected_by", "from": "2026-09-14",
                  "to": "2026-09-20", "reason": "first postponement"},
                 {"on": "2026-09-20", "field": "note", "reason": "second refinement"}]},
    {"id": 122, "item": "Encryption package", "status": "open", "expected_by": FUTURE,
     "note": "SECRET-MARKER-GAMMA", "precondition": "", "history": []},
    {"id": 60, "item": "Old, closed thread", "status": "resolved", "expected_by": "",
     "resolved_on": "2026-08-18", "outcome": "CLOSING-MARKER", "note": "n", "history": []},
    {"id": 77, "item": "Tied to a precondition", "status": "open", "expected_by": "",
     "note": "SECRET-MARKER-DELTA", "precondition": "next live hardware session", "history": []},
    {"id": 88, "item": "Very long note", "status": "open", "expected_by": "",
     "note": LONG_NOTE, "precondition": "", "history": []},
    {"id": 89, "item": "Second long note", "status": "open", "expected_by": "",
     "note": LONG_NOTE, "precondition": "", "history": []},
]}

tmp = S.make_memory_root("fu_detail_")
S.write(tmp, "kontextus/followups.json", json.dumps(FOLLOWUPS, ensure_ascii=False))

C.section("0. PROOF OF THE GAP: the old code had no targeted detail view")
OLD = S.find_backup("server_backup_pre_followup_detail_*.py")
if OLD is None:
    C.skip_no_backup("proof of the gap on the pre-patch code")
else:
    old = S.load_server(tmp, server_path=OLD)
    C.check("OLD: there is no get_followup", not hasattr(old, "get_followup"))
    C.check("OLD: the list itself exists (control)", "#101" in old.get_followups(include_resolved=False))

C.section("1. Basic: the full detail of one thread")
new = S.load_server(tmp)
C.check("NEW: get_followup exists", hasattr(new, "get_followup"))
r = new.get_followup(ids="101")
head = r.split("\n")[0]
C.check("the FULL note text appears", "SECRET-MARKER-ALPHA" in r, r[:200])
C.check("the multi-line part of the note is there too", "title-line view dropped" in r)
C.check("the header contains the id", "#101" in r)
C.check("TODAY tag is right", "TODAY" in head, head)
C.check("no other thread leaked in", "SECRET-MARKER-BETA" not in r)

C.section("2. Several threads, several separators")
for form in ["101,113,122", "101 113 122", "#101,#113 #122", "101;113;122"]:
    r = new.get_followup(ids=form)
    ok = all(j in r for j in ("SECRET-MARKER-ALPHA", "SECRET-MARKER-BETA", "SECRET-MARKER-GAMMA"))
    C.check(f"form {form!r} -> all 3 threads", ok)

C.section("3. Precondition, history, overdue/future tag")
r = new.get_followup(ids="113")
C.check("the PRECONDITION line appears", "PRECONDITION" in r and PRE_WEEKEND in r, r)
C.check("the reschedule history appears", "RESCHEDULE HISTORY (2)" in r, r)
C.check("the REASON of the history shows (not a hidden field)",
        "first postponement" in r and "second refinement" in r, r)
C.check("OVERDUE tag", re.search(r"OVERDUE by 1 days?\b", r.split("\n")[0]) is not None, r.split("\n")[0])
r = new.get_followup(ids="122")
C.check("future tag with a day count", re.search(r"in 9 days?\b", r.split("\n")[0]) is not None,
        r.split("\n")[0])
r = new.get_followup(ids="77")
C.check("no date + precondition -> 'tied to a precondition'",
        "tied to a precondition" in r.split("\n")[0], r.split("\n")[0])

C.section("4. A closed thread can be fetched too")
r = new.get_followup(ids="60")
C.check("CLOSED mark", "CLOSED" in r, r)
C.check("closing date", "2026-08-18" in r)
C.check("outcome appears", "CLOSING-MARKER" in r)

C.section("5. A MISSING id IS NOT SILENT (the most important part)")
r = new.get_followup(ids="999")
C.check("unknown id -> loud warning", "No such id" in r and "#999" in r, r)
r = new.get_followup(ids="101,999")
C.check("mixed: the existing one comes, the missing one is STATED",
        "SECRET-MARKER-ALPHA" in r and "#999" in r, r[:300])
r = new.get_followup(ids="abc")
C.check("unparseable token -> loud", "Unparseable token" in r or "no parseable id" in r.lower(), r)
r = new.get_followup(ids="101,abc")
C.check("mixed: the unparseable token is stated next to a real block",
        "SECRET-MARKER-ALPHA" in r and "Unparseable token" in r and "abc" in r, r[:300])
r = new.get_followup(ids="   ")
C.check("empty input -> useful guidance, no crash", "get_followup" in r, r)

C.section("6. THE CUT IS NOT SILENT EITHER -- and no thread VANISHES")
r = new.get_followup(ids="88,89")
C.check("the cut is stated", "SHORTENED" in r and "CUT HERE" in r, r[:300])
C.check("BOTH threads' headers are there (neither vanished)", "#88" in r and "#89" in r)
C.check("the cut block says how to fetch it on its own", "get_followup(ids=" in r)
C.check("the output stays around the threshold (no spill)",
        len(r) < new._FOLLOWUP_DETAIL_LIMIT + 3000, str(len(r)))
r1 = new.get_followup(ids="88")
C.check("requested on its own, the long thread is COMPLETE (no needless cut)",
        "CUT HERE" not in r1 or len(r1) > 15000)

C.section("7. No regression: get_followups works as before")
lst = new.get_followups(include_resolved=False)
C.check("the list contains the open threads", "#101" in lst and "#113" in lst)
C.check("the closed one is NOT listed by default", "#60" not in lst)
lst2 = new.get_followups(include_resolved=True)
C.check("include_resolved=True brings the closed one", "#60" in lst2)

C.finish()
