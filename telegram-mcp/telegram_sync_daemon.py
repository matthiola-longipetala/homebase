"""
telegram_sync_daemon.py — standalone, restart-resilient SYNC process for telegram-mcp.

THE ONLY getUpdates consumer: it continuously pulls Telegram into the SQLite buffer, so everything else
(Claude, the watchers, scheduled tasks) always reads a FRESH buffer. Telegram allows only one getUpdates
reader per bot at a time — two daemons would cause a 409 conflict.

Independent of any session: it is started from the Windows Task Scheduler (at logon), so it survives
machine and app restarts. It does NOT emit wake-up events — that is the watchers' job.

Safeguards:
- **Lock v2 (JSON):** pid + process creation time + machine boot time + heartbeat. Windows reuses PIDs,
  so a lock left over from a previous boot is PROVABLY dead this way, even if its PID appears "alive".
  (With the old PID-only lock the daemon silently exited after a reboot and the buffer froze — nothing
  signalled it.) The decision lives in a pure, testable function (`lock_verdict`).
- **Heartbeat every 30 s:** whether it is alive can be measured from outside (read by server.py's
  `_sync_daemon_health()`).
- **Fail-closed:** if the daemon cannot start, it does not fail silently: log entry + Telegram message
  to the owner (sendMessage is not a getUpdates consumer, so it does not conflict).
- **Unauthorized-sender protection ("idegen" = unauthorized sender):** after every cycle the quarantine
  step runs (`idegen_karanten.zar`), so content from non-allowed senders is encrypted into quarantine
  within ~2 s of arrival. A quarantine error does not stop the sync.
- The config (including the bot token) is re-read every cycle -> rotating the token = saving the config.
- Errors are written token-redacted and rate-limited to `data/sync_daemon.log`.

Several bots (e.g. two Claudes, two bots): use a separate `TELEGRAM_MCP_CONFIG` and
`TELEGRAM_SYNC_DATA_DIR` per instance.
"""
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

# Lock and log live in the same data/ folder as messages.db: next to the CONFIG file (your secrets folder
# when TELEGRAM_MCP_CONFIG points there). TELEGRAM_SYNC_DATA_DIR overrides it per instance.
_CONFIG = os.environ.get("TELEGRAM_MCP_CONFIG") or os.path.join(BASE, "config.json")
DATA_DIR = os.environ.get("TELEGRAM_SYNC_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(_CONFIG)), "data")
LOG = os.path.join(DATA_DIR, "sync_daemon.log")
LOCK = os.path.join(DATA_DIR, "sync_daemon.lock")
POLL_SLEEP = 1           # seconds between cycles (AFTER the long-poll; it returns immediately on a new message)
LONG_POLL = 20           # server-side long-poll seconds — instead of a 2 s short poll.
                         # This means ONE request every ~20 s (immediately on a message), not 30/minute;
                         # the heartbeat (30 s) and the 300 s stale threshold still fit comfortably.
ERROR_BACKOFF = 5        # seconds to wait after an error
HEARTBEAT_EVERY = 30     # seconds between lock-heartbeat rewrites
BOOT_TOLERANCE = 120     # seconds; clock/tick drift allowance for boot_id compare

LOCK_VERSION = 2


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{int(time.time())}  {msg}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Platform probe (Windows) — MEASUREMENT only. The decision does not live here.
# --------------------------------------------------------------------------

def _pid_alive(pid):
    """True if an openable process belongs to the PID. True when uncertain
    (never risk two parallel daemons merely because of a measurement error)."""
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return True  # uncertain → assume alive


def _process_create_time(pid):
    """The process creation time as a Windows FILETIME (int), or None.

    THIS IS THE KEY against PID reuse: two different processes can get
    the same PID, but their creation times differ."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            return None
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            ok = k32.GetProcessTimes(h, ctypes.byref(ft[0]), ctypes.byref(ft[1]),
                                     ctypes.byref(ft[2]), ctypes.byref(ft[3]))
            if not ok:
                return None
            c = ft[0]
            return (int(c.dwHighDateTime) << 32) | int(c.dwLowDateTime)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return None


def _boot_id():
    """Estimated unix time of the machine's boot (int), or None.

    now - uptime. It always differs between two different boots; within the same
    boot it can drift by a few seconds, hence BOOT_TOLERANCE."""
    try:
        import ctypes
        ms = ctypes.windll.kernel32.GetTickCount64()
        return int(time.time() - (int(ms) / 1000.0))
    except Exception:
        pass
    try:  # POSIX fallback (for testing / portability)
        with open("/proc/uptime", "r") as f:
            return int(time.time() - float(f.read().split()[0]))
    except Exception:
        return None


def probe_process(pid, lock_path=None):
    """Collects what can be learned from the system. Pure MEASUREMENT."""
    mtime = None
    try:
        mtime = int(os.path.getmtime(lock_path or LOCK))
    except Exception:
        pass
    return {
        "pid_alive": _pid_alive(pid),
        "create_time": _process_create_time(pid),
        "boot_id": _boot_id(),
        "lock_mtime": mtime,
        "now": int(time.time()),
    }


# --------------------------------------------------------------------------
# Lock record — read / write / DECISION (pure, testable functions)
# --------------------------------------------------------------------------

def parse_lock(raw):
    """Record dict from the lock-file content. Handles the old bare-PID format.
    Returns: dict, or None (if it cannot be parsed)."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            rec = json.loads(raw)
        except Exception:
            return None
        if not isinstance(rec, dict) or "pid" not in rec:
            return None
        try:
            rec["pid"] = int(rec["pid"])
        except Exception:
            return None
        return rec
    try:  # legacy: bare PID
        return {"v": 1, "pid": int(raw), "legacy": True}
    except Exception:
        return None


def make_record(pid, create_time, boot_id, now, started_at=None):
    return {
        "v": LOCK_VERSION,
        "pid": int(pid),
        "create_time": create_time,
        "boot_id": boot_id,
        "started_at": int(started_at if started_at is not None else now),
        "heartbeat": int(now),
    }


def write_lock(rec, path=None):
    """Atomic write (tmp + os.replace), so an interrupted write cannot leave
    truncated JSON (which `parse_lock` would read as None -> needless takeover).

    ⚠️ `path=None` is DELIBERATE, not style. A default of `path=LOCK` would be
    bound at function DEFINITION time, so reassigning the module-level LOCK
    (tests, relocation) would have NO effect: the function would keep writing to
    the ORIGINAL path. An end-to-end test caught exactly this — even when
    "redirected", the test would have operated on the real lock."""
    path = path or LOCK
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False))
    os.replace(tmp, path)


def read_lock(path=None):
    path = path or LOCK          # see the write_lock note (def-time binding)
    try:
        with open(path, encoding="utf-8") as f:
            return parse_lock(f.read())
    except FileNotFoundError:
        return None
    except Exception:
        return None


def lock_verdict(rec, probe, my_pid):
    """THE DECISION. Pure function — no files, no system calls, no platform.

    Returns: (take_over: bool, code: str, reason: str)
      take_over=True  -> the lock is stale, we may take it over
      take_over=False -> another daemon is alive (or we cannot rule it out) -> do NOT start

    THE ORDER IS DELIBERATE. The boot check PRECEDES the PID check, because that is
    exactly the class of bug being fixed: a PID left over from a previous boot looks "alive".
    """
    if rec is None:
        return True, "no_lock", "no lock file (or it cannot be parsed)"

    if int(rec.get("pid", -1)) == int(my_pid):
        return True, "own_pid", "the lock names our own PID (orphaned own lock)"

    rec_boot = rec.get("boot_id")
    pr_boot = probe.get("boot_id")
    if rec_boot is not None and pr_boot is not None:
        if abs(int(pr_boot) - int(rec_boot)) > BOOT_TOLERANCE:
            return True, "stale_boot", (
                f"the lock is from an EARLIER machine boot "
                f"(lock boot={int(rec_boot)}, current boot={int(pr_boot)})"
            )

    # ★ Safety net for the OLD (v1, bare-PID) lock: it has no boot_id, but the FILE
    # MTIME also reveals that the lock was written before the current boot. Without
    # this, the legacy branch would repeat the same bug once more, on the day of the switch.
    if pr_boot is not None:
        lm = probe.get("lock_mtime")
        if lm is not None and int(lm) < int(pr_boot) - 60:
            return True, "stale_boot_mtime", (
                f"the lock file was written BEFORE the current machine boot "
                f"(lock mtime={int(lm)}, boot={int(pr_boot)})"
            )

    if probe.get("pid_alive") is False:
        return True, "pid_dead", f"the lock's PID ({rec.get('pid')}) is not alive"

    rec_ct = rec.get("create_time")
    pr_ct = probe.get("create_time")
    if rec_ct is not None and pr_ct is not None and int(rec_ct) != int(pr_ct):
        return True, "pid_reused", (
            f"the PID ({rec.get('pid')}) is alive, but it is a DIFFERENT process: the creation time "
            f"differs (lock={int(rec_ct)}, current={int(pr_ct)})"
        )

    if rec.get("legacy"):
        return False, "legacy_alive", (
            f"OLD-format lock (PID only: {rec.get('pid')}), and the PID is alive — "
            "cannot rule out that it is a real daemon. If you are sure no daemon is running, "
            f"delete the lock file: {LOCK}"
        )

    if rec_ct is not None and pr_ct is None:
        return False, "unverifiable", (
            f"the PID ({rec.get('pid')}) is alive, but its creation time could not be "
            "queried — not starting, to be safe (two daemons would cause a getUpdates conflict)"
        )

    hb = rec.get("heartbeat")
    if hb is not None:
        age = int(probe.get("now", time.time())) - int(hb)
        if age > 300:
            return False, "alive_but_stale_hb", (
                f"the daemon process (pid {rec.get('pid')}) is ALIVE, but has not refreshed the lock "
                f"for {age // 60} minutes — probably stuck. NOT taking over "
                "(two getUpdates consumers would be worse), but this needs HUMAN intervention."
            )

    return False, "alive", f"another sync daemon is alive (pid {rec.get('pid')})"


# --------------------------------------------------------------------------
# FAIL-CLOSED notification — the silent exit itself was the bug
# --------------------------------------------------------------------------

def notify_failure(reason_text):
    """Telegram message to the owner if the daemon CANNOT start.

    sendMessage is NOT a getUpdates consumer -> it does not conflict even if another
    daemon really is running. Best-effort: never raises an exception upward."""
    try:
        import server  # noqa: E402
        import requests
        cfg = server._load_config()
        tok = cfg.get("bot_token")
        rcp = (cfg.get("recipients") or {}).get(cfg.get("default_recipient") or "owner") or {}
        chat = rcp.get("chat_id") or cfg.get("chat_id")
        if not tok or not chat:
            log("notify_failure: no token/chat_id, skipped")
            return False
        txt = (
            "⚠️ The Telegram sync daemon did NOT start.\n\n"
            f"Reason: {reason_text}\n\n"
            "While it is down, messages.db is NOT being filled: your messages only "
            "get in if a Claude instance syncs by itself. "
            "(This alert is the fail-closed guard — without it, this would happen silently.)"
        )
        r = requests.post(server._api_url(tok, "sendMessage"),
                          json={"chat_id": chat, "text": txt}, timeout=10)
        ok = bool(r.ok)
        log(f"notify_failure: sent={ok}")
        return ok
    except Exception as e:
        log(f"notify_failure ERROR: {type(e).__name__}: {e}")
        return False


def acquire_lock():
    """Refuse to start only if a live daemon PROVABLY holds the lock."""
    my_pid = os.getpid()
    rec = read_lock()
    probe = probe_process(rec.get("pid")) if rec else {
        "pid_alive": False, "create_time": None, "boot_id": _boot_id(),
        "lock_mtime": None, "now": int(time.time()),
    }
    take, code, reason = lock_verdict(rec, probe, my_pid)

    if not take:
        log(f"NOT starting — {code}: {reason}")
        notify_failure(reason)
        return False

    if code != "no_lock":
        log(f"stale lock taken over — {code}: {reason}")

    try:
        now = int(time.time())
        write_lock(make_record(my_pid, _process_create_time(my_pid), _boot_id(), now))
        return True
    except Exception as e:
        log(f"could not write lock: {e}")
        return False


def refresh_heartbeat(state):
    """Refresh the lock's heartbeat field (at most every HEARTBEAT_EVERY seconds).
    This makes it measurable from outside that the daemon is ALIVE — not just that the PID exists."""
    now = int(time.time())
    if now - state.get("last_hb", 0) < HEARTBEAT_EVERY:
        return
    state["last_hb"] = now
    try:
        rec = read_lock()
        if rec and int(rec.get("pid", -1)) != os.getpid():
            # someone else took over the lock — this is serious, but it is NOT our call
            log(f"WARNING: the lock is held by another pid ({rec.get('pid')}), ours is {os.getpid()}")
            return
        write_lock(make_record(os.getpid(), state.get("create_time"),
                               state.get("boot_id"), now, started_at=state.get("started_at")))
    except Exception as e:
        log(f"heartbeat ERROR: {type(e).__name__}: {e}")


def _redact(text, tok):
    if tok and isinstance(text, str):
        return text.replace(tok, "<token-redacted>")
    return text


def main():
    if not acquire_lock():
        return
    import server  # noqa: E402  telegram-mcp v3.4
    import idegen_karanten  # noqa: E402  unauthorized-sender protection

    now = int(time.time())
    state = {
        "last_hb": now,
        "started_at": now,
        "create_time": _process_create_time(os.getpid()),
        "boot_id": _boot_id(),
    }
    log(f"sync daemon started (pid {os.getpid()}) [unauthorized-sender protection ACTIVE, lock v{LOCK_VERSION}]")
    consec = 0
    while True:
        try:
            cfg = server._load_config()          # re-read each cycle (picks up token rotation)
            server._sync_buffer_to_sqlite(cfg, long_poll=LONG_POLL)
            # ★ unauthorized-sender protection: quarantine new non-allowed content immediately
            try:
                zart = idegen_karanten.zar(csendes=True)
                if zart:
                    log(f"unauthorized-sender protection: {zart} message(s) quarantined")
            except Exception as ke:
                log(f"unauthorized-sender protection ERROR (sync continues): {type(ke).__name__}: {ke}")
            if consec:
                log("sync recovered")
            consec = 0
            refresh_heartbeat(state)
            time.sleep(POLL_SLEEP)
        except Exception as e:
            consec += 1
            tok = None
            try:
                tok = server._load_config().get("bot_token")
            except Exception:
                pass
            # log sparsely: first, then milestones — avoid filling the log during outages
            if consec in (1, 5, 20) or consec % 60 == 0:
                log(f"sync-error ({consec}x): {type(e).__name__}: {_redact(str(e), tok)}")
            # the heartbeat runs EVEN ON ERROR: the daemon is alive, only the network is bad
            refresh_heartbeat(state)
            time.sleep(ERROR_BACKOFF)


if __name__ == "__main__":
    main()
