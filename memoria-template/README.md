# The memory folder (template)

Copy this whole folder to `{MEMORIA}`, a folder **outside** the repository, and point `MEMORIA_ROOT` and `CLAUDE_MEMORY_DIR` at it (INSTALL chapter 1–2). That copy is **your** memory and never goes back into the repository. (If no variable is set, the tools fall back to `{REPO}/memoria/`, which `.gitignore` excludes, but outside the repo is the rule.)

The folder names are Hungarian, because the system grew up in Hungarian and every tool in this package expects them. You don't have to type them often. Claude does the writing.

| Folder / file | Meaning | What lives there |
|---|---|---|
| `_state.md` | state | what Claude is working on now, what's next, blockers. Short; read at every start |
| `kontextus/` | context | the living picture: `helyzet.md` ("situation": the owner's life, plans, deadlines), `mintazatok.md` ("patterns": lessons learned across sessions), plus `followups.json`, `predikciok.json` and `owner_flags.json`, which the server writes itself |
| `naplo/` | journal | one file per session per day: `YYYY-MM-DD_session_N.md`. The raw story, what happened and how it felt |
| `projektek/` | projects | one file per ongoing project or thread |
| `eszkozok/` | tools | how-tos and recipes Claude wrote for itself |
| `archiv/` | archive | old journals and closed projects (the `archive` tools move them here) |

You can rename the folders: see `memoria.config.example.json` in `memoria-mcp/` (keys `context_file`, `patterns_file`). The other scripts (hooks, recall index) take their folder from `CLAUDE_MEMORY_DIR`.

## Conventions the server understands

- **Deadlines** go into `kontextus/helyzet.md`, under the heading `## URGENT / UPCOMING DEADLINES`, one per line, starting with an ISO date: `- 2099-03-15: final exam`. Dates elsewhere in the file are treated as references, not deadlines.
- **Open markers**: a line with `TODO:` anywhere in the memory is an open item for `get_open_markers`, until a follow-up covers it or it is marked done (`✅`).
- **Closed items**: put `✅` on the line, or the word `DONE` / `CLOSED` in capitals (a lower-case "done" inside a sentence doesn't count, so "TODO: get it done" stays open).
- **Questions for the owner** go into `_state.md`, under `## Open questions` → `### Questions needing the owner's decision`; write "No active question." when there is none.
- **Two Claudes**: each writes its own journal file name (e.g. `_night` in the name), and signs shared files with date and name. See the main INSTALL, chapter 11.

## Privacy

Everything here is about real people. Keep the folder out of any public place, back it up privately, and think about who can read the machine it lives on.
