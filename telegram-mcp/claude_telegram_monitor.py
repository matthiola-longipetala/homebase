"""claude_telegram_monitor.py — CONTINUOUS Telegram watcher for the Claude Monitor tool.

telegram_sync_daemon writes incoming messages into messages.db every ~2 seconds
(it is the ONLY getUpdates consumer). This script does NOT call getUpdates (no
409 conflict); it only polls the DB. It prints every NEW owner message (is_bot=0,
message_id > baseline) on ONE line (= one Monitor event), then CONTINUES
watching (updating the baseline) — persistent, it does not exit.

Related to claude_reply_watcher.py, but that one is one-shot (exits after 1 message); this one loops.

Usage: python claude_telegram_monitor.py [baseline_message_id]
"""
import sqlite3, sys, time

# The Windows console uses cp1250 -> emoji/accented characters would crash. Switch to UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import beallitas  # from config.json: the DB path and the owner's chat_id

DB = beallitas.db_path()
OWNER_CHAT = beallitas.chat_id()

baseline = int(sys.argv[1]) if len(sys.argv) > 1 else 0

while True:
    try:
        c = sqlite3.connect(DB, timeout=10.0)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT message_id, user_name, text FROM messages "
            "WHERE is_bot=0 AND message_id > ? AND chat_id = ? "
            "ORDER BY message_id ASC",
            (baseline, OWNER_CHAT),
        ).fetchall()
        c.close()
    except Exception:
        rows = []
    for r in rows:
        print(f"[TG id={r['message_id']}] {r['user_name']}: {r['text']}", flush=True)
        baseline = r["message_id"]
    time.sleep(3)
