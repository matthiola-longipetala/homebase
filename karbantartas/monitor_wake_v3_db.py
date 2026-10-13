"""
monitor_wake_v3_db.py — the Telegram wake-up watcher (DB only).

Why v3: earlier versions called getUpdates themselves, but telegram_sync_daemon.py is the ONE
legitimate getUpdates consumer, and two consumers collide with a 409 Conflict. So this script ONLY
polls the buffer database (messages.db); there is no getUpdates call in it.

- Watched senders: the `allowed_user_ids` from the telegram-mcp config (the daemon stores every
  message; here we only filter what wakes you). Override with TG_WAKE_USER_IDS="id1,id2".
- Baseline: the largest incoming update_id at start → only messages arriving AFTER that wake you,
  EXCEPT the gap fill (below).
- Every new message is one line: `TELEGRAM | <name>: <text>` (or `TELEGRAM-GROUP | …` for a group chat).
- Launch it with egyszer.py: it exits on the first event, so restart it after every event.

Gap fill: between an event and the restart there is a short gap, and a message landing in it would
never wake you. So the last DELIVERED update_id goes into a state file, and at start the watcher
continues from there: whatever arrived during the gap (at most RES_MAX_KOR hours old, at most
RES_MAX_DB lines) is still printed.

Second bot / second Claude: point TELEGRAM_MCP_CONFIG at that bot's config (DB and senders follow),
or set TG_WAKE_DB and TG_WAKE_STATE directly.
Optional: EMLEK_INDEX_DIR → one associative-recall line after each message (emlek_index); --nincs-emlek
("no memory") switches it off. Without the index it is a silent no-op.
"""
import os
import sqlite3
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The telegram-mcp folder (for beallitas.py, the shared config reader); default: ../telegram-mcp
TG_DIR = os.environ.get("TELEGRAM_MCP_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram-mcp")
sys.path.insert(0, TG_DIR)

NINCS_EMLEK = "--nincs-emlek" in sys.argv or "--no-recall" in sys.argv
_emlek_sor = lambda _s: ""
if not NINCS_EMLEK and os.environ.get("EMLEK_INDEX_DIR"):
    try:
        sys.path.insert(0, os.environ["EMLEK_INDEX_DIR"])
        from emlek_kliens import emlek_sor as _emlek_sor
    except Exception:
        pass


def emlek_kiir(szoveg):
    try:
        s = _emlek_sor(szoveg)
        if s:
            print(s, flush=True)
    except Exception:
        pass


def _beallitas():
    try:
        import beallitas
        return beallitas
    except Exception:
        return None


_b = _beallitas()
DB = os.environ.get("TG_WAKE_DB") or (_b.db_path() if _b else None)
if os.environ.get("TG_WAKE_USER_IDS"):
    WATCH = [int(x) for x in os.environ["TG_WAKE_USER_IDS"].split(",") if x.strip()]
else:
    WATCH = list(_b.engedelyezett()) if _b else []
if not DB or not WATCH:
    print("monitor-error: no DB or no watched senders (set TELEGRAM_MCP_CONFIG with a non-empty "
          "allowed_user_ids, or TG_WAKE_DB + TG_WAKE_USER_IDS)", flush=True)
    sys.exit(2)

POLL_SLEEP = 3  # s; the sync daemon writes about every 2 s, polling faster is pointless

ERROR_ALERT_AT = 12  # after this many consecutive errors (~1 minute), ONE alert
consec_errors = 0
alerted = False


def query(sql, params=()):
    c = sqlite3.connect(DB, timeout=10.0)
    try:
        c.row_factory = sqlite3.Row
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


STATE = os.environ.get("TG_WAKE_STATE") or os.path.join(os.path.dirname(DB), "wake_watcher_last_seen.txt")
RES_MAX_KOR = 6      # an older gap is not filled (e.g. a whole night) → plain baseline
RES_MAX_DB = 20      # nor does it pour more lines than this into the chat


def _save_seen(v):
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(int(v)))
        os.replace(tmp, STATE)
    except Exception:
        pass


rows = query("SELECT COALESCE(MAX(update_id), 0) AS m FROM messages WHERE update_id > 0")
db_max = rows[0]["m"] or 0
last_seen = db_max
res_mod = "baseline"
try:
    with open(STATE, encoding="utf-8") as f:
        mentett = int(f.read().strip() or 0)
    if 0 < mentett < db_max:
        ph = ",".join("?" * len(WATCH))
        hianyzo = query(
            f"SELECT update_id, date_ts FROM messages WHERE is_bot = 0 AND update_id > ? "
            f"AND user_id IN ({ph}) ORDER BY update_id ASC",
            [mentett, *WATCH],
        )
        friss = [r for r in hianyzo if (time.time() - (r["date_ts"] or 0)) <= RES_MAX_KOR * 3600]
        if friss and len(friss) <= RES_MAX_DB:
            last_seen = mentett
            res_mod = f"GAP FILL: {len(friss)} undelivered message(s) after {mentett}"
        elif hianyzo:
            res_mod = f"gap too old/large ({len(hianyzo)} rows), baseline"
    elif mentett >= db_max:
        last_seen = mentett
except FileNotFoundError:
    pass
except Exception as e:
    res_mod = f"state file error ({type(e).__name__}), baseline"
_save_seen(last_seen)
print(
    f"DB watcher up (poll {POLL_SLEEP}s, NO getUpdates) — "
    f"{len(WATCH)} watched sender(s), baseline update_id={last_seen}, {res_mod}",
    flush=True,
)

while True:
    try:
        ph = ",".join("?" * len(WATCH))
        rows = query(
            f"SELECT update_id, user_name, text, chat_id FROM messages "
            f"WHERE is_bot = 0 AND update_id > ? AND user_id IN ({ph}) "
            f"ORDER BY update_id ASC",
            [last_seen, *WATCH],
        )
        for r in rows:
            name = r["user_name"] or "?"
            text = (r["text"] or "").replace("\n", " ")
            cim = "TELEGRAM-GROUP" if (r["chat_id"] or 0) < 0 else "TELEGRAM"
            # Save FIRST: egyszer.py stops this process on the first matching line,
            # so a save after the print could be lost.
            last_seen = max(last_seen, r["update_id"])
            _save_seen(last_seen)
            print(f"{cim} | {name}: {text}", flush=True)
            emlek_kiir(text)
        if alerted:
            print("monitor-recovered: messages.db is readable again.", flush=True)
        consec_errors = 0
        alerted = False
        time.sleep(POLL_SLEEP)
    except Exception as e:
        consec_errors += 1
        if consec_errors >= ERROR_ALERT_AT and not alerted:
            print(
                f"monitor-error (persistent, {consec_errors}x): {type(e).__name__}: {e}",
                flush=True,
            )
            alerted = True
        time.sleep(10)
