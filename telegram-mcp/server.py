"""
telegram-mcp — a Telegram bridge for one (or more) Claudes: SQLite buffer, per-recipient routing,
unauthorized-sender protocol ("idegen" = unauthorized sender) and an audit log.

What it does:
- **Sending** (`telegram_send`, `telegram_send_photo`): to a recipient named in the config's
  `recipients` block (default: `default_recipient`, or "owner" if that is not set).
- **Reading** (`telegram_get_messages`, `telegram_check_new`): reads ONLY the buffer when `sync=False`
  — so it is safe while a separate watcher (Monitor) holds the single getUpdates consumer.
  Reads are filtered by recipient: without the `who` parameter only the owner's thread is visible
  (other threads are shown only with an explicit `who="partner"` / `who="all"`).
- **Waiting** (`telegram_wait_for_reply`, `telegram_send_and_wait`): for one recipient's user_id.
- **Photos and documents** (`telegram_get_photo`, `telegram_get_document`): download to a local folder;
  never downloads a file from a non-allowed (unauthorized) sender.
- **Unauthorized-sender protocol** (level 2): for a message from a non-allowed sender only the metadata
  is visible and the content stays hidden; viewing it (`telegram_inspect_unauthorized`) is audit-logged.
- **Media is never lost**: document/voice/video messages are stored in the buffer with placeholder text
  (raw_json keeps the file_id), because anything dropped once the getUpdates cursor moves on is gone for good.

Config (`config.example.json`; the real one lives OUTSIDE the repository, located via the
`TELEGRAM_MCP_CONFIG` environment variable):
   {
     "bot_token": "<BOT_TOKEN>",
     "recipients": {
       "owner":   { "chat_id": "<OWNER_CHAT_ID>",   "user_id": "<OWNER_USER_ID>" },
       "partner": { "chat_id": "<PARTNER_CHAT_ID>", "user_id": "<PARTNER_USER_ID>" }
     },
     "default_recipient": "owner",
     "allowed_user_ids": ["<OWNER_USER_ID>", "<PARTNER_USER_ID>"],
     "timezone": "Europe/Berlin"
   }
`timezone` (optional): IANA name or fixed offset like "+02:00" for displayed times; default: system local.
Without a `recipients` block, an "owner" recipient is created from the legacy `chat_id` / `allowed_user_ids[0]`.
"""

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP

# --- Configuration ---

# The config should live OUTSIDE the repository (secrets folder); its location comes from the
# TELEGRAM_MCP_CONFIG environment variable, by default config.json next to the server (excluded by .gitignore).
CONFIG_PATH = os.environ.get("TELEGRAM_MCP_CONFIG", str(Path(__file__).with_name("config.json")))


def _load_config(require_chat_id: bool = True) -> dict:
    """Load bot token and recipient config from config.json.

    Args:
        require_chat_id: If False, only bot_token is required (for setup tool).
                         If a `recipients` block is present, a legacy top-level
                         chat_id is never required.
    """
    if not os.path.isfile(CONFIG_PATH):
        raise FileNotFoundError(
            f"Config not found: {CONFIG_PATH}\n"
            "Create config.json with: {\"bot_token\": \"...\", \"chat_id\": 123, \"allowed_user_ids\": [123]}"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    if "bot_token" not in cfg or not cfg["bot_token"]:
        raise ValueError("Missing or empty 'bot_token' in config.json")

    has_recipients = bool(cfg.get("recipients"))
    if require_chat_id and not has_recipients and ("chat_id" not in cfg or not cfg["chat_id"]):
        raise ValueError("Missing or empty 'chat_id' in config.json")

    # Fail-closed: with an empty allowlist every sender would count as unauthorized and the owner could
    # not be told apart from a stranger. Only the setup tool (require_chat_id=False) may run without it.
    if require_chat_id and not [u for u in (cfg.get("allowed_user_ids") or []) if u]:
        raise ValueError("Empty 'allowed_user_ids' in config.json — refusing to run. "
                         "Send the bot a message, run telegram_setup_chat_id, and add the printed user_id.")

    return cfg



@lru_cache(maxsize=1)
def _display_tz():
    """Time zone for the timestamps shown in tool output (read once per process).

    config.json `"timezone"`: an IANA name ("Europe/Berlin", "America/New_York", "Etc/GMT-2" =
    fixed UTC+2) or a fixed offset ("+02:00", "-05:30"). Missing/empty/invalid -> None, which means
    the system's local time zone. (On Windows IANA names need the `tzdata` package.)
    """
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            name = (json.load(f).get("timezone") or "").strip()
    except Exception:
        return None
    if not name:
        return None
    m = re.fullmatch(r"(?:UTC)?([+-])(\d{1,2})(?::?(\d{2}))?", name)
    if m:
        try:
            delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
            return timezone(-delta if m.group(1) == "-" else delta)
        except ValueError as e:
            import sys
            print(f"telegram-mcp: invalid offset {name!r} in config ({e}); using the system local time zone",
                  file=sys.stderr)
            return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:
        import sys
        print(f"telegram-mcp: unknown timezone {name!r} in config ({e}); using the system local time zone",
              file=sys.stderr)
        return None


def _local_dt(ts: float) -> datetime:
    """Unix timestamp -> aware datetime in the configured display time zone (default: system local)."""
    tz = _display_tz()
    if tz is None:
        return datetime.fromtimestamp(ts).astimezone()
    return datetime.fromtimestamp(ts, tz=tz)


def _api_url(token: str, method: str) -> str:
    """Build Telegram Bot API URL."""
    return f"https://api.telegram.org/bot{token}/{method}"


def _redact(text):
    """Best-effort: strip the bot token from any string before it leaves the server
    (tool error messages, audit details). Reads the token from config; never raises.
    Connect/retry errors from `requests` embed the token-bearing getUpdates/sendMessage
    URL — this keeps it out of the caller's context and the audit_log."""
    try:
        if not isinstance(text, str):
            text = str(text)
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            tok = json.load(f).get("bot_token")
        if tok:
            text = text.replace(tok, "<token-redacted>")
    except Exception:
        pass
    return text


# --- Recipient routing (v3.1) ---

def _resolve_recipients(cfg: dict) -> dict:
    """Return {name: {"chat_id": int|None, "user_id": int|None}} with a default.

    Backward compatible: if cfg has no 'recipients', synthesize an 'owner'
    recipient from the legacy chat_id / allowed_user_ids[0].
    """
    recips = cfg.get("recipients")
    if recips:
        return {
            name: {"chat_id": r.get("chat_id"), "user_id": r.get("user_id")}
            for name, r in recips.items()
        }
    legacy_chat = cfg.get("chat_id")
    allowed = cfg.get("allowed_user_ids") or []
    legacy_uid = allowed[0] if allowed else None
    return {"owner": {"chat_id": legacy_chat, "user_id": legacy_uid}}


def _default_recipient(cfg: dict) -> str:
    return cfg.get("default_recipient", "owner")


def _recipient(cfg: dict, to: str | None) -> dict:
    """Resolve a recipient by name (or the configured default).

    Returns the dict {"chat_id": ..., "user_id": ...}. Raises ValueError on an
    unknown recipient or one without a chat_id.
    """
    recips = _resolve_recipients(cfg)
    name = to or _default_recipient(cfg)
    if name not in recips:
        raise ValueError(
            f"Unknown recipient '{name}'. Known: {', '.join(sorted(recips))}."
        )
    r = recips[name]
    if not r.get("chat_id"):
        raise ValueError(f"Recipient '{name}' has no chat_id configured yet.")
    return r


def _scope_chat_id(cfg: dict, who: str | None) -> int | None:
    """chat_id to scope reads to, or None for the mixed/all view.

    who in (None, "all") -> None (no scoping). Otherwise the recipient's chat_id.
    """
    if not who or who == "all":
        return None
    return _recipient(cfg, who)["chat_id"]


# --- Sync daemon liveness (FAIL-CLOSED) ---
#
# WHY: a sync daemon that stops (e.g. a stale lock file after a reboot) must not stop
# silently — otherwise every consumer keeps reading a FROZEN buffer and gets a seemingly
# valid answer. Absence has to be stated explicitly too.
# The daemon (telegram_sync_daemon.py, lock v2) refreshes the lock file's `heartbeat`
# field every 30 s; this function reads it.

SYNC_LOCK_PATH = os.environ.get(
    "TELEGRAM_MCP_SYNC_LOCK",
    os.path.join(os.environ.get("TELEGRAM_SYNC_DATA_DIR")
                 or os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), "data"), "sync_daemon.lock"),
)
_SYNC_HB_STALE_SEC = 300          # 5 minutes — ten times the 30 s heartbeat


def _sync_daemon_health() -> str | None:
    """None if the daemon is provably alive. Otherwise a WARNING line.

    NEVER raises and NEVER gives a false green checkmark: if the measurement itself
    cannot run, it says so."""
    try:
        if not os.path.isfile(SYNC_LOCK_PATH):
            return ("⚠️ SYNC DAEMON: no lock file → probably NOT RUNNING. "
                    "The buffer is only refreshed if this call syncs by itself (sync=True).")
        raw = open(SYNC_LOCK_PATH, encoding="utf-8").read().strip()
        if not raw.startswith("{"):
            return ("⚠️ SYNC DAEMON: old-format lock file (PID only) → liveness cannot be measured. "
                    "The daemon may be running, but this CANNOT be confirmed from here.")
        rec = json.loads(raw)
        hb = rec.get("heartbeat")
        if hb is None:
            return "⚠️ SYNC DAEMON: the lock file has no heartbeat field → liveness cannot be measured."
        age = int(time.time()) - int(hb)
        if age > _SYNC_HB_STALE_SEC:
            perc = age // 60
            return (f"⚠️ SYNC DAEMON: last heartbeat {perc} minutes ago (pid {rec.get('pid')}) → "
                    "VERY LIKELY STOPPED. While it is stopped, the Telegram buffer is not filled: "
                    "the list below may be OLDER than what has arrived since. "
                    "Action: restart the daemon (pythonw telegram_sync_daemon.py).")
        return None
    except Exception as e:
        return (f"⚠️ SYNC DAEMON: the liveness check itself did not run "
                f"({type(e).__name__}) → buffer freshness is NOT verified.")


def _with_sync_health(body: str) -> str:
    """Puts the daemon warning at the START of the response. Leaves the body untouched."""
    try:
        warn = _sync_daemon_health()
    except Exception:
        warn = "⚠️ SYNC DAEMON: the liveness check failed with an error."
    return f"{warn}\n\n{body}" if warn else body


# --- SQLite layer ---

def _db_path(cfg: dict) -> str:
    """Resolve the SQLite database path.

    Precedence (the same rule as beallitas.db_path()):
        0. the TELEGRAM_MCP_DB environment variable
        1. cfg["db_path"] (absolute path from config.json)
        2. <config_dir>/data/messages.db (default)
    """
    custom = os.environ.get("TELEGRAM_MCP_DB") or cfg.get("db_path")
    if custom:
        return custom
    config_dir = os.path.dirname(os.path.abspath(CONFIG_PATH))
    return os.path.join(config_dir, "data", "messages.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    update_id   INTEGER PRIMARY KEY,
    message_id  INTEGER,
    chat_id     INTEGER,
    user_id     INTEGER,
    user_name   TEXT,
    is_bot      INTEGER NOT NULL DEFAULT 0,
    text        TEXT,
    date_ts     INTEGER NOT NULL,
    inserted_at INTEGER NOT NULL,
    raw_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_date_ts ON messages(date_ts);
CREATE INDEX IF NOT EXISTS idx_messages_chat    ON messages(chat_id, date_ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ts   INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    details    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_event_ts ON audit_log(event_ts);
"""


def _get_db(cfg: dict) -> sqlite3.Connection:
    """Open (and initialize if needed) the SQLite database."""
    path = _db_path(cfg)
    Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def _set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def _log_audit(conn: sqlite3.Connection, event_type: str, details: dict | None = None) -> None:
    """Append an audit event. Does NOT commit — caller is responsible.
    The serialized details are token-redacted before storage."""
    conn.execute(
        "INSERT INTO audit_log(event_ts, event_type, details) VALUES (?, ?, ?)",
        (int(time.time()), event_type, _redact(json.dumps(details or {}, ensure_ascii=False))),
    )


def _audit_safe(event_type: str, attempt_details: dict, extra: dict | None = None) -> None:
    """Best-effort audit logging — never raises.

    Opens its own short-lived DB connection so it works in any code path,
    including ones where the main conn has already been closed or never
    opened. Used by tools that must not double-fail just because audit
    logging itself ran into trouble (e.g. config missing, DB locked).

    Args:
        event_type: canonical names are listed in the telegram_send docstring.
        attempt_details: per-attempt context (parse_mode, text_len, text_preview).
        extra: outcome-specific fields (message_id, sent_ts, error_type, error).
    """
    try:
        cfg_local = _load_config()
        conn_local = _get_db(cfg_local)
        try:
            payload = {**attempt_details, **(extra or {})}
            _log_audit(conn_local, event_type, payload)
            conn_local.commit()
        finally:
            conn_local.close()
    except Exception:
        pass  # best-effort: never let audit logging break the caller


# --- Telegram API helpers ---

def _send_message(token: str, chat_id: int, text: str, parse_mode: str = "Markdown") -> dict:
    url = _api_url(token, "sendMessage")
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")

    return data["result"]


# Keep-alive Session for getUpdates. Without it every call builds a NEW TCP+TLS
# connection (CA bundle load + handshake ≈ 0.2 s CPU per request) — measured on the sync
# daemon: 3 requests 0.64 s CPU → 0.02 s with a Session. The Session is created lazily
# (nothing is opened at import time); on errors, requests rebuilds the connection.
_UPDATES_SESSION: "requests.Session | None" = None


def _updates_session() -> "requests.Session":
    global _UPDATES_SESSION
    if _UPDATES_SESSION is None:
        _UPDATES_SESSION = requests.Session()
    return _UPDATES_SESSION


def _get_updates(token: str, offset: int | None = None, timeout: int = 2) -> list[dict]:
    url = _api_url(token, "getUpdates")
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset

    resp = _updates_session().get(url, params=params, timeout=timeout + 5)
    resp.raise_for_status()
    data = resp.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")

    return data.get("result", [])


def _get_file_path(token: str, file_id: str) -> str:
    """Resolve a Telegram file_id to a server-side file_path via getFile."""
    url = _api_url(token, "getFile")
    resp = requests.get(url, params={"file_id": file_id}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")
    return data["result"]["file_path"]


def _download_telegram_file(token: str, file_path: str, dest: str) -> str:
    """Download a Telegram file to `dest`. NOTE: the URL embeds the bot token —
    never log or return the URL itself, only the local destination path."""
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    Path(os.path.dirname(dest)).mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(resp.content)
    return dest


# v3.4: media types Telegram can send. THE ORDER MATTERS:
# an animation message ALSO has a `document` field, and a video_note looks similar
# to a `video` — so the more specific type comes first. Otherwise every GIF
# would land in the buffer as a "document".
_MEDIA_KINDS = (
    ("animation", "animation"),
    ("video_note", "video message"),
    ("video", "video"),
    ("voice", "voice message"),
    ("audio", "audio file"),
    ("document", "document"),
    ("sticker", "sticker"),
)


def _human_size(n) -> str | None:
    """Human-readable size (formatted with a decimal comma), or None."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            if unit == "B":
                return f"{n:.0f} B"
            return f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return None


def _media_info(msg: dict) -> dict | None:
    """The message's FIRST (most specific) media attachment, or None.

    `photo` is DELIBERATELY not included: it is a list (of PhotoSizes), has its own
    handler (`_largest_photo_file_id`) and its own tool (`telegram_get_photo`).
    """
    for key, label in _MEDIA_KINDS:
        obj = msg.get(key)
        if not obj or not isinstance(obj, dict):
            continue
        return {
            "kind": key,
            "label": label,
            "file_id": obj.get("file_id"),
            "file_name": obj.get("file_name"),
            "size": obj.get("file_size"),
            "mime": obj.get("mime_type"),
        }
    return None


def _message_display_text(msg: dict) -> str | None:
    """Text to store for a message. Photos/media get a placeholder (with caption
    if present) so they are NOT dropped — raw_json keeps the file_ids.
    Returns None for messages we intentionally skip (no text, no media, no caption).

    ⚠️ v3.4 — WHY THIS FUNCTION IS CRITICAL: its caller
    (`_sync_buffer_to_sqlite`) does `continue` on None, BUT still advances the
    getUpdates cursor. Whatever is classified as None here is not "kept for later",
    it is LOST FOR GOOD. Up to v3.3 every document/voice/video vanished this way.
    """
    text = msg.get("text")
    if text:
        return text
    caption = (msg.get("caption") or "").strip()
    if msg.get("photo"):
        return ("[photo] " + caption).strip() if caption else "[photo]"
    media = _media_info(msg)
    if media:
        name = media.get("file_name")
        head = f"{media['label']}: {name}" if name else media["label"]
        size = _human_size(media.get("size"))
        if size:
            head = f"{head} · {size}"
        head = f"[{head}]"
        return f"{head} {caption}" if caption else head
    if caption:
        return caption
    return None


def _largest_photo_file_id(msg: dict) -> str | None:
    """file_id of the highest-resolution PhotoSize in a message, or None."""
    photos = msg.get("photo") or []
    if not photos:
        return None
    best = max(photos, key=lambda p: (p.get("width", 0) or 0) * (p.get("height", 0) or 0))
    return best.get("file_id")


def _images_dir(cfg: dict) -> str:
    """Directory for downloaded images (default <config_dir>/data/images)."""
    custom = cfg.get("images_path")
    if custom:
        return custom
    return os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), "data", "images")


def _documents_dir(cfg: dict) -> str:
    """Directory for downloaded documents.

    Precedence:
        1. cfg["documents_path"]
        2. a SIBLING of `images_path` (<images_path>/../documents)
        3. <config_dir>/data/documents

    ⚠️ WHY BRANCH 2 IS NEEDED: when config.json lives in a separate secrets folder,
    <config_dir> is NOT the project folder but the SECRETS STORE. `_images_dir`
    avoids this only because the config has an explicit `images_path`. Without
    `documents_path`, branch 3 would download the user's documents next to the keys --
    with two harms: (a) user data mixes into the secrets store, (b) another client
    instance may not be able to read files from there (file access may be limited to
    connected folders), so the tool would only half work for that client.
    More generally: when a path root moves, every path DERIVED from it must be
    re-checked -- the old ones were already protected by their own explicit
    config keys, the NEW ones by nothing.
    """
    custom = cfg.get("documents_path")
    if custom:
        return custom
    imgs = cfg.get("images_path")
    if imgs:
        # ⚠️ DELIBERATELY NOT os.path.dirname: the server runs on Windows, but the
        # test environment may be Linux, where os.path does not treat "\\" as a
        # separator -- there dirname() would return an empty string and the bug
        # would SILENTLY pass verification. Manual, separator-agnostic split.
        base = str(imgs).replace("\\", "/").rstrip("/")
        parent = base.rsplit("/", 1)[0] if "/" in base else base
        # normpath: the manual split leaves "/", os.path.join on Windows adds "\"
        # -> a mixed-separator path (works, but ugly and easy to misread).
        return os.path.normpath(os.path.join(parent, "documents"))
    return os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), "data", "documents")


# The Telegram-supplied `file_name` is EXTERNAL, UNTRUSTED DATA (written by the sender).
# It is sanitized against path traversal and surprise extensions. Accented Latin letters
# (e.g. Hungarian) are kept on purpose.
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._\-áéíóöőúüűÁÉÍÓÖŐÚÜŰ ]+")


def _safe_filename(name, fallback_ext: str = "") -> str:
    """Sanitized file name without path separators (max 120 characters)."""
    default = "document" + (fallback_ext or "")
    if not name:
        return default
    base = os.path.basename(str(name).replace("\\", "/").rstrip("/"))
    base = _SAFE_NAME_RE.sub("_", base).strip(" .")
    if not base or set(base) <= {"_"}:
        return default
    return base[:120]


def _get_me(token: str) -> dict:
    url = _api_url(token, "getMe")
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data.get('description', 'unknown')}")
    return data["result"]


def _get_me_cached(cfg: dict, conn: sqlite3.Connection) -> tuple[int | None, str]:
    """Return (bot_user_id, bot_name), caching via meta table."""
    cached_id = _get_meta(conn, "bot_user_id")
    cached_name = _get_meta(conn, "bot_name")
    if cached_id and cached_name:
        try:
            return int(cached_id), cached_name
        except (TypeError, ValueError):
            pass
    try:
        me = _get_me(cfg["bot_token"])
        bot_id = me.get("id")
        bot_name = me.get("first_name") or "Claude"
        if bot_id:
            _set_meta(conn, "bot_user_id", bot_id)
            _set_meta(conn, "bot_name", bot_name)
            conn.commit()
            return int(bot_id), bot_name
    except Exception:
        pass
    return None, "Claude"


def _persist_bot_message(
    cfg: dict,
    conn: sqlite3.Connection,
    chat_id: int,
    message_id: int,
    text: str,
    date_ts: int | None = None,
) -> bool:
    """Insert an outgoing (bot) message into SQLite.

    Uses update_id = -message_id to avoid collision with positive getUpdates ids.
    """
    if date_ts is None:
        date_ts = int(time.time())
    bot_user_id, bot_name = _get_me_cached(cfg, conn)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO messages
            (update_id, message_id, chat_id, user_id, user_name,
             is_bot, text, date_ts, inserted_at, raw_json)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            -int(message_id),
            int(message_id),
            int(chat_id),
            bot_user_id,
            bot_name,
            text,
            int(date_ts),
            int(time.time()),
            None,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def _sync_buffer_to_sqlite(cfg: dict, conn: sqlite3.Connection | None = None, long_poll: int = 0) -> int:
    """Pull new updates from the Bot API and insert into SQLite (idempotent).

    Stores ALL messages — allowed and non-allowed alike. Filtering for display
    happens at query/formatter level, not at storage.
    """
    close_after = False
    if conn is None:
        conn = _get_db(cfg)
        close_after = True

    try:
        last_raw = _get_meta(conn, "last_sync_update_id", "0") or "0"
        try:
            last_id = int(last_raw)
        except ValueError:
            last_id = 0
        offset = (last_id + 1) if last_id > 0 else None

        updates = _get_updates(cfg["bot_token"], offset=offset, timeout=long_poll)
        if not updates:
            _set_meta(conn, "last_sync_ts", int(time.time()))
            conn.commit()
            return 0

        inserted = 0
        max_id = last_id
        for update in updates:
            uid = update.get("update_id")
            if uid is None:
                continue
            if uid > max_id:
                max_id = uid
            msg = update.get("message")
            if not msg:
                continue
            display_text = _message_display_text(msg)
            if display_text is None:
                continue
            sender = msg.get("from", {}) or {}
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (update_id, message_id, chat_id, user_id, user_name,
                     is_bot, text, date_ts, inserted_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    msg.get("message_id"),
                    (msg.get("chat") or {}).get("id"),
                    sender.get("id"),
                    sender.get("first_name"),
                    1 if sender.get("is_bot") else 0,
                    display_text,
                    msg.get("date"),
                    int(time.time()),
                    json.dumps(update, ensure_ascii=False),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1

        if max_id > last_id:
            _set_meta(conn, "last_sync_update_id", max_id)
        _set_meta(conn, "last_sync_ts", int(time.time()))
        conn.commit()
        return inserted
    finally:
        if close_after:
            conn.close()


# --- Query helpers ---

def _is_unauthorized(row: sqlite3.Row, allowed_ids: list[int] | None) -> bool:
    """True if this row represents a non-bot message from a user_id outside allowed_ids."""
    if bool(row["is_bot"]):
        return False
    if not allowed_ids:
        return False
    if row["user_id"] is None:
        return False
    return row["user_id"] not in allowed_ids


def _format_message_row(row: sqlite3.Row, allowed_ids: list[int] | None = None) -> str:
    """Format a messages row.

    Allowed user / bot: normal '[MM-DD HH:MM] 🤖/👤 Name: text' line.
    Unauthorized user (level 2): metadata-only line, content HIDDEN.
    """
    ts = _local_dt(row["date_ts"])
    ts_str = ts.strftime("%m-%d %H:%M")
    is_bot = bool(row["is_bot"])
    name = row["user_name"] or "?"
    text = row["text"] or ""

    if _is_unauthorized(row, allowed_ids):
        # Level 2: sender info visible, content hidden.
        return (
            f"[{ts_str}] ⚠️ UNAUTHORIZED "
            f"[user_id={row['user_id']} \"{name}\", "
            f"{len(text)} characters, content hidden — "
            f"use telegram_inspect_unauthorized() if you really need to see it]"
        )

    role = "🤖 Claude" if is_bot else f"👤 {name}"
    # Soft truncation only at Telegram's max (~4096) — preserves full message content
    # for read tools, while marking any overflow explicitly so info-loss is never silent.
    if len(text) <= 4000:
        display = text
    else:
        display = text[:4000] + f"... [+{len(text) - 4000} char truncated]"
    return f"[{ts_str}] {role}: {display}"


def _select_first_allowed_after(
    conn: sqlite3.Connection,
    allowed: list[int],
    cutoff_ts: int,
) -> sqlite3.Row | None:
    """Earliest non-bot allowed-user message after cutoff_ts (or None).

    `allowed` may be narrowed by the caller to a single recipient's user_id to
    scope a wait to that person.
    """
    if allowed:
        placeholders = ",".join("?" * len(allowed))
        sql = (
            f"SELECT * FROM messages "
            f"WHERE is_bot = 0 AND user_id IN ({placeholders}) "
            f"AND date_ts > ? "
            f"ORDER BY date_ts ASC LIMIT 1"
        )
        params = [*allowed, cutoff_ts]
    else:
        sql = (
            "SELECT * FROM messages "
            "WHERE is_bot = 0 AND date_ts > ? "
            "ORDER BY date_ts ASC LIMIT 1"
        )
        params = [cutoff_ts]
    return conn.execute(sql, params).fetchone()


# --- MCP Server ---

mcp = FastMCP(name="telegram-mcp")


@mcp.tool()
def telegram_send(message: str, parse_mode: str = "Markdown", to: str = "owner") -> str:
    """Send a message via the Telegram bot to a configured recipient.

    The outgoing message is persisted to SQLite (is_bot=1, with the recipient's
    chat_id) so later reads include Claude's own side of the conversation.

    Every call leaves a trail in the audit_log table (event_types:
    telegram_send_ok, telegram_send_failed, telegram_send_persist_failed,
    telegram_send_config_error, telegram_send_network_error,
    telegram_send_error).

    Args:
        message: The message text. Supports Markdown.
        parse_mode: "Markdown" (default), "HTML", or "" for plain text.
        to: Recipient name (default "owner"). E.g. "partner" once configured.

    Returns:
        Confirmation with message_id, or error description.
    """
    attempt_details = {
        "parse_mode": parse_mode,
        "text_len": len(message),
        "text_preview": message[:200],
        "to": to,
    }
    try:
        cfg = _load_config()
        rec = _recipient(cfg, to)
        chat_id = rec["chat_id"]
        result = _send_message(cfg["bot_token"], chat_id, message, parse_mode)
        msg_id = result.get("message_id", "?")
        sent_date_ts = result.get("date", int(time.time()))

        try:
            conn = _get_db(cfg)
            try:
                _persist_bot_message(
                    cfg, conn,
                    chat_id=chat_id,
                    message_id=msg_id if isinstance(msg_id, int) else int(msg_id),
                    text=message,
                    date_ts=sent_date_ts,
                )
            finally:
                conn.close()
        except Exception as persist_err:
            _audit_safe("telegram_send_persist_failed", attempt_details, {
                "message_id": msg_id,
                "sent_ts": sent_date_ts,
                "error_type": type(persist_err).__name__,
                "error": str(persist_err),
            })
            return f"✅ Message sent (id: {msg_id}) ⚠️ but DB persist failed: {_redact(str(persist_err))}"

        _audit_safe("telegram_send_ok", attempt_details, {
            "message_id": msg_id,
            "sent_ts": sent_date_ts,
        })
        return f"✅ Message sent to {to} (id: {msg_id})"
    except FileNotFoundError as e:
        _audit_safe("telegram_send_config_error", attempt_details, {"error": str(e)})
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        _audit_safe("telegram_send_config_error", attempt_details, {"error": str(e)})
        return f"❌ Recipient error: {_redact(str(e))}"
    except requests.RequestException as e:
        _audit_safe("telegram_send_network_error", attempt_details, {"error": str(e)})
        return f"❌ Network error: {_redact(str(e))}"
    except RuntimeError as e:
        _audit_safe("telegram_send_failed", attempt_details, {
            "error_type": "RuntimeError",
            "error": str(e),
        })
        return f"❌ Error: {_redact(str(e))}"
    except Exception as e:
        _audit_safe("telegram_send_error", attempt_details, {
            "error_type": type(e).__name__,
            "error": str(e),
        })
        return f"❌ Error: {_redact(str(e))}"


# WHY HERE: the key stays in the config INSIDE the MCP; Claude never reads it out
# (calling sendPhoto directly would have required Claude to read the token).
# LIMIT: image files only (extension + size), so this is not a general file-exfiltration path.
_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_PHOTO_MAX_BYTES = 10 * 1024 * 1024  # Telegram's sendPhoto limit


def _send_photo(token: str, chat_id: int, path: str, caption: str = "") -> dict:
    url = _api_url(token, "sendPhoto")
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    with open(path, "rb") as fh:
        resp = requests.post(url, data=data, files={"photo": (os.path.basename(path), fh)}, timeout=60)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body.get('description', 'unknown')}")
    return body["result"]


@mcp.tool()
def telegram_send_photo(path: str, caption: str = "", to: str = "owner") -> str:
    """Send a local IMAGE file via the Telegram bot to a configured recipient.

    Only image files are accepted (.jpg/.jpeg/.png/.webp, at most 10 MB) — this is
    not a general file-sending tool. The bot token stays inside the MCP.
    The outgoing photo is persisted to SQLite as "[photo] <caption>" (is_bot=1), so
    any watcher reading this DB sees it too.

    Args:
        path: Absolute path of the local image file.
        caption: Optional caption (plain text, max 1024 characters).
        to: Recipient name (default "owner"), e.g. "partner" or "group".

    Returns:
        Confirmation with message_id, or error description.
    """
    attempt_details = {"path": path, "caption_len": len(caption), "to": to}
    try:
        p = Path(path)
        if p.suffix.lower() not in _PHOTO_EXTS:
            raise ValueError(f"Not an image file (allowed: {', '.join(sorted(_PHOTO_EXTS))}): {p.name}")
        if not p.is_file():
            raise ValueError(f"File not found: {path}")
        size = p.stat().st_size
        if size > _PHOTO_MAX_BYTES:
            raise ValueError(f"Image too large ({_human_size(size)}; max 10 MB): {p.name}")
        if len(caption) > 1024:
            raise ValueError("Caption too long (max 1024 characters)")

        cfg = _load_config()
        rec = _recipient(cfg, to)
        chat_id = rec["chat_id"]
        result = _send_photo(cfg["bot_token"], chat_id, str(p), caption)
        msg_id = result.get("message_id", "?")
        sent_date_ts = result.get("date", int(time.time()))
        db_text = f"[photo] {caption}".strip()

        try:
            conn = _get_db(cfg)
            try:
                _persist_bot_message(
                    cfg, conn,
                    chat_id=chat_id,
                    message_id=msg_id if isinstance(msg_id, int) else int(msg_id),
                    text=db_text,
                    date_ts=sent_date_ts,
                )
            finally:
                conn.close()
        except Exception as persist_err:
            _audit_safe("telegram_send_photo_persist_failed", attempt_details, {
                "message_id": msg_id, "error_type": type(persist_err).__name__, "error": str(persist_err),
            })
            return f"✅ Photo sent (id: {msg_id}) ⚠️ but DB persist failed: {_redact(str(persist_err))}"

        _audit_safe("telegram_send_photo_ok", attempt_details, {"message_id": msg_id, "sent_ts": sent_date_ts})
        return f"✅ Photo sent to {to} (id: {msg_id})"
    except FileNotFoundError as e:
        _audit_safe("telegram_send_photo_config_error", attempt_details, {"error": str(e)})
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        _audit_safe("telegram_send_photo_rejected", attempt_details, {"error": str(e)})
        return f"❌ {_redact(str(e))}"
    except requests.RequestException as e:
        _audit_safe("telegram_send_photo_network_error", attempt_details, {"error": str(e)})
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        _audit_safe("telegram_send_photo_error", attempt_details, {"error_type": type(e).__name__, "error": str(e)})
        return f"❌ Error: {_redact(str(e))}"


def _sync_soft(cfg, conn, long_poll: int) -> bool:
    """Best-effort buffer sync. True if getUpdates actually ran.

    ARCHITECTURE RULE: there can be ONE getUpdates consumer per bot, and it is held
    by `telegram_sync_daemon.py` -- a standalone pythonw process started at logon,
    independent of Claude Desktop and Claude Code. It writes EVERY message into the
    shared SQLite buffer. Every other consumer reads FROM THE BUFFER (e.g. a Monitor
    watcher that only reads SQLite, so it never conflicts).

    Lesson learned: if the wait loop of `telegram_wait_for_reply` / `telegram_send_and_wait`
    calls `_sync_buffer_to_sqlite` unguarded, a 409 Conflict (= the daemon is ALIVE,
    everything is fine) bubbles up as a `requests.RequestException` and KILLS the wait
    with "❌ Network error" -- the message went out, but the second half of the loop broke.

    Here a 409 is NOT an error but a healthy state: we fall back to buffer mode.
    """
    try:
        _sync_buffer_to_sqlite(cfg, conn=conn, long_poll=long_poll)
        return True
    except Exception:
        return False


_BUFFER_MODE_NOTE = (
    " _(buffer mode: getUpdates is held by the live sync daemon/Monitor — this is NORMAL. "
    "The reply still lands in the buffer; the next `telegram_get_messages(sync=False)` will see it.)_"
)


@mcp.tool()
def telegram_wait_for_reply(timeout_seconds: int = 300, who: str = "owner") -> str:
    """Wait for a specific recipient's reply (blocking poll, DB-backed).

    Unauthorized (non-allowed) messages DO get stored, but they do NOT wake this tool.
    The wait is scoped to one recipient's user_id (default "owner").

    Args:
        timeout_seconds: Max seconds to wait (default 300).
        who: Recipient name to wait on (default "owner").

    Returns:
        The reply text, or a timeout message.
    """
    try:
        cfg = _load_config()
        rec = _recipient(cfg, who)
        wait_uid = rec.get("user_id")
        allowed = [wait_uid] if wait_uid else (cfg.get("allowed_user_ids") or [])
        cutoff_ts = int(time.time())
        deadline = cutoff_ts + timeout_seconds

        conn = _get_db(cfg)
        try:
            sync_ok = _sync_soft(cfg, conn, 0)

            while time.time() < deadline:
                sync_ok = _sync_soft(cfg, conn, 2 if sync_ok else 0)
                row = _select_first_allowed_after(conn, allowed, cutoff_ts)
                if row:
                    name = row["user_name"] or who
                    return f"{name}: {row['text']}"
                time.sleep(0.5 if sync_ok else 2.0)

            note = "" if sync_ok else _BUFFER_MODE_NOTE
            return (f"⏱️ TIMEOUT: {who} did not reply within {timeout_seconds} "
                    f"seconds.{note}")
        finally:
            conn.close()
    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        return f"❌ Recipient error: {_redact(str(e))}"
    except requests.RequestException as e:
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


@mcp.tool()
def telegram_send_and_wait(message: str, timeout_seconds: int = 300, parse_mode: str = "Markdown", to: str = "owner") -> str:
    """Send a message and wait for that recipient's reply (DB-backed).

    Outgoing message is persisted (is_bot=1). Unauthorized messages are stored but
    do NOT wake this tool. The wait is scoped to the `to` recipient's user_id.

    ⚠️ SENDING AND WAITING HAVE SEPARATE FATES. If the result is "⏱️ TIMEOUT", the
    message WAS STILL sent -- a later session closes the loop
    (e.g. via `telegram_get_messages(sync=False)`). The wait branch also works in
    buffer mode, when the live daemon/Monitor holds getUpdates (409).

    Args:
        message: The question/message to send.
        timeout_seconds: Max seconds to wait (default 300).
        parse_mode: "Markdown" (default), "HTML", or "".
        to: Recipient name (default "owner").

    Returns:
        The reply, or a timeout message.
    """
    try:
        cfg = _load_config()
        rec = _recipient(cfg, to)
        chat_id = rec["chat_id"]
        wait_uid = rec.get("user_id")
        allowed = [wait_uid] if wait_uid else (cfg.get("allowed_user_ids") or [])

        conn = _get_db(cfg)
        try:
            sync_ok = _sync_soft(cfg, conn, 0)

            sent = _send_message(cfg["bot_token"], chat_id, message, parse_mode)
            msg_id = sent.get("message_id")
            sent_date_ts = sent.get("date", int(time.time()))
            if msg_id is not None:
                try:
                    _persist_bot_message(
                        cfg, conn,
                        chat_id=chat_id,
                        message_id=int(msg_id),
                        text=message,
                        date_ts=sent_date_ts,
                    )
                except Exception:
                    pass

            cutoff_ts = int(time.time())
            deadline = cutoff_ts + timeout_seconds

            while time.time() < deadline:
                sync_ok = _sync_soft(cfg, conn, 2 if sync_ok else 0)
                row = _select_first_allowed_after(conn, allowed, cutoff_ts)
                if row:
                    name = row["user_name"] or to
                    return f"{name}: {row['text']}"
                time.sleep(0.5 if sync_ok else 2.0)

            note = "" if sync_ok else _BUFFER_MODE_NOTE
            return (f"⏱️ TIMEOUT: {to} did not reply within {timeout_seconds} "
                    f"seconds. ⚠️ THE MESSAGE WAS SENT.{note}")
        finally:
            conn.close()
    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        return f"❌ Recipient error: {_redact(str(e))}"
    except requests.RequestException as e:
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


@mcp.tool()
def telegram_get_messages(limit: int = 10, who: str = "owner", sync: bool = True) -> str:
    """Read recent messages from SQLite for one recipient's thread (+ bot replies).

    Scoped by recipient chat_id. Default "owner" — so scheduled tasks never see the partner's thread unless they explicitly pass who="partner"
    or who="all" (a deliberate privacy default).

    Unauthorized (non-allowed) messages are shown as a '⚠️ UNAUTHORIZED [metadata]' line
    with the raw content HIDDEN. Use `telegram_inspect_unauthorized` if you
    need to see the actual text (audit-logged).

    Args:
        limit: Number of most-recent messages (default 10).
        who: Recipient thread to read: "owner" (default), "partner", or "all".

    Returns:
        Formatted list, oldest first.
    """
    try:
        cfg = _load_config()
        allowed = cfg.get("allowed_user_ids") or []
        scope_chat = _scope_chat_id(cfg, who)

        conn = _get_db(cfg)
        try:
            if sync:
                try:
                    _sync_buffer_to_sqlite(cfg, conn=conn, long_poll=0)
                except Exception:
                    pass  # best-effort: a live consumer holds getUpdates → read from the buffer

            clauses = []
            params: list = []
            if scope_chat is not None:
                clauses.append("chat_id = ?")
                params.append(scope_chat)
            where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = (
                f"SELECT * FROM messages {where_sql} "
                f"ORDER BY date_ts DESC LIMIT ?"
            )
            rows = conn.execute(sql, [*params, max(1, limit)]).fetchall()

            if not rows:
                return _with_sync_health("📭 No messages in the SQLite buffer.")

            rows = list(reversed(rows))
            return _with_sync_health(
                "\n".join(_format_message_row(r, allowed_ids=allowed) for r in rows))
        finally:
            conn.close()
    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        return f"❌ Recipient error: {_redact(str(e))}"
    except requests.RequestException as e:
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


@mcp.tool()
def telegram_check_new(since_ts: int | None = None, include_bot: bool = False, limit: int = 50, who: str = "owner", sync: bool = True) -> str:
    """List messages with date_ts > since_ts. With sync=False reads SQLite only (no getUpdates → multi-consumer safe); with sync=True it also pulls fresh (best-effort).

    Scoped by recipient chat_id. Default "owner" — the partner's thread is excluded
    unless who="partner"/"all" (a deliberate privacy default).

    Unauthorized messages appear as metadata-only '⚠️ UNAUTHORIZED' lines with content
    hidden. The header summary reports how many of the returned messages are
    UNAUTHORIZED.

    Args:
        since_ts: Unix timestamp. None/0 = last 24h.
        include_bot: If True, includes Claude's own messages too.
        limit: Max messages (default 50, oldest-first).
        who: Recipient thread to read: "owner" (default), "partner", or "all".

    Returns:
        Formatted list, or a sentinel.
    """
    try:
        cfg = _load_config()
        allowed = cfg.get("allowed_user_ids") or []
        scope_chat = _scope_chat_id(cfg, who)

        if not since_ts or since_ts <= 0:
            since_ts = int(time.time()) - 24 * 3600

        conn = _get_db(cfg)
        try:
            if sync:
                try:
                    _sync_buffer_to_sqlite(cfg, conn=conn, long_poll=0)
                except Exception:
                    pass  # best-effort: a live consumer holds getUpdates → read from the buffer

            clauses = ["date_ts > ?"]
            params: list = [int(since_ts)]
            if not include_bot:
                clauses.append("is_bot = 0")
            if scope_chat is not None:
                clauses.append("chat_id = ?")
                params.append(scope_chat)
            sql = (
                "SELECT * FROM messages WHERE "
                + " AND ".join(clauses)
                + " ORDER BY date_ts ASC LIMIT ?"
            )
            rows = conn.execute(sql, [*params, max(1, limit)]).fetchall()

            if not rows:
                return _with_sync_health(f"📭 No new messages since {_local_dt(since_ts).strftime('%m-%d %H:%M')}.")

            unauth_count = sum(1 for r in rows if _is_unauthorized(r, allowed))
            header = f"🆕 {len(rows)} new message(s) (since {since_ts})"
            if unauth_count:
                header += f" — ⚠️ of which {unauth_count} UNAUTHORIZED (content hidden)"
            header += ":"
            body = "\n".join(_format_message_row(r, allowed_ids=allowed) for r in rows)
            return _with_sync_health(f"{header}\n{body}")
        finally:
            conn.close()
    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        return f"❌ Recipient error: {_redact(str(e))}"
    except requests.RequestException as e:
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


@mcp.tool()
def telegram_inspect_unauthorized(
    user_id: int | None = None,
    since_ts: int | None = None,
    limit: int = 20,
    reason: str = "",
) -> str:
    """Reveal the raw text of UNAUTHORIZED (non-allowlist) messages. AUDIT-LOGGED.

    Use sparingly: every call is recorded in the `audit_log` table with
    timestamp, parameters, and the count/update_ids of messages returned,
    so the owner can later review when Claude looked at impersonation content.

    Args:
        user_id: If given, restrict to this user_id. Default: all unauthorized.
        since_ts: If given, only messages after this Unix timestamp.
                  Default: last 7 days.
        limit: Max messages to reveal (default 20, hard cap 100).
        reason: Optional free-text rationale for the audit record
                (e.g. "the owner asked me to check yesterday's attempt").

    Returns:
        Formatted list with full raw content, or a sentinel if no match.
    """
    try:
        cfg = _load_config()
        allowed = cfg.get("allowed_user_ids") or []

        if not since_ts or since_ts <= 0:
            since_ts = int(time.time()) - 7 * 24 * 3600
        limit = max(1, min(int(limit), 100))

        conn = _get_db(cfg)
        try:
            _sync_buffer_to_sqlite(cfg, conn=conn, long_poll=0)

            # Build query: non-bot messages outside allowed_ids (optionally
            # pinned to a specific user_id), after since_ts.
            where_parts = ["is_bot = 0", "date_ts > ?"]
            params: list = [int(since_ts)]
            if allowed:
                placeholders = ",".join("?" * len(allowed))
                where_parts.append(f"user_id NOT IN ({placeholders})")
                params.extend(allowed)
            if user_id is not None:
                where_parts.append("user_id = ?")
                params.append(int(user_id))

            sql = (
                "SELECT * FROM messages WHERE "
                + " AND ".join(where_parts)
                + " ORDER BY date_ts ASC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()

            # Audit-log the call (even if 0 rows — we want to see the intent).
            _log_audit(
                conn,
                event_type="inspect_unauthorized",
                details={
                    "user_id": user_id,
                    "since_ts": since_ts,
                    "limit": limit,
                    "reason": reason,
                    "returned_count": len(rows),
                    "returned_update_ids": [r["update_id"] for r in rows],
                },
            )
            conn.commit()

            if not rows:
                return (
                    f"📭 No unauthorized messages match the filter "
                    f"(since {_local_dt(since_ts).strftime('%Y-%m-%d %H:%M')}"
                    f"{f', user_id={user_id}' if user_id is not None else ''}). "
                    f"The inspect attempt was audit-logged."
                )

            header = f"🔓 {len(rows)} unauthorized message(s) REVEALED (audit-logged):"
            lines = []
            for r in rows:
                ts = _local_dt(r["date_ts"])
                name = r["user_name"] or "?"
                text = r["text"] or ""
                lines.append(
                    f"[{ts.strftime('%m-%d %H:%M')}] ⚠️ user_id={r['user_id']} "
                    f"\"{name}\" (update_id={r['update_id']}):\n    {text}"
                )
            return f"{header}\n" + "\n".join(lines)
        finally:
            conn.close()
    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except requests.RequestException as e:
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


@mcp.tool()
def telegram_setup_chat_id() -> str:
    """Discover the chat_id/user_id of whoever most recently messaged the bot.

    Used at deploy time to learn a recipient's chat_id: they send the bot a message,
    then this tool reports their ids to add under config `recipients`.
    """
    try:
        cfg = _load_config(require_chat_id=False)
        token = cfg["bot_token"]

        updates = _get_updates(token, timeout=0)

        if not updates:
            return (
                "📭 The bot has no messages.\n\n"
                "Ask the person to open Telegram, find the bot, "
                "and send it a message (anything, e.g. 'Hi!').\n"
                "Then call this tool again."
            )

        for update in reversed(updates):
            msg = update.get("message")
            if msg and msg.get("from"):
                chat_id = msg["chat"]["id"]
                user_id = msg["from"]["id"]
                first_name = msg["from"].get("first_name", "?")
                username = msg["from"].get("username", "n/a")
                return (
                    f"✅ User found!\n\n"
                    f"Name: {first_name}\n"
                    f"Username: @{username}\n"
                    f"User ID: {user_id}\n"
                    f"Chat ID: {chat_id}\n\n"
                    f"The owner adds it to config.json by hand: "
                    f"\"recipients\": {{\"partner\": {{\"chat_id\": {chat_id}}}}} and {user_id} "
                    f"into \"allowed_user_ids\""
                )

        return "⚠️ There are messages, but none contain user data. Try again."

    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


@mcp.tool()
def telegram_get_photo(who: str = "partner", limit_scan: int = 30) -> str:
    """Download the most recent PHOTO sent by a recipient; return its local path.

    Reads from SQLite ONLY (does NOT call getUpdates), so it is safe to use while
    the Monitor long-poll is running. Relies on the sync pipeline (the Monitor or
    a read tool) having already stored the photo message.

    After this returns a path, use the Read tool on that path to view the image.

    Args:
        who: Whose latest photo to fetch ("partner" default, "owner", or "all").
        limit_scan: How many recent messages to scan for a photo (default 30).

    Returns:
        The local file path of the downloaded image (+ caption if any), or a sentinel.
    """
    try:
        cfg = _load_config()
        token = cfg["bot_token"]
        scope_chat = _scope_chat_id(cfg, who)

        conn = _get_db(cfg)
        try:
            clauses = ["is_bot = 0", "raw_json IS NOT NULL"]
            params: list = []
            if scope_chat is not None:
                clauses.append("chat_id = ?")
                params.append(scope_chat)
            sql = (
                "SELECT * FROM messages WHERE " + " AND ".join(clauses)
                + " ORDER BY date_ts DESC LIMIT ?"
            )
            params.append(max(1, limit_scan))
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        for r in rows:
            try:
                update = json.loads(r["raw_json"])
            except (TypeError, ValueError):
                continue
            msg = (update or {}).get("message") or {}
            file_id = _largest_photo_file_id(msg)
            if not file_id:
                continue
            file_path = _get_file_path(token, file_id)
            ext = os.path.splitext(file_path)[1] or ".jpg"
            dest = os.path.join(_images_dir(cfg), f"{r['message_id']}{ext}")
            _download_telegram_file(token, file_path, dest)
            caption = msg.get("caption") or ""
            cap_str = f"\nCaption: {caption}" if caption else ""
            return (
                f"✅ Photo downloaded: {dest}\n"
                f"Sender: {r['user_name']} (message_id={r['message_id']}){cap_str}\n"
                f"→ Open it with the Read tool from the path above."
            )
        return f"📭 No photo found in the '{who}' thread in the last {limit_scan} messages."
    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        return f"❌ Recipient error: {_redact(str(e))}"
    except requests.RequestException as e:
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


@mcp.tool()
def telegram_get_document(who: str = "owner", limit_scan: int = 30) -> str:
    """Download the most recent DOCUMENT (or audio/video/voice file) from a thread.

    Reads from SQLite ONLY (does NOT call getUpdates), so it is safe to use while
    the sync daemon holds the single getUpdates consumer. Mirrors
    `telegram_get_photo`, with one deliberate difference: it REFUSES to download
    anything sent by a non-allowed (unauthorized) user_id. A file landing on the owner's
    disk is a bigger step than a photo preview, so the level-2 unauthorized-sender protocol is
    enforced here at fetch time, not only at display time.

    After this returns a path, use the Read tool on that path to open the file.

    Args:
        who: Whose latest document to fetch ("owner" default, "partner", or "all").
        limit_scan: How many recent messages to scan (default 30).

    Returns:
        The local file path of the downloaded document, or a sentinel string.
    """
    try:
        cfg = _load_config()
        token = cfg["bot_token"]
        scope_chat = _scope_chat_id(cfg, who)
        allowed = cfg.get("allowed_user_ids") or []

        conn = _get_db(cfg)
        try:
            clauses = ["is_bot = 0", "raw_json IS NOT NULL"]
            params: list = []
            if scope_chat is not None:
                clauses.append("chat_id = ?")
                params.append(scope_chat)
            sql = (
                "SELECT * FROM messages WHERE " + " AND ".join(clauses)
                + " ORDER BY date_ts DESC LIMIT ?"
            )
            params.append(max(1, limit_scan))
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        skipped_idegen = 0
        for r in rows:
            if _is_unauthorized(r, allowed):
                skipped_idegen += 1
                continue
            try:
                update = json.loads(r["raw_json"])
            except (TypeError, ValueError):
                continue
            msg = (update or {}).get("message") or {}
            media = _media_info(msg)
            if not media or not media.get("file_id"):
                continue

            file_path = _get_file_path(token, media["file_id"])
            ext = os.path.splitext(file_path)[1]
            safe = _safe_filename(media.get("file_name"), ext)
            if not os.path.splitext(safe)[1] and ext:
                safe += ext
            dest = os.path.join(_documents_dir(cfg), f"{r['message_id']}_{safe}")
            _download_telegram_file(token, file_path, dest)

            caption = msg.get("caption") or ""
            cap_str = f"\nCaption: {caption}" if caption else ""
            size = _human_size(media.get("size"))
            size_str = f", {size}" if size else ""
            warn = (
                f"\n⚠️ {skipped_idegen} attachment(s) from unauthorized senders SKIPPED."
                if skipped_idegen else ""
            )
            return (
                f"✅ {media['label'].capitalize()} downloaded: {dest}\n"
                f"Sender: {r['user_name']} (message_id={r['message_id']}"
                f"{size_str}){cap_str}{warn}\n"
                f"→ Open it with the Read tool from the path above."
            )
        extra = f" ({skipped_idegen} unauthorized attachment(s) skipped.)" if skipped_idegen else ""
        return (
            f"📭 No document found in the '{who}' thread in the last "
            f"{limit_scan} messages.{extra}"
        )
    except FileNotFoundError as e:
        return f"❌ Config error: {_redact(str(e))}"
    except ValueError as e:
        return f"❌ Recipient error: {_redact(str(e))}"
    except requests.RequestException as e:
        return f"❌ Network error: {_redact(str(e))}"
    except Exception as e:
        return f"❌ Error: {_redact(str(e))}"


# --- Entry point ---

if __name__ == "__main__":
    mcp.run(transport="stdio")
