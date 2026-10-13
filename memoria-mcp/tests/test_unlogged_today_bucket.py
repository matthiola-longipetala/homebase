# -*- coding: utf-8 -*-
"""The "⏳ TODAY" bucket of get_unlogged_changes.

Background: the 🔴 SILENT DAY bucket gave a STRUCTURAL false positive for the
session that was running at that very moment. The log is written at the END
of a session, while the radar is called in the MIDDLE of it -- so every file
the session had written so far looked like "no log for this".

The measurement that decided it: on several days the radar said "0 silent
days" while the same session had written predictions (and predikciok.json IS
watched by the radar). The only difference was the CALL ORDER: when `predict`
ran after the radar, the file's mtime still fell on the previous, already
covered day. An instrument whose output depends on its own call order
measures nothing.

THE FIX IS NOT HIDING: today's items are PRINTED, in their own bucket, with
the warning that (a) a file you did not touch today is the trace of ANOTHER
session -> list_sessions, and (b) without a log they come back tomorrow as 🔴.
The filter SAYS it filtered.

How it is tested: the REAL server module on a temp memory root; only the two
disk collectors and the archive index are replaced by fixtures, so the subject
is the BUCKET LOGIC and the RENDERING, not the file-system walk.

Run:  python -X utf8 test_unlogged_today_bucket.py
"""
from datetime import datetime, timedelta

import _support as S

C = S.Checker("⏳ TODAY bucket (get_unlogged_changes)")
M = S.load_server(S.make_memory_root("today_"))

_FIXTURE = {"changed": [], "naplo": {}, "archiv": {}}
M._unlogged_collect = lambda cutoff_ts, inc=None: list(_FIXTURE["changed"])
M._unlogged_naplo_index = lambda inc=None: dict(_FIXTURE["naplo"])
M._archived_naplo_names = lambda: dict(_FIXTURE["archiv"])

TODAY = datetime.now().date()


def at(day_offset, hour, minute=0):
    """Timestamp on day `day_offset` counted from today, at the given hour."""
    d = TODAY + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute).timestamp()


def naplo_entry(day_offset, name_suffix, text, hour=22, routine=False):
    d = TODAY + timedelta(days=day_offset)
    name = f"{d.isoformat()}_{name_suffix}.md"
    return d.isoformat(), (name, routine, text.lower(), at(day_offset, hour))


def run(changed, naplo=None, **kw):
    _FIXTURE["changed"] = changed
    _FIXTURE["naplo"] = naplo or {}
    return M.get_unlogged_changes(**kw)


TODAY_H = "## ⏳ TODAY"
SILENT_H = "## 🔴 SILENT DAY"
SOFT_H = "## [?] There is a substantive log"
PRED = ("kontextus/predikciok.json", "predikciok.json")

C.section("1. Change TODAY, no log at all -> ⏳ TODAY, NOT 🔴")
out = run([PRED + (at(0, 4, 4),)])
C.check("the ⏳ TODAY section appears", TODAY_H in out, out)
C.check("the 🔴 SILENT DAY section does NOT appear", SILENT_H not in out)
C.check("the file is listed item by item (not hidden)", "predikciok.json" in out)
C.check("the time is printed too", "04:04" in out)

C.section("2. Change YESTERDAY, no log at all -> 🔴 (regression)")
out = run([("kontextus/something.md", "something.md", at(-1, 4, 4))])
C.check("the 🔴 SILENT DAY section appears", SILENT_H in out)
C.check("the ⏳ TODAY section does NOT appear", TODAY_H not in out)
C.check("the 'no log at all' marker is still there", "_(no log at all)_" in out)

C.section("3. Mixed: today + yesterday -> BOTH buckets, and no false ✅")
out = run([PRED + (at(0, 4, 4),),
           ("kontextus/something.md", "something.md", at(-1, 4, 4))])
C.check("both sections are there", TODAY_H in out and SILENT_H in out, out)
C.check("it does NOT claim 0 silent days (there is one)", "✅ **0 SILENT DAYS**" not in out)
C.check("🔴 comes BEFORE ⏳ TODAY (the stronger signal first)",
        SILENT_H in out and TODAY_H in out and out.index(SILENT_H) < out.index(TODAY_H), out)

C.section("4. Only today -> it states that the CLOSED days have 0 silent days")
out = run([PRED + (at(0, 4, 4),)])
C.check("the cleanliness of the closed days is STATED (not implied)",
        "✅ **0 SILENT DAYS** on the closed days." in out, out)

C.section("5. No change -> the old all-✅ branch is unchanged")
out = run([])
C.check("the all-✅ branch is still there", "✅ Every change has log coverage." in out, out)
C.check("neither bucket appears", TODAY_H not in out and SILENT_H not in out)

C.section("6. The ⏳ TODAY bucket is NOT silent: it says what to do with it")
out = run([PRED + (at(0, 4, 4),)])
C.check("points to the other-session case (list_sessions)", "list_sessions" in out)
C.check("states that without a log it becomes 🔴 TOMORROW", "TOMORROW as 🔴" in out, out)
C.check("states that this postpones, it does not acquit", "postpones, it does not acquit" in out, out)
C.check("names the call-ORDER dependence as the reason", "ORDER" in out, out)

C.section("7. Regression: today's file + today's substantive log, MENTIONED -> covered")
day, ent = naplo_entry(0, "agencia", "today I wrote the predikciok.json file", hour=5)
out = run([PRED + (at(0, 4, 4),)], {day: [ent]})
C.check("in none of the suspicion buckets",
        TODAY_H not in out and SILENT_H not in out and SOFT_H not in out, out)
C.check("counted as covered", "1 covered" in out, out)

C.section("8. Regression: today's file + today's substantive log, NOT mentioned -> [?]")
day, ent = naplo_entry(0, "agencia", "today I wrote about something else entirely", hour=5)
out = run([PRED + (at(0, 4, 4),)], {day: [ent]})
C.check("goes into the soft [?] bucket", SOFT_H in out, out)
C.check("NOT into the ⏳ TODAY bucket (there is a substantive log, it just does not mention it)",
        TODAY_H not in out)

C.section("9. Causal filter: a log written BEFORE the file change is no coverage")
# log at 01:00, file at 04:04 -> the log is 3 hours earlier (>2 h slack) -> no coverage
day, ent = naplo_entry(0, "agencia", "predikciok.json mentioned", hour=1)
out = run([PRED + (at(0, 4, 4),)], {day: [ent]})
C.check("goes into today's bucket (the earlier log is no coverage)", TODAY_H in out, out)
C.check("NOT counted as covered", "1 covered" not in out)

C.section("10. A routine (morning summary) log is not substantive -> today's file in ⏳ TODAY")
day, ent = naplo_entry(0, "reggeli", "morning summary", hour=5, routine=True)
out = run([PRED + (at(0, 4, 4),)], {day: [ent]})
C.check("the routine entry does NOT acquit", TODAY_H in out, out)
C.check("the bucket marks the state", "_(routine entry only" in out or "_(today)_" in out, out)

C.section("11. Regression: yesterday's file + yesterday's ROUTINE log -> 🔴 with the routine marker")
day, ent = naplo_entry(-1, "reggeli", "morning", hour=5, routine=True)
out = run([("kontextus/something.md", "something.md", at(-1, 4, 4))], {day: [ent]})
C.check("🔴 stays", SILENT_H in out, out)
C.check("the routine marker is still there", "_(routine entry only" in out, out)

C.section("12. Regression: grace_days=1 still works (yesterday's file, today's log)")
day, ent = naplo_entry(0, "agencia", "I wrote about the something.md file", hour=5)
out = run([("kontextus/something.md", "something.md", at(-1, 23, 50))], {day: [ent]},
          grace_days=1)
C.check("work spanning midnight stays covered (grace)",
        SILENT_H not in out and TODAY_H not in out, out)

C.finish()
