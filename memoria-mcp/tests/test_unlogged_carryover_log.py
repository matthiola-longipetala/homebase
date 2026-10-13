# -*- coding: utf-8 -*-
"""CARRY-OVER log coverage in get_unlogged_changes.

Background: the radar once reported a FALSE 🔴 SILENT DAY. The day's work was
fully logged -- just not in a file named after that day, but in an older
`naplo/<start-date>_session_1.md`, because a long-running main session kept
writing into the log file named with its START date for days. Measured on a
real log folder, the file-name date differed from the mtime date for many
files, and the gap grows every day the session lives -- i.e. it would become
a daily, permanent false positive: exactly the "alarm everybody learns to
ignore" anti-pattern.

THE FIX IS NOT HIDING: such a day is not green, it goes into a SEPARATE,
visible 🟡 band, with the NAME and the opening date of the covering log file,
plus the warning that the same picture arises if an OLD log was corrected
today.

How it is tested: the REAL server module is loaded (on a temp memory root);
only the two disk collectors (`_unlogged_collect`, `_unlogged_naplo_index`)
and the archive index are replaced by fixtures, so the subject is the logic,
not the disk. `_unlogged_carry_index` is the REAL code.

File-name suffixes `_agencia` (daily autonomous session) and `_reggeli`
(morning summary) are names the server parses; they stay as they are.

Run:  python -X utf8 test_unlogged_carryover_log.py
      python -X utf8 test_unlogged_carryover_log.py --negative
          (negative control against a pre-patch `server_backup_pre_atnyulo_*.py`, if present)
"""
import sys
from datetime import datetime, timedelta

import _support as S

NEG_REQUESTED = "--negative" in sys.argv or "--negativ" in sys.argv
server_path = S.find_backup("server_backup_pre_atnyulo_*.py") if NEG_REQUESTED else None
NEG = server_path is not None

C = S.Checker("Carry-over log coverage (get_unlogged_changes)", negative=NEG)
if NEG_REQUESTED and not NEG:
    C.skip_no_backup()
M = S.load_server(S.make_memory_root("carry_"), server_path=server_path)
print(f"source: {(server_path or S.SERVER_PATH).name}")

_FIXTURE = {"changed": [], "naplo": {}, "archiv": {}}
M._unlogged_collect = lambda cutoff_ts, inc=None: list(_FIXTURE["changed"])
M._unlogged_naplo_index = lambda inc=None: dict(_FIXTURE["naplo"])
M._archived_naplo_names = lambda: dict(_FIXTURE["archiv"])
HAS_CARRY = hasattr(M, "_unlogged_carry_index")

TODAY = datetime.now().date()


def at(day_offset, hour, minute=0):
    d = TODAY + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute).timestamp()


def naplo_file(name_day_offset, suffix, text, mt_day_offset, hour=22, routine=False):
    """(key, entry) -- the KEY is the day in the FILE NAME, but the mtime is on another day."""
    d = TODAY + timedelta(days=name_day_offset)
    name = f"{d.isoformat()}_{suffix}.md"
    return d.isoformat(), (name, routine, text.lower(), at(mt_day_offset, hour))


def run(changed, naplo=None, **kw):
    _FIXTURE["changed"] = changed
    _FIXTURE["naplo"] = naplo or {}
    return M.get_unlogged_changes(**kw)


SILENT_H = "## 🔴 SILENT DAY"
TODAY_H = "## ⏳ TODAY"
CARRY_H = "## 🟡 CARRY-OVER LOG COVERAGE"
GREEN_ALL = "✅ Every change has log coverage."
GREEN_CLOSED = "✅ **0 SILENT DAYS** on the closed days."

# ---------------------------------------------------------------- 1.
C.section("1. `_unlogged_carry_index` -- only a LATER mtime counts as carry-over")
if HAS_CARRY:
    ci = M._unlogged_carry_index
    k3, e3 = naplo_file(-3, "session_1", "long session", mt_day_offset=-1)
    k1, e1 = naplo_file(-1, "agencia", "same day", mt_day_offset=-1)
    idx = ci({k3: [e3], k1: [e1]})
    d1 = (TODAY + timedelta(days=-1)).isoformat()
    C.check("the carry-over file is filed under its MTIME day", d1 in idx)
    C.check("the same-day (not carry-over) file is NOT in it", len(idx.get(d1, [])) == 1)
    C.check("the NAME of the covering file is kept", idx[d1][0][0] == e3[0])
    C.check("the FILE-NAME date (opening day) is kept too", idx[d1][0][4] == k3)
else:
    C.skip("carry index unit checks", "the pre-patch version has no _unlogged_carry_index")

# ---------------------------------------------------------------- 2.
C.section("2. MAIN CASE: change yesterday, covered only by a CARRY-OVER log -> 🟡, NOT 🔴")
k, e = naplo_file(-3, "session_1", "the multi-day session's log", mt_day_offset=-1, hour=22)
out = run([("projektek/something.md", "something.md", at(-1, 20))], {k: [e]})
C.check("the 🟡 CARRY-OVER LOG COVERAGE section appears", CARRY_H in out, out)
C.check("it does NOT become a 🔴 SILENT DAY", SILENT_H not in out)
C.check("the NAME of the covering log file is printed", e[0] in out)
C.check("the changed file is listed item by item", "projektek/something.md" in out)
C.check("the band is NOT silent: it says it has to be checked", "CHECK:" in out)

# ---------------------------------------------------------------- 3.
C.section("3. CAUSAL FILTER: the carry-over log was written BEFORE the change -> 🔴 stays")
k, e = naplo_file(-3, "session_1", "early entry", mt_day_offset=-1, hour=6)
out = run([("projektek/something.md", "something.md", at(-1, 20))], {k: [e]})
C.check("🔴 SILENT DAY stays (a log cannot cover retroactively)", SILENT_H in out)
C.check("it does NOT go into the 🟡 band", CARRY_H not in out)

# ---------------------------------------------------------------- 4.
C.section("4. A ROUTINE carry-over log (morning summary) is no coverage -> 🔴")
k, e = naplo_file(-3, "reggeli", "morning summary", mt_day_offset=-1, routine=True)
out = run([("projektek/something.md", "something.md", at(-1, 20))], {k: [e]})
C.check("🔴 stays (a routine entry never counted)", SILENT_H in out)
C.check("it does NOT go into the 🟡 band", CARRY_H not in out)

# ---------------------------------------------------------------- 5.
C.section("5. TODAY stays ⏳ TODAY, not 🟡 (the TODAY bucket is intact)")
k, e = naplo_file(-2, "session_1", "multi-day", mt_day_offset=0, hour=3)
out = run([("projektek/something.md", "something.md", at(0, 4))], {k: [e]})
C.check("today's change stays in the ⏳ TODAY bucket", TODAY_H in out)
C.check("today does NOT go into the 🟡 band", CARRY_H not in out)

# ---------------------------------------------------------------- 6.
C.section("6. REGRESSION: WITHOUT a carry-over log everything is as before")
out = run([("projektek/something.md", "something.md", at(-1, 20))])
C.check("🔴 SILENT DAY works unchanged", SILENT_H in out)
C.check("the 'no log at all' marker is still there", "_(no log at all)_" in out)
# ⚠️ the morning summary is needed too, otherwise the 🟠 missing-scheduled-log
# bucket fires and the early green return is never reached -- the test would
# fail on its own fixture, not on the code.
k, e = naplo_file(-1, "agencia", "something.md done", mt_day_offset=-1)
_, e_r = naplo_file(-1, "reggeli", "morning", mt_day_offset=-1, routine=True)
out = run([("projektek/something.md", "something.md", at(-1, 20))], {k: [e, e_r]})
C.check("the green sentence is VERBATIM unchanged (test anchor)", GREEN_ALL in out, out)

# ---------------------------------------------------------------- 7.
C.section("7. The 🟡 band is NOT swallowed by the early green return")
k, e = naplo_file(-3, "session_1", "multi-day", mt_day_offset=-1, hour=22)
out = run([("projektek/something.md", "something.md", at(-1, 20))], {k: [e]})
C.check("NO early all-green return when there is a 🟡 day", GREEN_ALL not in out)
C.check("the '0 SILENT DAYS' sentence appears (there really is no silent day)", GREEN_CLOSED in out)
C.check("...and it points at the 🟡 band (the green does not stand alone)",
        "see the CARRY-OVER LOG COVERAGE section" in out, out)

# ---------------------------------------------------------------- 8.
C.section("8. Several changed files under ONE carry-over log -> one day, two lines")
k, e = naplo_file(-4, "session_1", "multi-day", mt_day_offset=-1, hour=23)
out = run([("projektek/a.md", "a.md", at(-1, 19)),
           ("projektek/b.md", "b.md", at(-1, 20))], {k: [e]})
C.check("a single 🟡 section", out.count(CARRY_H) == 1)
C.check("both files listed", "projektek/a.md" in out and "projektek/b.md" in out)
C.check("the OPENING date of the covering log is printed too", f"(opened: {k})" in out, out)

C.finish()
