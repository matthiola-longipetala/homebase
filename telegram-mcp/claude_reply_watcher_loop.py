"""claude_reply_watcher_loop.py — CONTINUOUS DB watcher (for the Claude Monitor tool).

The counterpart of claude_reply_watcher.py, but it does not exit after the first message: it prints
every new incoming (is_bot=0) message from the named recipients immediately (one stdout line = one
Monitor event) and keeps going. It does NOT call getUpdates (the sync daemon is the only consumer);
it only polls messages.db.

Usage: python claude_reply_watcher_loop.py [poll_s]
  On startup, baseline = the current highest message_id (old messages are not replayed).
"""
import sqlite3
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import beallitas  # from config.json: the DB path and the watched chats

DB = beallitas.db_path()
# Watched chats; unauthorized chat_ids are deliberately NOT in this list (their path is the MCP
# tool telegram_inspect_unauthorized, with auditing).
WATCH_CHATS = beallitas.figyelt_chatek()
poll_s = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def q(sql, args=()):
    c = sqlite3.connect(DB, timeout=10.0)
    c.row_factory = sqlite3.Row
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


CHATS_SQL = ",".join("?" * len(WATCH_CHATS))
baseline = 0
try:
    rows = q(f"SELECT MAX(message_id) AS m FROM messages WHERE chat_id IN ({CHATS_SQL})", WATCH_CHATS)
    baseline = rows[0]["m"] or 0
except Exception:
    pass
print(f"[watcher running, baseline={baseline}, poll {poll_s}s, threads: {len(WATCH_CHATS)}]", flush=True)

while True:
    try:
        rows = q(
            f"SELECT message_id, user_name, text, raw_json FROM messages "
            f"WHERE is_bot=0 AND message_id > ? AND chat_id IN ({CHATS_SQL}) ORDER BY message_id ASC",
            (baseline, *WATCH_CHATS),
        )
    except Exception:
        rows = []
    for r in rows:
        szoveg = r["text"] or ("[photo]" if '"photo"' in (r["raw_json"] or "") else "[non-text message]")
        print(f"[Telegram id={r['message_id']}] {r['user_name']}: {szoveg}", flush=True)
        baseline = r["message_id"]
    time.sleep(poll_s)
