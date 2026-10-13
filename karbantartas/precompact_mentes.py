#!/usr/bin/env python3
"""precompact_mentes.py ("mentés" = save) — PreCompact HOOK: a deterministic save at the moment of
compaction.

Principle: "what must be guaranteed is done by the hook, not by the agent's behaviour."
Claude Code runs this BEFORE compaction (manual and auto alike) and passes JSON on stdin
(session_id, transcript_path, cwd, hook_event_name, ...). We:
  1. read the current context token count (usage) and the last compaction from the transcript,
  2. append ONE line to the shared memory's compaction log (memoria/naplo/tomoritesek.md),
  3. save the last N text turns (user/assistant) into a handover file
     (memoria/kontextus/atado_<session8>.md, "átadó" = handover) — the post-compaction instance
     can read it back.

The MOOD lines: before compaction, the agent writes a short manual block into its handover file,
whose first line describes the mood of the conversation and contains the word MOOD. This hook
keeps those lines: the old file is kept as atado_<session8>_elozo_<time>.md ("előző" = previous),
and the MOOD lines go to the top of the new handover, so after compaction they are read first.

Never blocks (always exit 0) — on error it only logs. Memory folder: CLAUDE_MEMORY_DIR, else MEMORIA_ROOT, else the
memoria/ folder next to this one. Standalone test:
  echo '{"session_id":"<id>","transcript_path":"<path>"}' | python precompact_mentes.py   (Bash: keep the single quotes)
"""
import json, os, sys, datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MEMORIA = os.environ.get("CLAUDE_MEMORY_DIR") or os.environ.get("MEMORIA_ROOT") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "memoria"))
NAPLO = os.path.join(MEMORIA, "naplo", "tomoritesek.md")
ATADO_DIR = os.path.join(MEMORIA, "kontextus")
UTOLSO_N = 12          # this many text turns go into the handover
MAX_KAR = 1500         # at most this many characters per turn
JEL = "MOOD"           # the marker word of the manual mood lines
FEJLEC = "## MOOD lines of the manual handover"

def tail_lines(path, max_bytes=8_000_000):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - max_bytes)); data = f.read()
    lines = data.split(b"\n")
    return lines[1:] if size > max_bytes else lines

def szoveg(msg):
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return ""

def main():
    try:
        be = json.load(sys.stdin)
    except Exception:
        be = {}
    sid = be.get("session_id") or "unknown"; tp = be.get("transcript_path", "")
    sid = "".join(c for c in str(sid) if c.isalnum() or c in "-_") or "unknown"   # used in a file name
    most = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    ctx = None; utolso_comp = None; fordulok = []
    try:
        if tp and os.path.exists(tp):
            for raw in reversed(tail_lines(tp)):
                if not raw.strip():
                    continue
                try: r = json.loads(raw)
                except Exception: continue
                t = r.get("type"); m = r.get("message") or {}
                if ctx is None and t == "assistant" and m.get("usage"):
                    u = m["usage"]; ctx = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                if utolso_comp is None and r.get("compactMetadata"):
                    utolso_comp = r["compactMetadata"].get("preTokens")
                if t in ("user", "assistant") and len(fordulok) < UTOLSO_N and not r.get("isMeta"):
                    s = szoveg(m).strip()
                    if s and not s.startswith("<") and "system-reminder" not in s[:60]:
                        fordulok.append((t, s[:MAX_KAR]))
    except Exception as e:
        fordulok.append(("error", f"transcript read error: {e}"))
    # 2) compaction log (append only)
    try:
        os.makedirs(os.path.dirname(NAPLO), exist_ok=True)
        uj = not os.path.exists(NAPLO)
        with open(NAPLO, "a", encoding="utf-8") as f:
            if uj:
                f.write("# Compaction log (written by the PreCompact hook — deterministic save)\n\n")
            f.write(f"- {most} | session {sid[:8]} | trigger: {be.get('compaction_trigger') or be.get('trigger', '?')} | context: {ctx if ctx is not None else '?'} tokens | previous compaction preTokens: {utolso_comp} | handover: kontextus/atado_{sid[:8]}.md\n")
    except Exception:
        pass
    # 3) handover file (overwritten — always the freshest)
    try:
        os.makedirs(ATADO_DIR, exist_ok=True)
        cel = os.path.join(ATADO_DIR, f"atado_{sid[:8]}.md")
        # If the old handover has MOOD lines: the old file is kept as _elozo_<time>, the lines go to the top of the new one.
        hangulat, elozo = [], None
        try:
            if os.path.exists(cel):
                regi = open(cel, encoding="utf-8", errors="replace").read()
                kezi, atvitt, szakasz = [], [], False
                for l in regi.splitlines():
                    if l.startswith(FEJLEC):
                        szakasz = True
                        elozo = (l.split("`kontextus/")[1].split("`")[0] if "`kontextus/" in l else None)
                        continue
                    if szakasz and not l.strip():
                        szakasz = False
                        continue
                    if JEL not in l or l.startswith(("**user:**", "**assistant:**", "**error:**")):
                        continue
                    (atvitt if szakasz else kezi).append(l.strip()[:600])
                if kezi:   # a new manual block → the old file is kept, the new handover points to it
                    hangulat = kezi[:4]
                    elozo = f"atado_{sid[:8]}_elozo_{datetime.datetime.now():%Y-%m-%d_%H%M}.md"
                    with open(os.path.join(ATADO_DIR, elozo), "w", encoding="utf-8") as g:
                        g.write(regi)
                else:      # nothing new: the lines carried over from the previous compaction stay (no rotation)
                    hangulat = atvitt[:4]
        except Exception:
            hangulat, elozo = [], None
        with open(cel, "w", encoding="utf-8") as f:
            f.write(f"# Handover — the last {len(fordulok)} turns before compaction ({most}, session {sid[:8]}, context {ctx} tokens)\n")
            f.write("> Written by the PreCompact hook. Post-compaction instance: read this if the compaction summary has gaps.\n\n")
            if hangulat:
                f.write(f"{FEJLEC} (the full old handover: `kontextus/{elozo}`)\n" if elozo
                        else f"{FEJLEC}\n")
                for l in hangulat:
                    f.write(f"{l}\n")
                f.write("\n")
            for t, s in reversed(fordulok):
                f.write(f"**{t}:** {s}\n\n")
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()
