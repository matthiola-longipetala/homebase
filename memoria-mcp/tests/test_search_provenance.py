# -*- coding: utf-8 -*-
"""PROVENANCE in search (search_memory) + the snapshot filter (get_open_markers, archive_generic).

Background (measured on a real memory, not a hypothesis): a search for a
deadline phrase returned 4 hits, and ALL FOUR were stale snapshots (3 from
`archiv/` + 1 backup copy sitting in the LIVE `kontextus/`) -- ZERO from the
live file. The cause: the claim had been CORRECTED (its text changed), while
the copies kept the OLD text, which matched the old query better. ➜ The better
the correction, the more the search slides towards the snapshots -- and
nothing says so. A second measured problem: a broad query would have returned
hundreds of file blocks (most of the corpus is archive), which is a sure
SPILL: the tool "runs", but the content never reaches the model.

THE FIX IS NOT HIDING: bands by provenance (LIVE / log / snapshot), a loud
warning when there are 0 live hits, the NUMBER of archive hits always printed
(pointing to `search_archive`), and the cap SAYS it is a cap.

NO STUBBING: the REAL server module runs on a REAL temporary memory root --
the disk sweep itself is the subject. (`archiv/pillanatkep/` = the server's
snapshot archive folder.)

Run:  python -X utf8 test_search_provenance.py
      python -X utf8 test_search_provenance.py --negative
          (negative control against a pre-patch `server_backup_pre_provenancia_*.py`, if present)
"""
import os
import shutil
import sys

import _support as S

NEG_REQUESTED = "--negative" in sys.argv or "--negativ" in sys.argv
server_path = S.find_backup("server_backup_pre_provenancia_*.py") if NEG_REQUESTED else None
NEG = server_path is not None

C = S.Checker("Search provenance + snapshot filter", negative=NEG)
if NEG_REQUESTED and not NEG:
    C.skip_no_backup()

TMP = S.make_memory_root("prov_", subdirs=())
M = S.load_server(TMP, server_path=server_path)
print(f"source: {(server_path or S.SERVER_PATH).name}")
CTX = M.CONTEXT_FILE                                   # e.g. kontextus/helyzet.md
CTX_BAK = "kontextus/helyzet_backup_2026-09-09_pre_fix.md"


def reset():
    shutil.rmtree(TMP, ignore_errors=True)
    for sub in ("", "kontextus", "projektek", "naplo", "archiv", "eszkozok",
                "claude_sajat", "rendszer"):
        os.makedirs(os.path.join(TMP, sub), exist_ok=True)


def w(rel, text):
    return S.write(TMP, rel, text)


C.check("isolation: the test does NOT work on a real memory",
        os.path.realpath(M.MEMORIA_ROOT) == os.path.realpath(str(TMP)) and S.is_inside_temp(M.MEMORIA_ROOT),
        M.MEMORIA_ROOT)

C.section("1. _is_snapshot_file: positive AND negative control")
if not hasattr(M, "_is_snapshot_file"):
    C.check("the pre-patch code has no _is_snapshot_file", False)
else:
    isnap = M._is_snapshot_file
    POS = ["helyzet_backup_2026-09-09_pre_fix.md", "_bak_state_2026-08-29.md",
           "_state.md.bak_2026-09-01_pre_compaction",
           "server_backup_pre_hl_truncate_2026-07-31.py",
           "restarter_backup_2026-09-04_pre_cron.ps1",
           "kontextus/mintazatok.md.bak_2026-08-31_pre_section_archiving"]
    NEGF = ["rendszer/weekly_memory_backup.md", "naplo/2026-09-09_agencia.md",
            CTX, "_state.md", "deeds.md",
            "projektek/robot_price_check_2026-09-01.md",
            "projektek/skill_split_brief_2026-09-07.md",
            "projektek/restart_verification_plan_2026-08-21.md",
            "eszkozok/safety_backup_howto.md", "kontextus/mintazatok.md"]
    C.check(f"{len(POS)} real snapshots recognised", all(isnap(n) for n in POS),
            [n for n in POS if not isnap(n)])
    C.check(f"{len(NEGF)} live files are NOT snapshots (0 false positives)",
            not any(isnap(n) for n in NEGF), [n for n in NEGF if isnap(n)])
    C.check("a LIVE spec with the word 'backup' but NO date -> not a snapshot (the point of the double condition)",
            not isnap("rendszer/weekly_memory_backup.md"))

C.section("2. THE MEASURED CASE: 0 live hits, only copies")
reset()
w(CTX, "# S\n- 2026-10-01: the CORRECTED claim, with DIFFERENT text.\n")
w(CTX_BAK, "# S\n- the OLD claim: keywordXyZ.\n")
w("archiv/_bak_helyzet_2026-08-29.md", "# S\n- the OLD claim: keywordXyZ.\n")
w("archiv/helyzet_backup_2026-09-07_pre_x.md", "# S\n- the OLD claim: keywordXyZ.\n")
r = M.search_memory("keywordXyZ")
C.check("LOUD warning when there are 0 live hits",
        "NOT A SINGLE MATCH COMES FROM THE LIVE MEMORY" in r, r[:400])
C.check("the snapshot band IS VISIBLE (a label, not a removal)", "SNAPSHOT" in r and "pre_fix" in r, r)
C.check("the NUMBER of archive hits is printed + a pointer to search_archive (no silent filter)",
        "2 more file(s)" in r and "search_archive" in r, r)
C.check("the archive CONTENT does not come back (spill guard)",
        "_bak_helyzet_2026-08-29" not in r and "helyzet_backup_2026-09-07" not in r, r)

C.section("3. There are live hits -> no false alarm, and LIVE comes first")
reset()
w("projektek/live_project.md", "# P\n- live content: sharedWord.\n")
w("naplo/2026-09-01_agencia.md", "# N\n- sharedWord in the log.\n")
w("kontextus/something_backup_2026-08-01_pre_x.md", "# S\n- sharedWord in the snapshot.\n")
r = M.search_memory("sharedWord")
C.check("NO false warning when there is a live hit", "NOT A SINGLE MATCH" not in r, r)
ok_order = ("LIVE memory" in r and "naplo/ (historical" in r and "SNAPSHOTS" in r
            and r.index("LIVE memory") < r.index("naplo/ (historical") < r.index("SNAPSHOTS"))
C.check("order: LIVE -> log -> snapshot", ok_order, r[:500])
C.check("the file is there in all three bands (nothing is lost)",
        all(x in r for x in ("live_project.md", "2026-09-01_agencia.md", "something_backup_2026-08-01")), r)

C.section("4. THE CAP SAYS IT IS A CAP")
reset()
cap = getattr(M, "_SEARCH_LIVE_CAP", 40)
for i in range(cap + 7):
    w("projektek/many_%03d.md" % i, "capProbe\n")
r = M.search_memory("capProbe")
C.check("it prints how many files were left out + the cap value",
        f"(cap: {cap})" in r and "and 7 more file(s)" in r,
        "\n".join(l for l in r.splitlines() if "cap" in l.lower()))
C.check("the header gives the FULL count, not the cut one", f"{cap + 7} LIVE" in r, r.splitlines()[0])

C.section("5. FAIL-CLOSED: an unreadable file is NOT a silent 'no match'")
reset()
bad = w("projektek/broken.md", "x\n")
open(bad, "wb").write(b"\xff\xfe\x00 binary failClosedProbe \xff")
w("projektek/good.md", "failClosedProbe is here\n")
r = M.search_memory("failClosedProbe")
C.check("the unreadable file appears in the FAIL-CLOSED line", "FAIL-CLOSED" in r and "broken.md" in r, r[-400:])

C.section("6. Regression: unchanged behaviour at the edges")
reset()
C.check("the 'No match' answer stays", M.search_memory("completelyUnknownQqq").startswith("No match"))
w("projektek/nameMatchProbe_file.md", "nothing inside\n")
C.check("the file-name match is still marked", "file name match" in M.search_memory("nameMatchProbe"))
w("kontextus/many_lines.md", "\n".join("lineProbe %d" % i for i in range(12)))
r = M.search_memory("lineProbe")
C.check("max 5 excerpt lines per file (unchanged)", r.count("  L") == 5, str(r.count("  L")))

C.section("7. get_open_markers: a snapshot's markers do not duplicate, but the skip is LOUD")
reset()
w("_state.md", "# State\n## Where I am\nnothing special\n")
w(CTX, "# S\n- OPEN: a real markerProbe todo stands here.\n")
w(CTX_BAK, "# S\n- OPEN: a real markerProbe todo stands here.\n")
r = M.get_open_markers()
head = r.split("Skipped as dated SNAPSHOT")[0]
C.check("the real marker appears", os.path.basename(CTX) in r, r)
C.check("the snapshot's marker does NOT duplicate in the list", os.path.basename(CTX_BAK) not in head, head[-400:])
C.check("the skip is LOUD (the footer names it)", "Skipped as dated SNAPSHOT copies" in r, r[-500:])
C.check("the footer states the hygiene conclusion too", "it belongs in `archiv/`" in r, r[-500:])

C.section("8. archive_generic: a snapshot from anywhere, a LIVE file NEVER")
reset()
if not hasattr(M, "archive_generic"):
    C.check("archive_generic exists", False)
else:
    w(CTX_BAK, "old copy\n")
    w("_state.md.bak_2026-09-01_pre_compaction", "old state copy\n")
    w(CTX, "# LIVE, LOAD-BEARING file\n")
    w("_state.md", "# LIVE state\n")
    w("eszkozok/old_tool.md", "outdated tool note\n")

    # (a) a snapshot from kontextus/ -> GOES, into the snapshot bucket
    try:
        r = M.archive_generic(CTX_BAK, "dated copy, it belongs in the archive", digest_entry=False)
        moved = not os.path.exists(os.path.join(TMP, CTX_BAK))
        C.check("a snapshot from kontextus/ CAN be archived (the source is gone)", moved, str(r))
        C.check("the target is the separate `archiv/pillanatkep/...` bucket", "archiv/pillanatkep/" in r, str(r))
    except Exception as e:  # noqa: BLE001
        C.check("a snapshot from kontextus/ can be archived", False, f"{type(e).__name__}: {e}")

    # (b) a .bak_ snapshot in the root -> GOES
    try:
        M.archive_generic("_state.md.bak_2026-09-01_pre_compaction", "dated copy", digest_entry=False)
        C.check("a .bak_ snapshot in the root can be archived too",
                not os.path.exists(os.path.join(TMP, "_state.md.bak_2026-09-01_pre_compaction")))
    except Exception as e:  # noqa: BLE001
        C.check("a .bak_ snapshot in the root can be archived", False, f"{type(e).__name__}: {e}")

    # (c) ⚠️ THE MOST IMPORTANT NEGATIVE: a LIVE load-bearing file can NOT be archived
    for live in (CTX, "_state.md"):
        try:
            M.archive_generic(live, "this must not go", digest_entry=False)
            C.check(f"LIVE file stays PROTECTED: {live}", False, "no error raised -- IT WENT THROUGH!")
        except ValueError:
            C.check(f"LIVE file stays PROTECTED (ValueError + the file is in place): {live}",
                    os.path.exists(os.path.join(TMP, live)))
        except Exception as e:  # noqa: BLE001
            C.check(f"LIVE file stays PROTECTED: {live}", False, f"unexpected error: {type(e).__name__}: {e}")

    # (d) regression: the old whitelist still works
    try:
        M.archive_generic("eszkozok/old_tool.md", "outdated", digest_entry=False)
        C.check("regression: archiving from eszkozok/ still works",
                not os.path.exists(os.path.join(TMP, "eszkozok", "old_tool.md")))
    except Exception as e:  # noqa: BLE001
        C.check("regression: archiving from eszkozok/ works", False, f"{type(e).__name__}: {e}")

C.finish()
