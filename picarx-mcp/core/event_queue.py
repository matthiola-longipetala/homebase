"""
In-band events queue — a PLACEHOLDER in phase 1.

It comes to life in phase 3: every audio/sensor event since the last tool call
is recorded with a timestamp, and every tool response returns them in its
'events' field. This makes parallel communication fluid (e.g. while the robot
is moving, someone speaks — the next response carries the transcript).

For now only the interface exists, so tools already return the
'events' / 'events_age_s' fields in their final shape (empty).
"""
import time

_events: list = []
_last_drained_at = time.time()


def push_event(event: dict) -> None:
    """Phase 3: the reflex loop / audio pipeline pushes events here."""
    event.setdefault("timestamp", time.time())
    _events.append(event)


def drain() -> tuple[list, float]:
    """Returns the accumulated events and empties the queue.

    Returns:
        (events, events_age_s) — in phase 1 this is always ([], 0.0).
    """
    global _events, _last_drained_at
    if not _events:
        return [], 0.0
    out = _events
    age = round(time.time() - _last_drained_at, 1)
    _events = []
    _last_drained_at = time.time()
    return out, age
