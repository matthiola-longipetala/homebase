#!/usr/bin/env python3
"""sessionstart_figyelo_emlekezteto.py ("figyelő-emlékeztető" = watcher reminder) — SessionStart HOOK
(matcher: startup|resume).

After a start or resume it tells the new instance, in one message, what the state is (machine boot
time, Telegram sync daemon heartbeat) and which watchers to start again: background watchers do NOT
survive an app restart. Read only. Never blocks.

The launch list lives in figyelok.json ("figyelők" = watchers) next to this script, so you can edit it
without touching code (see figyelok.example.json). With two Claudes, szerepek.json ("szerepek" = roles)
maps a session id (first 8 characters) to a role; a role can have its own launch list, Telegram data
folder and journal file. In a command, `{session}` is replaced by the full session id, `{date}` by today,
`{karbantartas}` by this folder's absolute path (so the commands work from any working folder), and
`{meter_threshold}` by the context meter's threshold: figyelok.json "meter_threshold", else the env var
CONTEXT_METER_THRESHOLD, else 130000 (~65 % of a 200k-token window; for a 1M-token model use ~590000).
The message is printed ONCE, as the hookSpecificOutput JSON (plain text next to it would be injected twice).

Env: CLAUDE_MEMORY_DIR or MEMORIA_ROOT (memory folder, default ../memoria), TELEGRAM_MCP_DATA (the sync daemon's data
folder with sync_daemon.lock; default: data/ next to TELEGRAM_MCP_CONFIG, else ../telegram-mcp/data).
"""
import datetime, json, os, subprocess, sys, time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ITT = os.path.dirname(os.path.abspath(__file__))
GYOKER = os.path.dirname(ITT)
MEMORIA = os.environ.get("CLAUDE_MEMORY_DIR") or os.environ.get("MEMORIA_ROOT") or os.path.join(GYOKER, "memoria")
TG_ALAP = (os.environ.get("TELEGRAM_MCP_DATA") or os.environ.get("TELEGRAM_SYNC_DATA_DIR")
           or (os.path.join(os.path.dirname(os.path.abspath(os.environ["TELEGRAM_MCP_CONFIG"])), "data")
               if os.environ.get("TELEGRAM_MCP_CONFIG") else os.path.join(GYOKER, "telegram-mcp", "data")))

# Without figyelok.json only the context meter is listed (the Telegram watcher needs telegram-mcp set up
# first; listed too early it would fail at once and wake Claude in a loop). Copy figyelok.example.json.
ALAP_LISTA = [
    "context meter: `PYTHONUTF8=1 py -3.12 -u {karbantartas}/egyszer.py --minta \"CONTEXT|compaction\" -- py -3.12 -u {karbantartas}/kontextus_mero.py --session {session} --watch {meter_threshold} 120`",
    "(no figyelok.json yet: copy {karbantartas}/figyelok.example.json to figyelok.json and keep only the watchers you have set up)",
]


def json_olvas(ut, alap):
    try:
        with open(ut, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return alap


def demon_allapot(adat):
    try:
        lock = json_olvas(os.path.join(adat, "sync_daemon.lock"), None)
        if not isinstance(lock, dict):
            return f"⚠️ no readable lock in {adat} (daemon not running, or not installed)"
        kor = int(time.time()) - int(lock.get("heartbeat", 0))
        return (f"alive (pid {lock.get('pid')}, heartbeat {kor}s ago)" if kor < 300
                else f"⚠️ no heartbeat for {kor}s (pid {lock.get('pid')})")
    except Exception as e:
        return f"⚠️ no readable lock ({type(e).__name__})"


def boot_ido():
    try:
        if os.name == "nt":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-dd HH:mm')"],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
        else:
            out = subprocess.run(["uptime", "-s"], capture_output=True, text=True, timeout=5).stdout.strip()
        return out or "?"
    except Exception:
        return "?"


def main():
    try:
        be = json.load(sys.stdin)
    except Exception:
        be = {}
    src = be.get("source") or be.get("matcher") or "?"
    sid = "".join(c for c in str(be.get("session_id") or "") if c.isalnum() or c in "-_")   # goes into shell commands
    ma = datetime.date.today().isoformat()
    szerepek = json_olvas(os.path.join(ITT, "szerepek.json"), {})
    szerep = (szerepek.get(sid[:8]) if isinstance(szerepek, dict) else None) or {}
    if not isinstance(szerep, dict):
        szerep = {}
    nev = szerep.get("role", "default")
    figyelok = json_olvas(os.path.join(ITT, "figyelok.json"), {})
    if not isinstance(figyelok, dict):
        figyelok = {}
    lista = figyelok.get(nev) or figyelok.get("default") or ALAP_LISTA
    adat = szerep.get("telegram_data") or TG_ALAP
    naplo = (szerep.get("journal") or "naplo/{date}_session_1.md").replace("{date}", ma)

    karb = ITT.replace("\\", "/")
    kuszob = str(figyelok.get("meter_threshold") or os.environ.get("CONTEXT_METER_THRESHOLD") or 130000)
    sorok = " ".join(f"({i}) {s.replace('{session}', sid).replace('{date}', ma).replace('{karbantartas}', karb).replace('{meter_threshold}', kuszob)};"
                     for i, s in enumerate(lista, 1))
    uzenet = (f"SESSION START ({src}{', role ' + nev if szerep else ''}). Machine boot: {boot_ido()}. "
              f"Telegram sync daemon: {demon_allapot(adat)}. "
              "Background watchers do NOT survive a restart → start them again, each as a standalone background "
              "Bash call with a 7-day timeout, through the egyszer.py launcher (never a `| while read` wrapper; "
              "never with `&` in a foreground command). After an app restart, old watchers may still run orphaned: "
              f"clean those up first. {sorok} "
              f"★ JOURNAL DATE: today is {ma} — write into today's file ({naplo}; if it doesn't exist, open it with a "
              "pointer to the previous one).")
    if szerep.get("note"):
        uzenet += " " + szerep["note"]
    # TRACE: fail-closed evidence that the hook ran at all
    try:
        with open(os.path.join(MEMORIA, "naplo", "sessionstart_nyom.log"), "a", encoding="utf-8") as f:
            f.write(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + f" | source={src} | session {sid[:8] or '?'} | ran\n")
    except Exception:
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": uzenet}}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
