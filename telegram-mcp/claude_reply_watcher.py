"""claude_reply_watcher.py — one-shot watcher for an interactive Claude conversation over Telegram.

telegram_sync_daemon writes incoming messages into messages.db every ~2 seconds
(it is the only getUpdates consumer). This script does NOT call getUpdates (no
409 conflict); it only polls the DB for a new incoming (is_bot=0) message with
message_id > baseline. On a match it prints it and exits (the background task wakes Claude).

Usage: python claude_reply_watcher.py <baseline_message_id> [timeout_s]
"""
import sqlite3, sys, time

# The Windows console uses cp1250 -> emoji/accented characters would crash. Switch to UTF-8 (errors='replace' as belt and braces).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import beallitas  # from config.json: the DB path and the owner's chat_id

DB = beallitas.db_path()
OWNER_CHAT = beallitas.chat_id()

baseline = int(sys.argv[1]) if len(sys.argv) > 1 else 0
timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 1800.0

start = time.time()
while time.time() - start < timeout:
    try:
        c = sqlite3.connect(DB, timeout=10.0)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT message_id, user_name, text, date_ts FROM messages "
            "WHERE is_bot=0 AND message_id > ? AND chat_id = ? "
            "ORDER BY message_id ASC",
            (baseline, OWNER_CHAT),
        ).fetchall()
        c.close()
    except Exception as e:
        rows = []
    if rows:
        for r in rows:
            print(f"[NEW MESSAGE id={r['message_id']}] {r['user_name']}: {r['text']}")
        sys.exit(0)
    time.sleep(3)

print("TIMEOUT: no reply arrived within the given time.")
sys.exit(2)
