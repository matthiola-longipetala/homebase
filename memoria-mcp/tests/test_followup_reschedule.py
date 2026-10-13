# -*- coding: utf-8 -*-
"""reschedule_followup + the structured `precondition` field.

Background: there was no way to change the date or text of an existing
follow-up, so every time the old thread had to be closed and a NEW one opened
for the same thing. Measured on a real tracker: about a third of all entries
were such successors, and half of the open list was administrative duplicates
(several known chains of 2-3 threads for one and the same thing).

THE SUBJECT IS THREE THINGS, and EACH can fail on its own:
  (1) MUTATION LOGIC: what changes, what does not, and what the tool refuses;
  (2) AUDIT: the history is append-only and `reason` is required (a silent
      rewrite is exactly the stale-cache failure class this was built against);
  (3) RENDERING: the history and the precondition SHOW in get_followups -- a
      hidden field is worth as much as nothing.

How it is tested: the REAL server module; the store (`_load_followups` /
`_save_followups`) is an in-memory dict and the clock (`_today`,
`_days_until`) is fixed, so the results do not depend on the disk or the date.

Run:  python -X utf8 test_followup_reschedule.py
"""
import copy
from datetime import datetime

import _support as S

C = S.Checker("reschedule_followup + precondition")
M = S.load_server(S.make_memory_root("resched_"))

TODAY = "2026-08-20"
_DB = {"followups": [], "next_id": 1}


def _days_until(s):
    d = M._parse_date((s or "").strip())
    if d is None:
        return None
    return (d.date() - datetime.strptime(TODAY, "%Y-%m-%d").date()).days


def _save(data):
    global _DB
    _DB = data


M._today = lambda: TODAY
M._days_until = _days_until
M._load_followups = lambda: _DB
M._save_followups = _save
reschedule_followup = M.reschedule_followup
get_followups = M.get_followups
track_followup = M.track_followup


def reset():
    global _DB
    _DB = {"followups": [], "next_id": 1}


def seed(**kw):
    """Create one open follow-up directly (bypassing track_followup)."""
    rec = {"id": kw.get("id", 1), "item": kw.get("item", "Test thread"),
           "expected_by": kw.get("expected_by", "2026-08-25"), "note": kw.get("note", ""),
           "created": "2026-08-01", "status": kw.get("status", "open"),
           "resolved_on": None, "outcome": "", "covers": [],
           "precondition": kw.get("precondition", ""), "history": kw.get("history", [])}
    _DB["followups"].append(rec)
    _DB["next_id"] = rec["id"] + 1
    return rec


PRE = "next live hardware session"

# ---------------------------------------------------------------- 1. MUTATION
C.section("[1] Mutation logic")

reset(); seed()
out = reschedule_followup(1, reason="the vendor has not answered yet", new_due="2026-09-01")
C.check("1.1 the date change runs", "rescheduled" in out, out)
C.check("1.2 the new date is in the record", _DB["followups"][0]["expected_by"] == "2026-09-01")
C.check("1.3 the answer prints the old AND the new value", "2026-08-25" in out and "2026-09-01" in out, out)

reset(); seed()
out = reschedule_followup(1, reason="x", new_due="2026-08-25")
C.check("1.4 does not reschedule to the same date", "already" in out and "not modified" in out.lower(), out)
C.check("1.5 ... and writes no history either", _DB["followups"][0]["history"] == [])

reset(); seed()
out = reschedule_followup(1, reason="x", new_due="2026-13-45")
C.check("1.6 rejects an invalid date", out.startswith("Error"), out)
C.check("1.7 ... and did not touch the record", _DB["followups"][0]["expected_by"] == "2026-08-25")

reset(); seed(status="resolved")
out = reschedule_followup(1, reason="x", new_due="2026-09-01")
C.check("1.8 does not reschedule a closed thread", "is not open" in out, out)

reset(); seed()
out = reschedule_followup(99, reason="x", new_due="2026-09-01")
C.check("1.9 non-existent id -> error", out.startswith("Error: no follow-up"), out)

reset(); seed()
out = reschedule_followup("not-a-number", reason="x", new_due="2026-09-01")
C.check("1.10 non-numeric id -> error", out.startswith("Error"), out)

reset(); seed(item="Old title, in the future tense")
out = reschedule_followup(1, reason="the text is outdated", new_item="New title, in the past tense")
C.check("1.11 title refinement", _DB["followups"][0]["item"] == "New title, in the past tense")
C.check("1.12 the OLD title stays in the history",
        _DB["followups"][0]["history"][0]["changes"][0]["from"] == "Old title, in the future tense",
        _DB["followups"][0]["history"])

reset(); seed(note="original note")
out = reschedule_followup(1, reason="new info", note_add="a letter arrived")
n = _DB["followups"][0]["note"]
C.check("1.13 note_add APPENDS, does not overwrite", n.startswith("original note") and "a letter arrived" in n, n)
C.check("1.14 the appended line is dated", f"[{TODAY}]" in n, n)

reset(); seed()
out = reschedule_followup(1, reason="just a reason, no change at all")
C.check("1.15 without a change no history is written", _DB["followups"][0]["history"] == [], out)
C.check("1.16 ... and it says so", "you gave no change at all" in out, out)

# --------------------------------------------------- 2. DATE -> PRECONDITION
C.section("[2] Date-driven -> precondition-driven")

reset(); seed()
out = reschedule_followup(1, reason="the date only produced noise", new_due="open")
C.check("2.1 clearing the date WITHOUT a precondition is FORBIDDEN", out.startswith("Error"), out)
C.check("2.2 ... and the date really stayed", _DB["followups"][0]["expected_by"] == "2026-08-25")

reset(); seed()
out = reschedule_followup(1, reason="the date carried no information", new_due="nyitott", precondition=PRE)
C.check("2.3 with a precondition the clearing goes through", _DB["followups"][0]["expected_by"] == "", out)
C.check("2.4 the precondition is recorded", _DB["followups"][0]["precondition"] == PRE)

reset(); seed()
TOKENS = ["-", "NYITOTT", "nyitott vegu", "torol", "open", "open-ended", "clear", "none"]
for tok in TOKENS:
    _DB["followups"][0]["expected_by"] = "2026-08-25"
    reschedule_followup(1, reason="r", new_due=tok, precondition="p")
    C.check(f"2.5 the clear token {tok!r} works", _DB["followups"][0]["expected_by"] == "")

reset(); seed(expected_by="", precondition="old precondition")
out = reschedule_followup(1, reason="refinement", precondition="new precondition")
C.check("2.6 an existing precondition can be overwritten", _DB["followups"][0]["precondition"] == "new precondition")

# ------------------------------------------------------------------ 3. AUDIT
C.section("[3] Audit: reason required + append-only history")

reset(); seed()
for bad in ["", "   ", None]:
    out = reschedule_followup(1, reason=bad, new_due="2026-09-01")
    C.check(f"3.1 reason={bad!r} -> error", out.startswith("Error"), out)
C.check("3.2 ... and the date did NOT change", _DB["followups"][0]["expected_by"] == "2026-08-25")

reset(); seed()
reschedule_followup(1, reason="first slip", new_due="2026-09-01")
reschedule_followup(1, reason="second slip", new_due="2026-09-10")
h = _DB["followups"][0]["history"]
C.check("3.3 the history is append-only (2 entries)", len(h) == 2, str(len(h)))
C.check("3.4 the FIRST entry is unchanged", h[0]["reason"] == "first slip")
C.check("3.5 every entry is dated", all(e["on"] == TODAY for e in h))
C.check("3.6 from-what-to-what is recorded",
        h[1]["changes"][0]["from"] == "2026-09-01" and h[1]["changes"][0]["to"] == "2026-09-10", str(h[1]))

out = reschedule_followup(1, reason="third slip", new_due="2026-09-20")
C.check("3.7 at the 3rd reschedule it WARNS", "RESCHEDULE NO. 3" in out, out)
C.check("3.8 ... and names it a decision problem", "decision problem" in out, out)
C.check("3.9 ... and shows a way out (flag_for_owner)", "flag_for_owner" in out, out)

reset(); seed(history="not-a-list")   # a damaged old record
out = reschedule_followup(1, reason="r", new_due="2026-09-01")
C.check("3.10 a damaged history does not blow up, it is rebuilt",
        isinstance(_DB["followups"][0]["history"], list) and len(_DB["followups"][0]["history"]) == 1, out)

# ------------------------------------------------------------- 4. RENDERING
C.section("[4] Rendering: what does not show, does not exist")

reset(); seed(expected_by="", precondition=PRE)
r = get_followups()
C.check("4.1 the precondition appears", f"PRECONDITION: {PRE}" in r, r)
C.check("4.2 the tag is 'tied to a precondition'", "tied to a precondition" in r, r)

reset(); seed(expected_by="")
r = get_followups()
C.check("4.3 without a precondition it stays 'open-ended'", "open-ended" in r, r)

reset(); seed()
reschedule_followup(1, reason="the vendor was on holiday", new_due="2026-09-01")
r = get_followups()
C.check("4.4 the number of reschedules shows", "rescheduled/refined 1x" in r, r)
C.check("4.5 the REASON shows too", "the vendor was on holiday" in r, r)
C.check("4.6 the last date shows too", TODAY in r, r)

reschedule_followup(1, reason="again", new_due="2026-09-05")
reschedule_followup(1, reason="again2", new_due="2026-09-09")
r = get_followups()
C.check("4.7 at 3 postponements the LIST warns too", "3 postponements" in r, r)
C.check("4.8 ... and says it is a decision problem", "decision problem" in r, r)

reset(); seed(note="important note")
reschedule_followup(1, reason="r", new_due="2026-09-01")
r = get_followups()
C.check("4.9 the note still appears", "important note" in r, r)

reset(); seed(); r = get_followups()
C.check("4.10 without history there is NO noise on the line", "rescheduled" not in r, r)

# --------------------------------------------------- 5. REGRESSION / COMPAT
C.section("[5] Regression: old records and track_followup")

reset()
# an old record: no precondition, no history
_DB["followups"].append({"id": 1, "item": "Old record", "expected_by": "2026-08-25",
                         "note": "n", "created": "2026-08-01", "status": "open",
                         "resolved_on": None, "outcome": "", "covers": []})
_DB["next_id"] = 2
r = get_followups()
C.check("5.1 rendering an old record does not fail", "Old record" in r, r)
out = reschedule_followup(1, reason="migration", new_due="2026-09-01")
C.check("5.2 an old record can be rescheduled", _DB["followups"][0]["expected_by"] == "2026-09-01", out)
C.check("5.3 ... and gets a history", len(_DB["followups"][0]["history"]) == 1)

reset()
out = track_followup("New thread with a precondition", precondition=PRE)
C.check("5.4 track_followup accepts the precondition", "tied to a precondition" in out, out)
C.check("5.5 ... and it gets into the record", _DB["followups"][0]["precondition"] == PRE)
C.check("5.6 ... and it gets an empty history", _DB["followups"][0]["history"] == [])

reset()
out = track_followup("Date AND precondition", expected_by="2026-09-01", precondition="p")
C.check("5.7 date+precondition together is legitimate, but it warns", "⚠️" in out, out)

reset()
out = track_followup("Plain old call", expected_by="2026-09-01")
C.check("5.8 the old call form works unchanged", "expected: 2026-09-01" in out, out)

reset()
out = track_followup("Open-ended, nothing extra")
C.check("5.9 open-ended creation unchanged", "(open-ended)" in out, out)

reset(); seed()
before = copy.deepcopy(_DB["followups"][0])
reschedule_followup(1, reason="r", new_due="2026-09-01")
after = _DB["followups"][0]
C.check("5.10 the `covers` field is not touched", after["covers"] == before["covers"])
C.check("5.11 the `created` field is not touched", after["created"] == before["created"])
C.check("5.12 the `status` stays open", after["status"] == "open")

C.finish()
