# memoria-mcp — the shared memory

*"memória" = memory.*

An MCP server that gives Claude structured access to a file-based memory: the owner's situation and deadlines, the current state, a journal, projects, and Claude's own bookkeeping (follow-ups, flags for the owner, predictions). Instead of reading and parsing files by hand, Claude calls `get_context()` at the start of a session and gets one actionable overview.

The memory is plain Markdown and JSON in a folder you own (`memoria/`, see `../memoria-template/`). Several Claude instances can share it: Claude Desktop, Claude Code, a second Claude on a night shift.

## Setup

1. `pip install -r requirements.txt` (Python 3.10+). Keep `mcp` below 2.0; see the comment in the file.
2. Create the memory: copy `../memoria-template/` to `{MEMORIA}`, a folder **outside** the repository, and point `MEMORIA_ROOT` at it. (Without `MEMORIA_ROOT` the server falls back to the git-ignored `{REPO}/memoria/` and says so on stderr.)
3. Optional: copy `memoria.config.example.json` to `<MEMORIA_ROOT>/memoria.config.json`. That's where you set `owner_name` (how the owner is called in the output), other file names, or extra folders to watch.
4. Register it (Claude Desktop `claude_desktop_config.json`, or Claude Code `claude mcp add`):
   ```json
   "memoria-mcp": {
     "command": "python",
     "args": ["{REPO}/memoria-mcp/server.py"],
     "env": { "MEMORIA_ROOT": "{MEMORIA}" }
   }
   ```
5. Restart the app, then ask Claude to call `get_context()`.

Tests: `python -X utf8 tests/run_all.py`. They only use temporary folders and never touch a real memory.

## The tools

**Start of a session**

| Tool | What it gives |
|---|---|
| `get_context()` | the full overview: the owner's situation, deadlines, state, last 3 days of sessions. **Call it first.** |
| `get_deadlines()` | deadlines sorted by urgency, from the deadline section of the context file only |
| `get_state()` / `update_state(section, content)` | the short `_state.md`: focus, next steps, blockers, open questions |
| `get_open_threads()` | what is open: blockers, questions waiting for the owner, plans |
| `get_patterns(part)` | the cross-session lessons file, on demand (it can be large) |

**Journal and context**

| Tool | What it does |
|---|---|
| `log_entry(title, content, entry_type)` | a timestamped journal entry in `naplo/`: every call writes a NEW file, `YYYY-MM-DD_<entry_type>_<N>.md` (N counts up per day and type). The "one file per session per day" convention (`_session_N.md`) is for the journal Claude writes itself with the file tools; use `log_entry` for separate notes |
| `log_session_summary(session_type, highlights, context_updates, open_items)` | a structured trace of a session, for the next instance |
| `get_recent_activity(days)` | what happened in the last N days |
| `reflect(topic)` | everything the memory knows about a topic, synthesized |
| `suggest_context_updates()` / `acknowledge_updates(...)` | compares recent sessions with the context file and suggests what to update; marks suggestions as handled |
| `update_context(field, value)` | updates one field of the context file |
| `search_memory(query)` | full-text search with provenance (live file, journal, or old snapshot) |

**Files**

`list_memory_files`, `read_memory_file`, `write_memory_file` (with an integrity check), `append_memory_file`, `move_memory_file`, `delete_memory_file`. Every path stays inside `MEMORIA_ROOT`, and writes are atomic. Moves and deletes are logged in `<MEMORIA_ROOT>/_audit/memoria_audit.db` (it keeps the first line of each file, so it stays with the memory; override with `MEMORIA_AUDIT_DB`).

**Archive**

`archive(dry_run, retention_days)` moves old sessions and logs into `archiv/`. `archive_project`, `archive_generic` and `search_archive` work on single files.

**Claude's own bookkeeping**

| Tool | What it is for |
|---|---|
| `track_followup`, `get_followups`, `get_followup`, `reschedule_followup`, `set_followup_covers`, `resolve_followup` | Claude's waiting list: "I'll check whether X happened by date Y". Rescheduling keeps a history instead of creating successor threads |
| `get_open_markers(...)` | sweeps the memory for orphaned `TODO:` markers no follow-up covers. A suspicion list, not a todo list |
| `get_unlogged_changes(days, grace_days)` | finds work that landed in files but never made it into the journal (the "silent session end") |
| `flag_for_owner`, `get_owner_flags`, `resolve_owner_flag` | things only the owner can decide: Claude flags them, sends the question, and later records whether asking was useful. That builds a calibration of when to speak up |
| `predict`, `defer_prediction`, `score_prediction`, `get_calibration` | falsifiable predictions recorded *before* checking, scored afterwards; `get_calibration` shows how well-calibrated Claude is, separately for the world, for itself, and for the owner |
| `get_claude_reflections()` | Claude's own notes about itself |
| `get_code_freshness(paths, slack_seconds)` | whether a server file changed after the running process started (i.e. a patch is staged but not live) |

## Writing the memory so the server understands it

- **Deadlines:** under a `## … DEADLINES` heading in the context file (`kontextus/helyzet.md`), one per line, ISO date first. Dates elsewhere are references, but future dates outside the section are reported as "might be in the wrong place", so a misplaced deadline is not silently lost.
- **Open markers:** `TODO:` (or `TEENDŐ:`), closed with `✅` or a `DONE` / `CLOSED` word.
- **Questions for the owner:** `_state.md` → `## Open questions` → `### Questions needing the owner's decision`.
- The parsers accept both the English and the Hungarian markers, so a memory can be written in either language.

## Design principles

- **Calendar days, not timestamps.** Today = 0, tomorrow = +1. A deadline dated today is not overdue at 2 a.m.
- **Nothing is dropped silently.** When a filter or a size limit holds something back, the output says so, and says where the rest is.
- **The loaded view is short; the files are not.** `get_context()` truncates long session highlights to a pointer, because it is loaded at every start, but the journal itself is never shortened.
- **Suspicion lists are not verdicts.** The marker and unlogged-change radars point at places to look; closing an item needs evidence.
