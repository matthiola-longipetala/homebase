# -*- coding: utf-8 -*-
"""The 🟠 MISSING SCHEDULED LOG bucket of get_unlogged_changes.

Background: a scheduled autonomous session once did real work (a new file, a
new follow-up, rescheduled threads) and then stopped WITHOUT writing its log
and WITHOUT updating _state.md -- exactly the failure class this radar was
built for. The radar still reported ✅ **0 SILENT DAYS**.

THE CAUSE, IN ONE SENTENCE: the unit of the coverage check was the DAY, while
the thing sought is a SESSION. That day several interactive sessions had
logged, so the day looked covered, and the scheduled session's file traces
fell into the SOFT "[?] not mentioned" bucket, which the protocol explicitly
says not to turn into mandatory work. ➜ A silent scheduled session is
invisible exactly on the days when someone ALSO worked interactively -- on
most days. The checker reported its own blindness with a ✅.

THE FIX is not a heuristic: the daily scheduled tasks ALWAYS write a log with
a fixed name pattern (`*_agencia.md` = autonomous session, `*_reggeli.md` =
morning summary; the server parses these names, so they stay). If a CLOSED
day has no such file, either (a) the task did not run (lastRunAt shows it),
or (b) it stopped SILENTLY (nothing caught that before).

How it is tested: the REAL server module on a temp memory root; only the two
disk collectors and the archive index are fixtures. The fail-closed helpers
and `_unlogged_carry_index` are the REAL code (a stubbed green-sentence helper
would fake exactly the sentence being measured). All dates are relative to
today (a fixed date in a clock-sensitive assertion rots).

Run:  python -X utf8 test_unlogged_scheduled_logs.py
"""
import re
from datetime import datetime, timedelta

import _support as S

C = S.Checker("🟠 MISSING SCHEDULED LOG (get_unlogged_changes)")
M = S.load_server(S.make_memory_root("sched_"))

_FIXTURE = {"changed": [], "naplo": {}, "archiv": {}}
M._unlogged_collect = lambda cutoff_ts, inc=None: list(_FIXTURE["changed"])
M._unlogged_naplo_index = lambda inc=None: dict(_FIXTURE["naplo"])
M._archived_naplo_names = lambda: dict(_FIXTURE["archiv"])

TODAY = datetime.now().date()


def at(day_offset, hour, minute=0):
    d = TODAY + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute).timestamp()


def day_iso(off):
    return (TODAY + timedelta(days=off)).isoformat()


def ent(off, suffix, text="something", hour=22, routine=False):
    """One log-index entry: (file name, routine?, text, mtime)."""
    return (f"{day_iso(off)}_{suffix}.md", routine, text.lower(), at(off, hour))


def naplo(*specs):
    """specs: (day_offset, [suffix, ...]) pairs -> log-index dict."""
    out = {}
    for off, suffixes in specs:
        out[day_iso(off)] = [
            ent(off, s, routine=bool(re.search(r"reggeli|vasarnapi", s)))
            for s in suffixes]
    return out


def run(changed=None, nap=None, archiv=None, **kw):
    _FIXTURE["changed"] = changed or []
    _FIXTURE["naplo"] = nap or {}
    _FIXTURE["archiv"] = archiv or {}
    return M.get_unlogged_changes(**kw)


SCHED_H = "## 🟠 MISSING SCHEDULED LOG"
SILENT_H = "## 🔴 SILENT DAY"
GREEN_ALL = "✅ Every change has log coverage."

C.section("1. The real case: 4 interactive logs, BUT no autonomous-session log")
out = run(nap=naplo((-1, ["reggeli", "session_1", "session_2", "session_3", "session_4"]),
                    (-2, ["reggeli", "agencia"])))
C.check("the 🟠 section appears", SCHED_H in out, out)
C.check("the incomplete day is listed", day_iso(-1) in out)
C.check("it names WHICH log is missing", "`*_agencia.md`" in out)
C.check("it names the task too (where to look for lastRunAt)", "(task: autonomous session)" in out, out)
# ⚠️ A LINE-EXACT anchor, not "is it in the section". A first attempt searched
# `out.split(SCHED_H)[1]` and FAILED FALSELY: the -2 day's date also stands in the
# closing "skipped days" note. A badly aimed anchor is as much noise as a bad filter.
C.check("the complete day (morning + autonomous) gets NO accusation line",
        f"**{day_iso(-2)}** -- missing" not in out, out)

C.section("2. ⚠️ THE POINT: the file-based ✅ does NOT silence the 🟠")
out = run(changed=[], nap=naplo((-1, ["session_1"]), (-2, ["reggeli", "agencia"])))
C.check("it fires with zero file changes TOO (independent signal)", SCHED_H in out)
C.check("it does NOT write the all-✅ while session coverage is incomplete", GREEN_ALL not in out)

C.section("2b. And the other way round: it appears next to a file-based 🔴 too")
# Day -1 has a log file (so it CAN be judged), just not the autonomous one; and the
# file change has no SUBSTANTIVE entry (morning routine only) -> 🔴 AND 🟠 together.
out = run(changed=[("kontextus/x.md", "x.md", at(-1, 4))],
          nap=naplo((-1, ["reggeli"]), (-2, ["reggeli", "agencia"])))
C.check("both buckets visible at once", SILENT_H in out and SCHED_H in out, out)

C.section("3. Every day complete -> no 🟠, and the all-✅ branch remains")
full = {}
for k in range(1, 8):
    full[day_iso(-k)] = [ent(-k, "reggeli", routine=True), ent(-k, "agencia")]
out = run(nap=full)
C.check("no false positive", SCHED_H not in out)
C.check("the old ✅ sentence is VERBATIM still there (the anchor of test_unlogged_today_bucket, case 5)",
        GREEN_ALL in out, out)
C.check("the new claim comes as a separate sentence", "every daily scheduled session logged" in out, out)

C.section("4. A missing autonomous log TODAY is no accusation (the log is written at the end)")
out = run(nap=dict(full, **{day_iso(0): [ent(0, "reggeli", routine=True)]}))
C.check("today is left out of the check", SCHED_H not in out)

C.section("5a. An ARCHIVED log counts as coverage -- it is not missing")
# The day's live naplo/ has only one interactive entry; the morning and autonomous
# logs were already moved to archiv/raw/naplo/. This is the real corpus shape; a
# first, heuristic version (min(naplo)) failed on almost all hits here.
out = run(nap=naplo((-1, ["session_1"])),
          archiv={day_iso(-1): [f"{day_iso(-1)}_reggeli.md", f"{day_iso(-1)}_agencia.md"]})
C.check("the archived autonomous + morning logs count as coverage", SCHED_H not in out, out)

C.section("5b. ...but if it is missing from the archive too, it IS reported")
out = run(nap=naplo((-1, ["session_1"])),
          archiv={day_iso(-1): [f"{day_iso(-1)}_reggeli.md"]})
C.check("the archive is no blanket excuse: the missing autonomous log still shows",
        SCHED_H in out and "`*_agencia.md`" in out, out)

C.section("5c. A day with NO file anywhere cannot be judged -> skipped, but PRINTED")
out = run(nap=naplo((-1, ["reggeli", "agencia"])), days=7)
C.check("days -2..-7 are NOT accused (nothing to judge there)", SCHED_H not in out)
C.check("but it PRINTS that it skipped and WHY (the filter must not be silent)",
        "were left out of the scheduled-log" in out and "there is NO file at all" in out, out)
C.check("it says what the next step is if such a day is suspicious", "lastRunAt" in out)

C.section("6. Empty log index -> no 🟠 (nothing to measure against)")
out = run(nap={})
C.check("no accusation invented for an empty corpus", SCHED_H not in out)

C.section("7. The 🟠 bucket SAYS what to do")
out = run(nap=naplo((-1, ["session_1"]), (-2, ["reggeli", "agencia"])))
C.check("first step: is the session still alive", "list_sessions" in out)
C.check("if alive: only the owner can stop it (flag_for_owner)", "flag_for_owner" in out, out)
C.check("if dead: a reconstructed log, with a frontmatter marker", "rekonstrualt" in out, out)
C.check("it separates 'did not run' from 'stopped silently'", "lastRunAt" in out)

C.section("8. A missing morning summary is a signal too (not only the autonomous log)")
out = run(nap=naplo((-1, ["agencia", "session_1"]), (-2, ["reggeli", "agencia"])))
C.check("it reports the missing morning summary", SCHED_H in out and "`*_reggeli.md`" in out, out)

C.section("9. Mutation probe: with the check switched off, case 1 FAILS")
_saved = M._missing_scheduled_logs
M._missing_scheduled_logs = lambda naplo_, days_, today_, arch_=None: ([], (0, None))
out_mut = run(nap=naplo((-1, ["session_1"]), (-2, ["reggeli", "agencia"])))
M._missing_scheduled_logs = _saved
C.check("switching the check off REALLY removes the bucket -> the test can fail", SCHED_H not in out_mut)

C.section("10. Reconstructing the old behaviour: was ✅ the right answer? NO")
M._missing_scheduled_logs = lambda naplo_, days_, today_, arch_=None: ([], (0, None))
out_old = run(changed=[("kontextus/helyzet.md", "helyzet.md", at(-1, 4, 7))],
              nap=naplo((-1, ["reggeli", "session_1", "session_2"])))
M._missing_scheduled_logs = _saved
C.check("the old behaviour REALLY wrote 0 SILENT DAYS for this input (bug reproduced)",
        "✅ **0 SILENT DAYS**" in out_old, out_old)
out_new = run(changed=[("kontextus/helyzet.md", "helyzet.md", at(-1, 4, 7))],
              nap=naplo((-1, ["reggeli", "session_1", "session_2"])))
C.check("the current code fires for the SAME input", SCHED_H in out_new)
C.check("and NEXT TO the remaining '0 SILENT DAYS' line it says to look at the 🟠",
        "BUT see the 🟠 MISSING SCHEDULED LOG section" in out_new, out_new)

C.finish()
