"""beallitas.py ("beallitas" = settings) — shared config reader for the helper scripts (watchers, quarantine).

Reads the same config.json as server.py (TELEGRAM_MCP_CONFIG, by default the config.json next to the
server), so chat and user IDs live in ONE place and none of them are hard-coded.
"""
import json
import os
from pathlib import Path

BAZIS = Path(__file__).resolve().parent
CONFIG_PATH = os.environ.get("TELEGRAM_MCP_CONFIG", str(BAZIS / "config.json"))


def config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def db_path():
    """The buffer database — the SAME rule as server._db_path(), so the server, the sync daemon, the
    watchers and the quarantine all use one file: TELEGRAM_MCP_DB, else the config's `db_path`, else
    data/messages.db next to the CONFIG file (i.e. in your secrets folder, not in the repository)."""
    return (os.environ.get("TELEGRAM_MCP_DB") or config().get("db_path")
            or os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), "data", "messages.db"))


def chat_id(nev=None):
    """A recipient's chat_id from the `recipients` block (default: default_recipient / "owner")."""
    cfg = config()
    nev = nev or cfg.get("default_recipient", "owner")
    r = (cfg.get("recipients") or {}).get(nev)
    if r and r.get("chat_id"):
        return int(r["chat_id"])
    if nev == cfg.get("default_recipient", "owner") and cfg.get("chat_id"):
        return int(cfg["chat_id"])   # legacy config without a recipients block
    raise KeyError(f"no such recipient in the config: {nev}")


def figyelt_chatek():
    """chat_ids of all named recipients (the watcher monitors these; never an unauthorized chat)."""
    cfg = config()
    ids = [int(r["chat_id"]) for r in (cfg.get("recipients") or {}).values() if r.get("chat_id")]
    return tuple(ids) or (chat_id(),)


def engedelyezett():
    return tuple(int(x) for x in config().get("allowed_user_ids", []))
