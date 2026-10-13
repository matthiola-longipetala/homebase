# -*- coding: utf-8 -*-
"""Search provenance bands + snapshot filter, through a PLAIN `import server`.

The sibling test (test_search_provenance.py) loads the server by file path.
This one checks the same behaviour the way a user's own script would get it:
a regular `import server`, with MEMORIA_ROOT set in the environment BEFORE the
import and NOT patched afterwards. A module that cannot be redirected cannot
be tested -- and then a test silently works on the real memory. So the first
check here is that the import itself honours MEMORIA_ROOT.

Notes for running it:
- On Windows use `python -X utf8` (or PYTHONIOENCODING=utf-8): otherwise the
  console code page cannot print the emoji in the output, and the test looks
  like a server error although it only cannot print itself.
- The `mcp` package is not needed: a stub stands in for it.

Run:  python -X utf8 test_search_provenance_module_import.py
"""
import importlib
import os
import shutil
import sys

import _support as S

C = S.Checker("Search provenance via a plain `import server`")

tmp = S.make_memory_root("prov_import_", subdirs=("kontextus", "projektek", "naplo", "archiv",
                                                  "eszkozok", "claude_sajat", "rendszer"))
code_dir = S.prepare_server_copy()
S.redirect_env(tmp, code_dir)
S.stub_mcp()
sys.path.insert(0, str(code_dir))
sys.modules.pop("server", None)
server = importlib.import_module("server")


def w(rel, text):
    return S.write(tmp, rel, text)


C.check("isolation: `import server` honours MEMORIA_ROOT (no patching after the import)",
        os.path.realpath(server.MEMORIA_ROOT) == os.path.realpath(str(tmp)), server.MEMORIA_ROOT)
C.check("isolation: the imported module is the temp copy, not the repository file",
        os.path.realpath(server.__file__) == os.path.realpath(str(code_dir / "server.py")), server.__file__)
C.check("isolation: the audit database points into the temp folder",
        S.is_inside_temp(server.MEMORIA_AUDIT_DB), server.MEMORIA_AUDIT_DB)

sm = server.search_memory
gm = server.get_open_markers
CTX = server.CONTEXT_FILE
CTX_BAK = "kontextus/helyzet_backup_2026-09-09_pre_fix.md"

C.section("1. _is_snapshot_file: positive + NEGATIVE control")
POS = ["helyzet_backup_2026-09-09_pre_fix.md", "_bak_state_2026-08-29.md",
       "_state.md.bak_2026-09-01_pre_compaction", "server_backup_pre_hl_truncate_2026-07-31.py",
       "restarter_backup_2026-09-04_pre_cron.ps1"]
NEG = ["rendszer/weekly_memory_backup.md", "naplo/2026-09-09_agencia.md", CTX,
       "projektek/robot_price_check_2026-09-01.md", "projektek/skill_split_brief_2026-09-07.md",
       "_state.md", "eszkozok/safety_backup_howto.md"]
C.check(f"{len(POS)} real snapshots recognised", all(server._is_snapshot_file(n) for n in POS),
        [n for n in POS if not server._is_snapshot_file(n)])
C.check(f"{len(NEG)} live files are NOT snapshots (0 false positives)",
        not any(server._is_snapshot_file(n) for n in NEG), [n for n in NEG if server._is_snapshot_file(n)])
C.check("weekly_memory_backup.md (a LIVE spec, 'backup' in the name, NO date) is NOT a snapshot",
        not server._is_snapshot_file("rendszer/weekly_memory_backup.md"))

C.section("2. THE MEASURED CASE: 0 live hits, only copies -> LOUD warning")
w(CTX, "# Situation\n- 2026-10-01: the CORRECTED claim, with new text.\n")
w(CTX_BAK, "# Situation\n- the OLD stale claim keyword_XyZ.\n")
w("archiv/_bak_helyzet_2026-08-29.md", "# Situation\n- the OLD stale claim keyword_XyZ.\n")
r = sm("keyword_XyZ")
C.check("loud ⚠️ block when there are 0 live hits", "NOT A SINGLE MATCH COMES FROM THE LIVE MEMORY" in r, r)
C.check("the snapshot band appears (does not vanish)", "SNAPSHOTS" in r and "pre_fix" in r, r)
C.check("the NUMBER of archive hits is printed (no silent filter)",
        "1 more file(s)" in r and "search_archive" in r, r[:600])
C.check("the archive CONTENT does not come back (spill guard)", "_bak_helyzet_2026-08-29" not in r)

C.section("3. There are live hits -> no false alarm, and LIVE comes first")
w("projektek/live_project.md", "# P\n- this is live content: shared_word is here.\n")
w("naplo/2026-09-01_agencia.md", "# N\n- shared_word in the log.\n")
w("kontextus/something_backup_2026-08-01_pre_x.md", "# S\n- shared_word in the snapshot.\n")
r = sm("shared_word")
C.check("NO false ⚠️ warning when there is a live hit", "NOT A SINGLE MATCH" not in r)
C.check("the LIVE band stands before the log band",
        "✅ LIVE memory" in r and "📓 naplo/" in r and r.index("✅ LIVE memory") < r.index("📓 naplo/"), r)
C.check("the log band stands before the SNAPSHOT band",
        "🗄️ SNAPSHOTS" in r and r.index("📓 naplo/") < r.index("🗄️ SNAPSHOTS"), r)
C.check("all three bands are present",
        all(x in r for x in ("live_project.md", "2026-09-01_agencia.md", "something_backup_2026-08-01")))

C.section("4. THE CAP SAYS IT IS A CAP")
for i in range(server._SEARCH_LIVE_CAP + 7):
    w(f"projektek/many_{i:03d}.md", "cap_probe content\n")
r = sm("cap_probe")
C.check("at the cap it prints how many files were left out",
        "(cap:" in r and "and 7 more file(s)" in r, [l for l in r.splitlines() if "cap" in l])
C.check("the header gives the FULL count, not the cut one", f"{server._SEARCH_LIVE_CAP + 7} LIVE" in r)

C.section("5. FAIL-CLOSED: an unreadable file is NOT a silent 'no match'")
bad = w("projektek/unreadable.md", "fail_closed_probe\n")
open(bad, "wb").write(b"\xff\xfe\x00binary \xff not utf8 fail_closed_probe")
r = sm("fail_closed_probe")
C.check("the unreadable file appears in a ⛔ FAIL-CLOSED line", "FAIL-CLOSED" in r, r[:300])

C.section("6. No match -> unchanged behaviour (regression)")
# The deliberately broken file of case 5 would put a FAIL-CLOSED line into EVERY
# answer, so this check could never be green structurally. Removing it restores
# what case 6 really claims (no match -> no noise).
os.remove(bad)
C.check("the 'no match' answer stays", sm("completely_unknown_phrase_qqq").startswith("No match"))

C.section("7. get_open_markers: a snapshot's markers do NOT duplicate, but the skip is LOUD")
shutil.rmtree(tmp)
os.makedirs(tmp)
for sub in ("kontextus", "projektek", "naplo", "archiv", "eszkozok", "claude_sajat"):
    os.makedirs(os.path.join(tmp, sub), exist_ok=True)
w("_state.md", "# State\n## Where I am\nnothing\n")
w(CTX, "# S\n- ⚑ OPEN: a real marker_probe todo stands here.\n")
w(CTX_BAK, "# S\n- ⚑ OPEN: a real marker_probe todo stands here.\n")
r = gm()
C.check("the real marker appears", os.path.basename(CTX) in r, r)
C.check("the snapshot's marker does NOT appear in the list",
        os.path.basename(CTX_BAK) not in r.split("Skipped as dated SNAPSHOT")[0])
C.check("the skip is LOUD (the footer names it)", "Skipped as dated SNAPSHOT copies" in r, r[-500:])
C.check("the hygiene signal is stated ('it belongs in archiv/')", "it belongs in `archiv/`" in r)

C.finish()
