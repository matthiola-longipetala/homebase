# -*- coding: utf-8 -*-
"""hangkep.py — the robot's SOUNDSCAPE state + change-based event logic.

Pure stdlib (no numpy/litert) -> imported by the SYSTEM python 3.13 (robot_listen_loop.py).
The raw YAMNet classification runs separately (hangcimke.py in the venv); this module only
processes the ready-made top-k labels:

  1) QUERYABLE STATE: rolling soundscape -> /tmp/hangkep.json
     ("what I hear now" + the background carpet labels + the last N events with timestamps).
  2) CHANGE-BASED EVENT: when a NEW, distinct sound appears (not present in the last
     'background_ttl_s', and its score is above the event threshold), it returns an event
     string (the listener writes it to STDOUT -> Monitor -> chat).
     A continuous base (e.g. a carpet of crickets) SETTLES INTO THE BACKGROUND after the
     first signal: while it is continuously present it does not nag again; if it is gone for
     longer than 'background_ttl_s', we forget it -> it may speak again (e.g. wind picking up).

The event/state text uses the display name from hangkep_labels.magyar() (Hungarian by
default, the English YAMNet name where there is no translation).

The parameters come from hangkep_config.json (separate from voice_config.json, which holds
secrets). Missing fields get built-in defaults.
"""
import os, json, time

try:
    from hangkep_labels import magyar
except Exception:
    def magyar(en):
        return en

CONFIG_PATH = os.path.expanduser("~/picarx-mcp/hangkep_config.json")
STATE_PATH = "/tmp/hangkep.json"

DEFAULTS = {
    "enabled": True,
    "topk": 5,
    # a label is "present" in the soundscape if its score is >= this:
    "score_threshold": 0.25,
    # a NEW label triggers a STANDALONE event only if its score is >= this:
    "event_threshold": 0.40,
    # an already announced/seen label counts as "known" (background) for this many seconds;
    # if it is NOT heard for longer than this, we forget it -> it may speak again:
    "background_ttl_s": 180,
    # global anti-spam: at least this many seconds between two emitted events. It only
    # catches rapid bursts; repeats of the SAME sound are caught by the settling
    # (background_ttl_s), not by this -> hence small:
    "min_event_gap_s": 3,
    # at most this many events per classification:
    "max_events_per_ingest": 2,
    # this many recent events are kept in /tmp/hangkep.json:
    "recent_events_keep": 12,
    # these acoustic-scene / meta classes are IGNORED
    # (not an event and not added to the "I hear now" list either):
    "ignore_labels": [
        "Silence", "Noise", "Environmental noise", "White noise", "Pink noise",
        "Static", "Sound effect", "Field recording", "Inside, small room",
        "Inside, large room or hall", "Inside, public space",
        "Outside, urban or manmade", "Outside, rural or natural",
        "Reverberation", "Echo", "Sine wave", "Harmonic", "Pulse",
    ],
    # umbrella labels: they fire as an event ONLY if there is no more specific hit in the
    # same segment (they still show up in the soundscape state):
    "generic_labels": [
        "Animal", "Domestic animals, pets", "Wild animals",
        "Livestock, farm animals, working animals",
        "Bird vocalization, bird call, song", "Insect", "Music", "Vehicle",
    ],
}


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if v is not None})
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return cfg


class HangkepEngine:
    def __init__(self, config_path=CONFIG_PATH, state_path=STATE_PATH):
        self.config_path = config_path
        self.state_path = state_path
        self.cfg = load_config(config_path)
        self._cfg_mtime = self._mtime(config_path)
        # label -> last "was present" timestamp (basis of the background carpet + settling)
        self.last_seen = {}
        self.recent_events = []   # [{"t": "HH:MM:SS", "hu":.., "en":.., "score":..}]
        self.last_event_ts = 0.0
        self.now_scored = []      # the present labels of the latest classification

    @staticmethod
    def _mtime(path):
        try:
            return os.path.getmtime(path)
        except Exception:
            return 0.0

    def _maybe_reload_config(self):
        m = self._mtime(self.config_path)
        if m != self._cfg_mtime:
            self.cfg = load_config(self.config_path)
            self._cfg_mtime = m

    def _ignored(self, en):
        return en in self.cfg.get("ignore_labels", [])

    def ingest(self, labels, now=None):
        """labels: [(en, hu, score), ...] from YAMNet (already top-k).
        Returns: list of event strings to emit (e.g. 'tücsök 0.72' = cricket)."""
        if now is None:
            now = time.time()
        self._maybe_reload_config()
        if not self.cfg.get("enabled", True):
            return []

        st = float(self.cfg["score_threshold"])
        et = float(self.cfg["event_threshold"])
        ttl = float(self.cfg["background_ttl_s"])
        gap = float(self.cfg["min_event_gap_s"])
        maxev = int(self.cfg["max_events_per_ingest"])

        # present labels (above threshold, not ignored)
        present = [(en, hu, sc) for (en, hu, sc) in labels
                   if sc >= st and not self._ignored(en)]
        self.now_scored = [{"en": en, "hu": hu, "score": round(float(sc), 3)}
                           for (en, hu, sc) in present]

        # settling: a label is "new" if last_seen is missing OR older than ttl
        candidates = []
        for (en, hu, sc) in present:
            prev = self.last_seen.get(en, 0.0)
            is_new = (now - prev) > ttl
            # refresh the "was present" timestamp for EVERY present label (this keeps it in the background)
            self.last_seen[en] = now
            if is_new and sc >= et:
                candidates.append((en, hu, sc))

        # generic umbrella labels (Animal, Domestic animals...) fire as an event only if
        # there is NO specific hit in the same segment
        # (for a meowing cat, all 3 labels of the animal chain would have fired)
        generic = set(self.cfg.get("generic_labels", []))
        if any(en not in generic for (en, hu, sc) in candidates):
            candidates = [c for c in candidates if c[0] not in generic]

        events = []
        for (en, hu, sc) in candidates[:maxev]:
            if (now - self.last_event_ts) >= gap:
                tag = hu if hu else en
                events.append(f"{tag} {sc:.2f}")
                self.last_event_ts = now
                self.recent_events.append({
                    "t": time.strftime("%H:%M:%S", time.localtime(now)),
                    "hu": hu, "en": en, "score": round(float(sc), 3),
                })

        # clean up old last_seen entries (so it does not grow forever)
        cutoff = now - max(ttl * 2, 600)
        self.last_seen = {k: v for k, v in self.last_seen.items() if v >= cutoff}
        keep = int(self.cfg["recent_events_keep"])
        if len(self.recent_events) > keep:
            self.recent_events = self.recent_events[-keep:]

        self._write_state(now, ttl)
        return events

    def _write_state(self, now, ttl):
        # background carpet: what we heard at least once in the last ttl
        background = [magyar(en) for en, ts in sorted(
            self.last_seen.items(), key=lambda kv: -kv[1]) if (now - ts) <= ttl]
        state = {
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "updated_ts": int(now),
            "enabled": bool(self.cfg.get("enabled", True)),
            "now": [d["hu"] for d in self.now_scored],
            "now_scored": self.now_scored,
            "background": background[:12],
            "recent": list(reversed(self.recent_events)),  # newest first
        }
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            os.replace(tmp, self.state_path)
        except Exception:
            pass
