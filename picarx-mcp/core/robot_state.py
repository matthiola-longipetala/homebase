"""
Active/passive mode framework.

mark_chat_active() must be called at the start of every tool call. In phase 4 (audio)
it will control switching audio recording on/off: in active mode continuous
recording + event-queue collection, in passive mode saving energy/cost.

ACTIVE_TIMEOUT_S can be tuned from user feedback (30 / 60 / 120 s).
"""
import os
import time

_last_chat_active_at = 0.0
ACTIVE_TIMEOUT_S = 60

# The active-mode timestamp is ALSO written to a FILE so that separate processes
# (e.g. stream_supervisor) can see "am I active right now" — the in-memory state
# is only visible inside the server process. The stream is tied to this.
ACTIVE_STAMP_FILE = "/tmp/robot_active_at"


def configure(active_timeout_s: int) -> None:
    """Loads the 'active_timeout_s' value from config.json (at server start)."""
    global ACTIVE_TIMEOUT_S
    if active_timeout_s and active_timeout_s > 0:
        ACTIVE_TIMEOUT_S = int(active_timeout_s)


def mark_chat_active() -> None:
    """Call at the start of every tool call — refreshes the last-active timestamp.

    Also refreshes the file-based timestamp (ACTIVE_STAMP_FILE) so that
    stream_supervisor (a separate process) knows whether the robot is in active mode,
    and keeps the stream alive meanwhile. A file-write error must NEVER break the
    tool call (the stream is only an add-on)."""
    global _last_chat_active_at
    _last_chat_active_at = time.time()
    try:
        with open(ACTIVE_STAMP_FILE, "w") as f:
            f.write(str(_last_chat_active_at))
    except Exception:
        pass


def is_active_mode() -> bool:
    return (time.time() - _last_chat_active_at) < ACTIVE_TIMEOUT_S


def get_last_active_age_s() -> float:
    if _last_chat_active_at == 0.0:
        return -1.0   # there has been no active tool call yet (since server start)
    return time.time() - _last_chat_active_at
