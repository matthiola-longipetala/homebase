#!/usr/bin/env python3
"""test_garble_fix.py — checks garble_fix.annotate() on INVENTED examples of the kind of
Hungarian STT garbles a noisy card game produces, plus negative (harmless) sentences against
false annotations. The test sentences are Hungarian STT-style data on purpose.

The test loads garble_hints.example.json (shipped, matches the sentences below), so it runs
out of the box. Your own private garble_hints.json is never touched by the test. To check your
own dictionary against your own sentences, point GARBLE_HINTS_FILE at it.

Run: `python3 test_garble_fix.py`  (pytest picks it up too).
"""
import os

import garble_fix
from garble_fix import annotate

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_HINTS = os.environ.get("GARBLE_HINTS_FILE") or os.path.join(_HERE, "garble_hints.example.json")
# Test-only override: production code keeps loading garble_hints.json (missing -> no-op).
garble_fix._HINTS = garble_fix._load_hints(_TEST_HINTS)

MARK = "⟦→"  # every annotation starts with this


def _annotated(line):
    out = annotate(line)
    assert out.startswith(line), "the raw line must be contained unchanged: %r -> %r" % (line, out)
    return MARK in out


# --- POSITIVE cases: these MUST get an annotation ---
POSITIVE = [
    # call / "megadta" (called)
    "Kornél megotto",
    "Kornél meg Otto",
    "Az ugatta",
    "Szerintem maga adta",
    "madom meg",
    "Elnyomogattam",
    # check (fuzzy)
    "Ekkor",
    "Meckek",
    "Megkelt",
    "Ekkolok",
    "csakolt",
    "Lekollok",
    # turn
    "born karolt",
    "Na, torjon!",
    "torn jott",
    # river
    "rivér lett",
    "Béver",
    # "huszonot" (twenty-five)
    "Uszonyotig emelek",
    "Uszonyotlet",
    # clubs / diamonds / spades
    "trüff",
    "F7-es",
    "Erref negy",
    "Haró öt",
    "Miknegy",
    "pick kilenc",
    # Italian-sounding hallucination
    "Eccolo!",
    "Eccolo che finisce!",
    # "the flop is out"
    "Kopp előállt",
    "A jelenflopp",
    "Kimbola Flopp",
    "Én van a flop",
    "Én vagyok a flop",
    "Én volnék a flop",
    # all-in
    "ahol en ment",
    # uncertain "hundred"
    "Allast hivok",
    # STT noise at the start of a line
    "Jó reggelt, agyam!",
    "Jó reggelt mindenkinek!",
    # "I call twenty" (only with a calling context, guard: hiv_context)
    "Én imádom a húsokat, hívok",
]


# --- NEGATIVE cases: these must NOT get an annotation ---
NEGATIVE = [
    "Kint van a flop.",
    "Ügyes vagy!",
    "Emelek harmincat.",
    "Passzolok.",
    "Ez egy jo lap.",
    "Mennyi a tet?",
    "Dobok.",
    "Te jossz.",
    "Osztok.",
    "Jo estet mindenkinek!",       # NOT 'jo reggelt' (good morning) -> not an STT noise pattern
    "Imádom a húsokat a grillen.",  # WITHOUT a calling context -> must not be annotated
    "Megoldom ezt a kezet.",        # 'Megoldom' on its own is harmless
    "Megvoltam a fLoppal.",         # 'Megvoltam' on its own is harmless
]


def test_positive():
    fail = [l for l in POSITIVE if not _annotated(l)]
    assert not fail, "these did NOT get an annotation (but should have): %r" % (fail,)


def test_negative():
    fail = [l for l in NEGATIVE if _annotated(l)]
    assert not fail, "these got a false annotation (should not have): %r" % (fail,)


def test_raw_preserved():
    # the raw text is always the prefix
    for l in POSITIVE + NEGATIVE:
        assert annotate(l).startswith(l)


def test_empty():
    assert annotate("") == ""
    assert annotate(None) is None


if __name__ == "__main__":
    test_positive()
    test_negative()
    test_raw_preserved()
    test_empty()
    print("OK — all tests green.")
