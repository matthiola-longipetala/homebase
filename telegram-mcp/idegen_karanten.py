"""idegen_karanten.py ("idegen karantén" = unauthorized-sender quarantine) — protects against messages
from senders who are not on the allowlist.

Goal: the CONTENT of messages from anyone outside the allowed senders (config: allowed_user_ids)
must not sit readable in messages.db. The content is Fernet-encrypted into a quarantine
table and a placeholder is left in the original rows. While quarantining, the process
NEVER prints the content — only counts/IDs.

Modes:
  python idegen_karanten.py zar            — quarantine new unauthorized messages ("zar" = lock)
  python idegen_karanten.py lista          — quarantine metadata list, without content ("lista" = list)
  python idegen_karanten.py felold <id>    — decrypt ONE message to the SCREEN ("felold" = unlock)
                                             (audited! ONLY at the owner's request / in their presence)

The key: data/idegen_kulcs.key — it never leaves the machine; NEVER send it to Telegram or a chat.
"""
import sqlite3
import sys
import time
import os

BAZIS = os.path.dirname(os.path.abspath(__file__))
# With several bot instances, each one quarantines its own DB (TELEGRAM_KARANTEN_DB).
# Default: the same DB the server and the daemon use (beallitas.db_path(): next to the config file).
try:
    import beallitas as _beallitas
    _ALAP_DB = _beallitas.db_path()
except Exception:
    _ALAP_DB = os.path.join(BAZIS, "data", "messages.db")
DB = os.environ.get("TELEGRAM_KARANTEN_DB", _ALAP_DB)
# The Fernet key should live OUTSIDE the repository and every synced folder (TELEGRAM_KARANTEN_KULCS).
# Default: next to the DB (so, with the config in the secrets folder, the key is there too).
KULCS = os.environ.get("TELEGRAM_KARANTEN_KULCS", os.path.join(os.path.dirname(DB), "idegen_kulcs.key"))
import beallitas  # noqa: E402
ENGEDELYEZETT = beallitas.engedelyezett()
PLACEHOLDER = "[UNAUTHORIZED MESSAGE - QUARANTINED, content encrypted in the idegen_karanten table]"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from cryptography.fernet import Fernet


def kulcs_betolt():
    if not os.path.exists(KULCS):
        k = Fernet.generate_key()
        fd = os.open(KULCS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # owner-only on Linux/macOS
        with os.fdopen(fd, "wb") as f:
            f.write(k)
        print("[new quarantine key generated]")
    with open(KULCS, "rb") as f:
        return Fernet(f.read())


def db():
    c = sqlite3.connect(DB, timeout=15.0)
    c.row_factory = sqlite3.Row
    return c


def tabla_biztosit(c):
    c.execute(
        "CREATE TABLE IF NOT EXISTS idegen_karanten ("
        " message_id INTEGER PRIMARY KEY, chat_id INTEGER, user_id INTEGER,"
        " user_name TEXT, date_ts INTEGER, text_enc BLOB, raw_json_enc BLOB,"
        " karanten_ido TEXT)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS idegen_audit ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, ido TEXT, muvelet TEXT,"
        " message_id INTEGER, megjegyzes TEXT)"
    )


def audit(c, muvelet, message_id, megjegyzes=""):
    c.execute(
        "INSERT INTO idegen_audit (ido, muvelet, message_id, megjegyzes) VALUES (?,?,?,?)",
        (time.strftime("%Y-%m-%d %H:%M:%S"), muvelet, message_id, megjegyzes),
    )


def zar(csendes=False):
    """Quarantine new unauthorized messages. csendes=True ("quiet"): runs in daemon mode,
    only returns the number of quarantined messages and writes nothing to stdout."""
    f = kulcs_betolt() if not csendes else _kulcs_csendes()
    c = db()
    tabla_biztosit(c)
    ph = ",".join("?" * len(ENGEDELYEZETT))
    sorok = c.execute(
        f"SELECT message_id, chat_id, user_id, user_name, date_ts, text, raw_json "
        f"FROM messages WHERE is_bot=0 AND (user_id IS NULL OR user_id NOT IN ({ph})) "  # SENDER-based, not chat-based: in a group chat the chat_id is new but the senders are the same
        f"AND (text IS NULL OR text != ?)",
        (*ENGEDELYEZETT, PLACEHOLDER),
    ).fetchall()
    if not sorok:
        if not csendes:
            print("No new unauthorized messages to quarantine.")
        return 0
    with c:
        for r in sorok:
            t_enc = f.encrypt((r["text"] or "").encode("utf-8"))
            j_enc = f.encrypt((r["raw_json"] or "").encode("utf-8"))
            c.execute(
                "INSERT OR REPLACE INTO idegen_karanten "
                "(message_id, chat_id, user_id, user_name, date_ts, text_enc, raw_json_enc, karanten_ido) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (r["message_id"], r["chat_id"], r["user_id"], r["user_name"],
                 r["date_ts"], t_enc, j_enc, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            c.execute(
                "UPDATE messages SET text=?, raw_json=? WHERE message_id=?",
                (PLACEHOLDER, "{}", r["message_id"]),
            )
            audit(c, "quarantined", r["message_id"],
                  f"chat_id={r['chat_id']}" + (" [daemon-auto]" if csendes else ""))
            if not csendes:
                print(f"  quarantined: message_id={r['message_id']} chat_id={r['chat_id']} (content NOT shown)")
    if not csendes:
        print(f"DONE: {len(sorok)} unauthorized message(s) quarantined, placeholder left in the original rows.")
    return len(sorok)


def _kulcs_csendes():
    """Load the key without stdout noise (daemon mode)."""
    if not os.path.exists(KULCS):
        with open(KULCS, "wb") as fk:
            fk.write(Fernet.generate_key())
    with open(KULCS, "rb") as fk:
        return Fernet(fk.read())


def lista():
    c = db()
    tabla_biztosit(c)
    for r in c.execute(
        "SELECT message_id, chat_id, user_name, date_ts, karanten_ido, "
        "LENGTH(text_enc) AS enc_meret FROM idegen_karanten ORDER BY date_ts"
    ):
        print(
            f"id={r['message_id']} chat={r['chat_id']} name={r['user_name']!r} "
            f"ts={r['date_ts']} quarantined={r['karanten_ido']} enc={r['enc_meret']}B"
        )


def felold(message_id):
    print("!!! WARNING: audited decryption — only at the owner's request / in their presence !!!")
    f = kulcs_betolt()
    c = db()
    tabla_biztosit(c)
    r = c.execute(
        "SELECT * FROM idegen_karanten WHERE message_id=?", (message_id,)
    ).fetchone()
    if not r:
        print(f"Not in quarantine: {message_id}")
        return
    with c:
        audit(c, "decrypted_to_screen", message_id, "at operator request")
    print(f"--- message_id={message_id} chat={r['chat_id']} name={r['user_name']!r} ---")
    print("TEXT:", f.decrypt(r["text_enc"]).decode("utf-8", errors="replace"))


if __name__ == "__main__":
    mod = sys.argv[1] if len(sys.argv) > 1 else "lista"
    if mod == "zar":
        zar()
    elif mod == "lista":
        lista()
    elif mod == "felold" and len(sys.argv) > 2:
        felold(int(sys.argv[2]))
    else:
        print(__doc__)
