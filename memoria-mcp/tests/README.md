# memoria-mcp tests

Plain Python scripts (no pytest needed, no MCP SDK needed). Each `test_*.py`
loads the real `server.py`, runs its checks, and ends with one line:

```
RESULT: <n> passed, <n> failed, <n> skipped, <n> xfail, <n> xpass
```

## Running

```bash
cd tests
python -X utf8 run_all.py        # every test in its own subprocess; -v prints failing output
python -X utf8 test_followup_reschedule.py   # a single file
```

`-X utf8` matters on Windows: the output contains emoji, which a legacy console
code page cannot print. `run_all.py` exits non-zero if any file fails.

- **SKIP** = a case that cannot run here, always with the reason (e.g. an
  optional pre-patch backup is not present).
- **XFAIL** = a known server bug, documented in the check's message. If it
  starts passing it shows as XPASS: remove the xfail.

## What is covered

| File | What it checks |
|---|---|
| `test_completed_line_filter.py` | "done" words (Hungarian + English) match on word boundaries only; filtered future dates are reported, not dropped; end-to-end through `get_deadlines()` |
| `test_deadline_head_anchor.py` | only the date at the head of a deadline bullet is a deadline; body dates go to an advisory block; genuine deadlines are not lost |
| `test_deadline_same_day_dedup.py` | two separate deadlines on the same day both survive the date dedup; prose repetitions do not |
| `test_followup_detail.py` | `get_followup(ids)` returns full details; unknown ids, bad tokens and cuts are stated out loud |
| `test_followup_reschedule.py` | `reschedule_followup`: mutation rules, required reason, append-only history, precondition, rendering in `get_followups` |
| `test_handoff_expiry.py` | a stale "morning handoff" line in `_state.md` is expired in the rendered view; everything else stays bit-identical |
| `test_marker_declared_coverage.py` | the `covers` declaration of follow-ups: parsing, suffix matching, stale file-level covers, end-to-end in `get_open_markers` |
| `test_marker_lexical_coverage.py` | lexical marker/follow-up scoring: strong / weak / none bands, identifier weight, best match wins, evidence in the tag |
| `test_prediction_contamination.py` | "contaminated" (non-measurement) predictions: required reason, later marking, exclusion from calibration that stays visible |
| `test_prediction_horizon.py` | prediction horizons: pending vs. abandoned, text-horizon hints (horizon form only), labels in `get_calibration` |
| `test_radar_fail_closed.py` | radars report checks they could not run (real injected faults: unreadable files, broken symlink, invalid UTF-8) |
| `test_search_provenance.py` | `search_memory` bands (live / log / snapshot), loud warning on 0 live hits, the cap says it is a cap; snapshot skip in `get_open_markers`; `archive_generic` snapshot exception |
| `test_search_provenance_module_import.py` | the same search behaviour through a plain `import server`, proving MEMORIA_ROOT is honoured at import time |
| `test_server_name_integrity.py` | static scope analysis: no global name in `server.py` is unresolvable (a run-time NameError), with a built-in negative control |
| `test_unlogged_carryover_log.py` | `get_unlogged_changes`: a day covered only by a multi-day log goes to the 🟡 carry-over band, not 🔴 |
| `test_unlogged_scheduled_logs.py` | `get_unlogged_changes`: a closed day without the daily scheduled logs is flagged 🟠, independent of file mtimes |
| `test_unlogged_today_bucket.py` | `get_unlogged_changes`: today's changes go to a separate ⏳ TODAY bucket instead of a false 🔴 |

## Isolation: nothing touches a real memory

Every test loads the server through `_support.load_server()`, which

1. creates a fresh temporary memory root (`tempfile.mkdtemp`) and sets
   `MEMORIA_ROOT` to it **before** the module is executed (the server reads it
   at import time), and refuses to run on a root outside the temp folder;
2. removes `MEMORIA_CONTEXT_FILE`, `MEMORIA_PATTERNS_FILE` and
   `MEMORIA_OWNER_NAME` from the environment and points `MEMORIA_AUDIT_DB` into
   the temp folder;
3. copies `server.py` into a temporary code folder and loads that copy, so the
   paths the server derives from its own location (the default audit database,
   the "own code" folder watched by `get_unlogged_changes`) are temporary too;
4. replaces the `mcp` package with a small stub.

All fixture files are written inside those temp roots (mostly through
`_support.write()`, which refuses any path outside the temp folder); all temp
folders are removed at exit. The fixtures are synthetic: no test reads a real
memory folder. A full `run_all.py` leaves the repository byte-for-byte
unchanged (checked by diffing a file listing with sizes and mtimes before and
after a run).

## Optional negative controls

Some tests can also run against a pre-patch server, where their checks must
fail ("a guard that is green on the buggy version is no guard"). The backups
are not shipped; put one into `tests/` (or the server folder) to enable it:

| Test | Backup file pattern / flag |
|---|---|
| `test_unlogged_carryover_log.py --negative` | `server_backup_pre_atnyulo_*.py` |
| `test_search_provenance.py --negative` | `server_backup_pre_provenancia_*.py` |
| `test_prediction_contamination.py --backup` | `server_backup_pre_szennyezett_*.py` |
| `test_radar_fail_closed.py --mutans <file.py>` | any older server file |
| `test_deadline_head_anchor.py` (automatic) | `server_backup_pre_head_anchor_*.py` |
| `test_followup_detail.py` (automatic) | `server_backup_pre_followup_detail_*.py` |

Without the backup the control prints `SKIP (no pre-patch backup)` and the main
checks still run against the current `server.py`.

## Platform notes

`test_radar_fail_closed.py` injects real read faults. On POSIX it uses
`chmod 000` (ineffective as root). On Windows `chmod` cannot make a file
unreadable, so it falls back to a byte-range lock (`msvcrt.locking`). If no
method takes effect, those cases are skipped with a message. The broken-symlink
case is skipped where symlinks cannot be created.
