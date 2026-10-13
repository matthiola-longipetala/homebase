#!/usr/bin/env python3
"""postcompact_emlekezteto.py ("emlékeztető" = reminder) — SessionStart (matcher: compact) HOOK.

Runs AFTER compaction; Claude Code hands what this prints (the `additionalContext` of the JSON on
stdout) to the continuing instance. We use it to say where the handover is (written by the
PreCompact hook), what the last compaction-log line was, and which journal file today's notes go to.
Never blocks.

Memory folder: CLAUDE_MEMORY_DIR, else MEMORIA_ROOT (the same as memoria-mcp), else the memoria/ folder next to this one.

Two Claudes (optional): szerepek.json ("szerepek" = roles) next to this script maps a session id
(first 8 characters) to a role with its own journal file and an extra note, e.g.
  {"1a2b3c4d": {"journal": "naplo/{date}_night.md",
                "note": "YOU ARE THE NIGHT CLAUDE: only look after your OWN watchers."}}
"""
import datetime, json, os, sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MEMORIA = os.environ.get("CLAUDE_MEMORY_DIR") or os.environ.get("MEMORIA_ROOT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memoria"))
NAPLO = os.path.join(MEMORIA, "naplo", "tomoritesek.md")
SZEREPEK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "szerepek.json")

def main():
    try:
        be = json.load(sys.stdin)
    except Exception:
        be = {}
    sid = "".join(c for c in str(be.get("session_id") or "") if c.isalnum() or c in "-_")[:8]   # same rule as precompact
    atado = os.path.join(MEMORIA, "kontextus", f"atado_{sid}.md") if sid else ""
    utolso = ""
    try:
        with open(NAPLO, encoding="utf-8") as f:
            sorok = [s for s in f.read().splitlines() if s.startswith("- ")]
        utolso = sorok[-1] if sorok else ""
    except Exception:
        pass
    ma = datetime.date.today().isoformat()
    try:
        szerep = json.load(open(SZEREPEK, encoding="utf-8")).get(sid) or {}
    except Exception:
        szerep = {}
    naplo_fajl = (szerep.get("journal") or "naplo/{date}_session_1.md").replace("{date}", ma)
    uzenet = ("YOU ARE AFTER A COMPACTION. If the summary has gaps: read the handover "
              f"({atado if atado and os.path.exists(atado) else 'no handover file'}) — its MOOD lines first, then the facts — "
              "and the open promises (memoria-mcp get_followups). "
              f"Last compaction-log line: {utolso or 'none'}. "
              "Running background watchers survive a compaction, but not an app restart. "
              f"★ JOURNAL DATE: today is {ma} — write into TODAY's file ({naplo_fajl}; if it doesn't exist, "
              "open it with a pointer to the previous one). A multi-day session = a new file every day.")
    if szerep.get("note"):
        uzenet += " " + szerep["note"]
    # TRACE: fail-closed evidence that the hook ran at all
    try:
        with open(os.path.join(MEMORIA, "naplo", "postcompact_nyom.log"), "a", encoding="utf-8") as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " | session " + (sid or "?")
                    + " | source=" + str(be.get("source") or be.get("matcher") or "?") + " | ran\n")
    except Exception:
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": uzenet}}, ensure_ascii=False))
    sys.exit(0)

if __name__ == "__main__":
    main()
