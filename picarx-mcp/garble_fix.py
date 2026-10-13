#!/usr/bin/env python3
"""garble_fix.py — STT mishearing corrector (garble dictionary) for robot_listen_loop.py.

The Hungarian ElevenLabs Scribe STT often garbles words during a noisy card game. We do NOT
replace automatically (that would be dangerous); we ANNOTATE: if a line matches a known
garble pattern, a suggestion with a question mark is appended to the END of the line:

    "Kornél megotto"  ->  "Kornél megotto ⟦→ megadta?⟧"   (invented example)

The raw text ALWAYS stays unchanged. Every annotation has a question mark (only a suggestion).

The patterns live in garble_hints.json (your own, not shipped; if it is missing, annotate()
is a no-op). Start from garble_hints.example.json. The file is a list, per element:
    {"pattern": <regex or keyword>, "hint": <suggestion>, "type": "regex"|"fuzzy",
     "note": <opt>, "guard": <opt, a key of _GUARDS, e.g. "hiv_context">}

- "regex":  the pattern is a regex, case-insensitive search on the line.
- "fuzzy":  the pattern is a reference phrase; the line's words (or word pairs) are compared
            to it with difflib.SequenceMatcher (stdlib, zero dependencies on the Pi). We only
            annotate on a close enough match.

Principles: (a) few, certain annotations rather than many false ones; (b) patterns around
card suits and actions are the most valuable; (c) every annotation has the form "→ X?".

Self-test: `python3 garble_fix.py`  -> stdin line by line -> annotated stdout.
"""
import os
import re
import json
from difflib import SequenceMatcher

_HINTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "garble_hints.json")

# Fuzzy threshold: the SequenceMatcher ratio (0..1) must reach this for an annotation.
_FUZZY_THRESHOLD = 0.82

# Named guards: a rule only fires if its guard condition also holds.
# E.g. a sentence like "imadom a husokat" only gets an annotation in a calling ("hív...")
# context. The regex below matches Hungarian word forms of "call" — keep it as is.
_HIV_CONTEXT = re.compile(r"\bh[ií]v(ok|om|[aá]s|ni|j|jak)?\b", re.IGNORECASE)
_GUARDS = {
    "hiv_context": lambda line: bool(_HIV_CONTEXT.search(line)),
}


def _load_hints(path=_HINTS_PATH):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    compiled = []
    for h in raw:
        typ = h.get("type", "regex")
        entry = {"hint": h["hint"], "type": typ, "pattern": h["pattern"],
                 "guard": h.get("guard")}
        if typ == "regex":
            entry["rx"] = re.compile(h["pattern"], re.IGNORECASE)
        else:
            # fuzzy: precompute the number of reference words (how many words to join)
            entry["ref"] = h["pattern"].lower()
            entry["ref_words"] = len(h["pattern"].split())
        compiled.append(entry)
    return compiled


try:
    _HINTS = _load_hints()
except Exception:
    # If the json is missing/broken, do not crash: with an empty dictionary annotate is a no-op.
    _HINTS = []


_WORD_RX = re.compile(r"[0-9a-zA-ZÀ-ſ]+")


def _norm(s):
    """lower-case, accent-preserving normalisation for fuzzy comparison."""
    return s.lower()


def _fuzzy_hit(line, ref, ref_words):
    """True if some word window (of ref_words length) of the line is close enough to ref."""
    words = _WORD_RX.findall(_norm(line))
    if not words:
        return False
    n = max(1, ref_words)
    # sliding window: compare n-word slices
    for i in range(len(words) - n + 1):
        cand = " ".join(words[i:i + n])
        if SequenceMatcher(None, cand, ref).ratio() >= _FUZZY_THRESHOLD:
            return True
    # if ref is a single word and a word of the line is very close (already covered above with n=1)
    return False


def annotate(line):
    """Appends the matching garble suggestions to the end of the line, with question marks.
    The raw line stays unchanged. With several hits all are appended (dedup).
    An empty/None line is returned unchanged.
    """
    if not line:
        return line
    hints_found = []
    seen = set()
    for h in _HINTS:
        if h["type"] == "regex":
            hit = bool(h["rx"].search(line))
        else:  # fuzzy
            hit = _fuzzy_hit(line, h["ref"], h["ref_words"])
        # optional guard: only fires if the context condition also holds
        if hit and h.get("guard"):
            guard_fn = _GUARDS.get(h["guard"])
            if guard_fn is not None and not guard_fn(line):
                hit = False
        if hit and h["hint"] not in seen:
            seen.add(h["hint"])
            hints_found.append(h["hint"])
    if not hints_found:
        return line
    suffix = " ".join("⟦→ {}⟧".format(hh) for hh in hints_found)
    return "{} {}".format(line, suffix)


if __name__ == "__main__":
    import sys
    # UTF-8 output for the ⟦⟧ markers (the Pi is UTF-8, but this is also safe for Windows tests):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for raw in sys.stdin:
        out = annotate(raw.rstrip("\n"))
        print(out, flush=True)
