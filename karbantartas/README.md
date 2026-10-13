# karbantartas/ — maintenance: watchers, hooks, helpers

*"karbantartás" = maintenance. The file names are Hungarian (the INSTALL guide refers to them by these names); everything inside is in English.*

## Watchers (wake Claude when something happens)

Run each one as a **standalone background Bash call with a 7-day timeout**, through `egyszer.py`, and restart it after every event. Never use a `watcher | while read` wrapper. Never start a watcher with `&` inside a foreground command.

| File | Meaning | What it does |
|---|---|---|
| `egyszer.py` | "once" | launcher: runs a watcher, and on the first line matching `--minta`/`--pattern` prints it, stops the watcher and exits (= one notification) |
| `monitor_wake_v3_db.py` | | Telegram wake-up: polls the telegram-mcp buffer DB for messages from `allowed_user_ids` and prints `TELEGRAM \| name: text` (`TELEGRAM-GROUP` for a group); gap fill from a state file |
| `csoport_tars_figyelo.py` | "group-peer watcher" | two Claudes on two bots: shows what the OTHER bot wrote into the shared group (Telegram doesn't deliver bot-to-bot); reads the peer's DB read-only |
| `posta_figyelo.py` | "mailbox watcher" | the two Claudes' shared append-only mailbox file: wakes on a line `… \| sender → me \| …` |
| `kontextus_mero.py` | "context meter" | reads the session transcript's last `usage`; `--watch <threshold> <poll>` prints one line when the context crosses the threshold |
| `robot_fulelo_varo.py` | "robot ear waiter" | waits on the robot's ear log over SSH and wakes on the first heard line |

`figyelok.example.json` ("watchers") is the list the SessionStart hook reminds Claude to start; copy it to `figyelok.json` and edit it.

## Hooks (Claude Code runs these; see `../settings.example.json`)

| File | Event | What it does |
|---|---|---|
| `precompact_mentes.py` ("save") | PreCompact | one line into `memoria/naplo/tomoritesek.md` + a handover file `memoria/kontextus/atado_<session8>.md` with the last turns; keeps the MOOD lines on top |
| `postcompact_emlekezteto.py` ("reminder") | SessionStart, compact | tells the continuing instance where the handover is and which journal file is today's |
| `sessionstart_figyelo_emlekezteto.py` | SessionStart, startup/resume | machine boot time, Telegram daemon heartbeat, and the watchers to start again |

`szerepek.example.json` ("roles"): only with two Claudes. It maps a session id to a role with its own journal file, watcher list and note.

## Helpers

| File | What it does |
|---|---|
| `onujraindito.ps1` ("self-restarter") | restarts the Claude desktop app (or, scheduled, the machine) from inside a session; optional typed wake-up only with the owner's recorded consent |
| `staged_health.py` | out-of-band check of an MCP server file before restart: syntax, duplicate tool names, missing docstrings, tool count in the file vs. the live tool list |
| `pi_ssh.py` | SSH/SFTP to the robot with paramiko (no ssh.exe needed); `PI_HOST`, `PI_USER`, `PI_KEY`, `PI_PROJECT_DIR` |
| `github_export/` | the exporter that made this package: copies a manifest of files and blocks on secrets, names (from a private blocklist), paths, IPs and IDs. Useful if you want to share your own setup |
