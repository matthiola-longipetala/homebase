#!/usr/bin/env python3
"""csoport_tars_figyelo.py ("csoport-társ figyelő" = group-peer watcher) — with two Claudes, each on its
own Telegram bot, one Claude sees what the OTHER Claude wrote into the shared family group.

Why it exists: Telegram does not deliver one bot's group messages to another bot (tested, with
@-mentions too). Each bot does save what it sends into its own buffer database, though
(is_bot=1, chat_id = the group). This watcher opens the PEER's database READ-ONLY (mode=ro), and when
the peer bot has written a new line into the group it prints one event line and exits.
Launch it with egyszer.py, and restart it after every event, like the other watchers.

Output:  TELEGRAM-GROUP | <label>: <text>          (multi-line text joined with " ⏎ ")
State:   --state file (the largest date_ts + message_id already seen)

Usage (background Bash; one instance per Claude, each pointed at the OTHER one's database):
  PYTHONUTF8=1 python -u egyszer.py --minta "^(TELEGRAM|monitor-error)" -- \\
    python -u csoport_tars_figyelo.py --peer-db <peer telegram-mcp data>/messages.db \\
      --peer-user-id <peer bot's user_id> --group-chat-id <group chat_id> \\
      --state <my telegram-mcp data>/group_peer_last_seen.txt --label "Peer Claude"

Every argument can also come from the environment: PEER_TG_DB, PEER_BOT_USER_ID, TG_GROUP_CHAT_ID,
PEER_WATCH_STATE, PEER_LABEL. Add --no-recall to skip the optional [MEMORY] line (emlek_index).
"""
import argparse
import os
import sqlite3
import sys
import time

POLL = 15

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _emlek_betolt(kikapcsolva):
    """Optional associative recall (emlek_index): prints one [MEMORY] line after an event.
    Without the index (or with --no-recall) this is a no-op that never raises."""
    if kikapcsolva:
        return lambda _s: ""
    d = os.environ.get("EMLEK_INDEX_DIR")
    if not d:
        return lambda _s: ""
    try:
        sys.path.insert(0, d)
        from emlek_kliens import emlek_sor
        return emlek_sor
    except Exception:
        return lambda _s: ""


def args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    env = os.environ.get
    ap.add_argument("--peer-db", default=env("PEER_TG_DB"), help="the peer bot's messages.db (opened read-only)")
    ap.add_argument("--peer-user-id", type=int, default=env("PEER_BOT_USER_ID"), help="the peer bot's Telegram user_id")
    ap.add_argument("--group-chat-id", type=int, default=env("TG_GROUP_CHAT_ID"), help="the shared group's chat_id (negative)")
    ap.add_argument("--state", default=env("PEER_WATCH_STATE"), help="state file in MY data folder")
    ap.add_argument("--label", default=env("PEER_LABEL", "Peer Claude"))
    ap.add_argument("--no-recall", action="store_true")
    a = ap.parse_args()
    hiany = [n for n in ("peer_db", "peer_user_id", "group_chat_id", "state") if getattr(a, n) in (None, "")]
    if hiany:
        ap.error("missing: " + ", ".join("--" + n.replace("_", "-") for n in hiany))
    return a


def olvas_allapot(state):
    try:
        with open(state, encoding="utf-8") as f:
            ts, mid = f.read().split()
            return int(ts), int(mid)
    except Exception:
        return None


def ir_allapot(state, ts, mid):
    tmp = state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(f"{ts} {mid}")
    os.replace(tmp, state)


def kapcsolat(db):
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)


def uj_sorok(a, utolso):
    c = kapcsolat(a.peer_db)
    try:
        return c.execute(
            "SELECT date_ts, message_id, text FROM messages "
            "WHERE chat_id=? AND user_id=? AND (date_ts>? OR (date_ts=? AND message_id>?)) "
            "ORDER BY date_ts, message_id",
            (a.group_chat_id, a.peer_user_id, utolso[0], utolso[0], utolso[1]),
        ).fetchall()
    finally:
        c.close()


def main():
    a = args()
    emlek_sor = _emlek_betolt(a.no_recall)
    utolso = olvas_allapot(a.state)
    if utolso is None:
        # first start: everything already there counts as seen; only new lines wake you
        c = kapcsolat(a.peer_db)
        r = c.execute("SELECT COALESCE(MAX(date_ts),0), COALESCE(MAX(message_id),0) FROM messages "
                      "WHERE chat_id=? AND user_id=?", (a.group_chat_id, a.peer_user_id)).fetchone()
        c.close()
        utolso = (int(r[0]), int(r[1]))
        ir_allapot(a.state, *utolso)
    print(f"group-peer watcher started (peer DB, read-only), last seen: {utolso}", flush=True)
    while True:
        try:
            sorok = uj_sorok(a, utolso)
        except Exception as e:
            print(f"monitor-error | csoport_tars_figyelo: {type(e).__name__}: {e}", flush=True)
            time.sleep(POLL * 4)
            continue
        if sorok:
            # Save the state BEFORE printing: egyszer.py stops this process at the first matching line,
            # so a save after the print could be lost and the same message would wake you again.
            # Multi-line text goes on one line, so the stop doesn't cut it in half.
            for ts, mid, text in sorok:
                utolso = (int(ts), int(mid))
            ir_allapot(a.state, *utolso)
            for ts, mid, text in sorok:
                egy = " ⏎ ".join(x.strip() for x in (text or "").strip().splitlines() if x.strip())
                print(f"TELEGRAM-GROUP | {a.label}: {egy}", flush=True)
                try:
                    s = emlek_sor((text or "").strip())
                    if s:
                        print(s, flush=True)
                except Exception:
                    pass
            return 0
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
