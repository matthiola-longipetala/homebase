# -*- coding: utf-8 -*-
"""Prediction horizon guard -- FUNCTIONAL test.

Lesson behind it ("a verification that a failure would also pass"): it is not
enough to look whether a field exists. The observations must look DIFFERENT
in case of failure, so every case asserts an EXPECTED value and wrong logic
trips on it.

What is tested:
- `_pred_open_summary`: open / abandoned / pending, where an explicit
  `horizon` separates "not decidable yet" from "abandoned" (the old rule only
  looked at the age);
- `_pred_text_horizon`: a due-date HINT read from the immutable claim text --
  only a date in HORIZON FORM counts (`-ig`/`-re`/`-ra`, "by", "until", ...);
- an end-to-end run on a synthetic `kontextus/predikciok.json` through the
  real store loader (`_load_preds`) and the horizon labels.

Storage keys (`allitas` = claim, `konfidencia` = confidence, `tipus` = kind,
`predikciok` = predictions) are the server's JSON field names. The Hungarian
`-ig` ("until") suffix is a horizon form the server parses.

Run:  python -X utf8 test_prediction_horizon.py
"""
import json
from datetime import datetime, timedelta

import _support as S

C = S.Checker("Prediction horizon guard")
ROOT = S.make_memory_root("horizon_")
M = S.load_server(ROOT)

now = datetime.now()


def mk(pid, days_ago, horizon=None, claim="x"):
    p = {"id": pid, "status": "open", "konfidencia": 70, "tipus": "world",
         "allitas": claim,
         "created": (now - timedelta(days=days_ago)).isoformat(timespec="seconds")}
    if horizon:
        p["horizon"] = horizon
    return p


def day(n):
    return (now + timedelta(days=n)).strftime("%Y-%m-%d")


C.section("1. Open / abandoned / pending (each case would differ under the old age-only rule)")
cases = [
    # (pred, expected bucket, why)
    (mk(101, 30, day(6)), "pending",
     "recorded LONG ago, but its horizon is still in the future -> the OLD logic would say ABANDONED"),
    (mk(102, 1, day(-5)), "abandoned",
     "FRESH, but its horizon has ALREADY passed -> the OLD logic would not speak (a new catch)"),
    (mk(103, 1, day(1)), "pending", "fresh + future horizon"),
    (mk(104, 10), "abandoned", "no horizon, 10 days old -> the old >7-day rule applies"),
    (mk(105, 2), "neither", "no horizon, 2 days old -> open, but not abandoned"),
    (mk(106, 0, day(0)), "pending", "due today -> because of grace it is still pending TODAY"),
    (mk(107, 0, day(-1)), "pending",
     "due yesterday -> grace = 1 day, so it is still pending TODAY (abandoned from tomorrow)"),
]
op, old, futo = M._pred_open_summary({"predikciok": [c[0] for c in cases]})
oid, fid = {p["id"] for p in old}, {p["id"] for p in futo}
for p, expected, why in cases:
    actual = "abandoned" if p["id"] in oid else ("pending" if p["id"] in fid else "neither")
    C.check(f"#{p['id']} expected={expected:<9} | {why}", actual == expected, f"actual={actual}")
C.check("every open item is in `op`", len(op) == len(cases), len(op))
C.check("no item is abandoned AND pending at the same time", not (oid & fid), oid & fid)

C.section("2. Text horizon HINT (read from the immutable text; it shows, it does not decide)")
# ⚠️ Every date is RELATIVE: an earlier version wrote FIXED dates next to a
# relative `created`; once the calendar caught up, the function correctly
# returned None while the test still expected the old value. The instrument
# rotted, not the server.
t_cases = [
    (mk(201, 8, None, "by %s at least 8 more predictions are recorded" % day(5)), day(5), "'by' + date"),
    (mk(202, 8, None, "%s-ig legalább 8 további predikció kerül bele" % day(5)), day(5),
     "Hungarian '-ig' (until) suffix"),
    (mk(203, 8, None, "there is no date at all in this sentence"), None, "no date"),
    (mk(204, 8, None, "because of the %s event" % day(-30)), None, "a PAST date is no hint"),
    (mk(205, 8, None, "surely there by %s" % day(400)), None, "> 120 days away is no hint"),
    (mk(206, 8, None, "%s és %s között" % (day(5), day(20))), day(20),
     "Hungarian 'között' (between) postposition: the latest horizon-form date wins"),
    (mk(207, 8, None, 'quoted call: defer_prediction(5, "%s")' % day(9)), None,
     "a quoted argument is NOT horizon form (false alibi guard)"),
]
for p, expected, why in t_cases:
    th = M._pred_text_horizon(p)
    actual = th.strftime("%Y-%m-%d") if th else None
    C.check(f"#{p['id']} expected={str(expected):<10} | {why}", actual == expected, f"actual={actual}")
C.check("#207 the quoted date still shows up as a WEAK mention (the filter is not silent)",
        M._pred_text_mention(t_cases[-1][0]) is not None)
# The server documents English "at the latest" AFTER the date as a horizon form
# (`_PRED_HORIZON_SUFFIX_RE`), and `_pred_text_dates` looks at 20 characters after the date
# (it used to be 12, too short for " at the latest"; this case caught that).
p208 = mk(208, 8, None, "the report reaches the client %s at the latest" % day(5))
th = M._pred_text_horizon(p208)
C.check("#208 English '<date> at the latest' is read as a horizon hint",
        (th.strftime("%Y-%m-%d") if th else None) == day(5), f"actual={th}")

C.section("3. End-to-end on a synthetic predikciok.json (replaces the old real-data printout)")
store = {"next_id": 306, "predikciok": [
    mk(301, 30, day(6), "the parts kit arrives before the horizon"),
    mk(302, 10, None, "a stale claim nobody scored"),
    mk(303, 10, None, "the backup job runs by %s" % day(5)),
    mk(304, 10, None, 'quoted: defer_prediction(3, "%s")' % day(9)),
    dict(mk(305, 3, None, "already scored item"), status="scored", eredmeny="hit"),
]}
S.write(ROOT, M.PREDICTIONS_PATH, json.dumps(store, ensure_ascii=False, indent=2))
real = M._load_preds()
C.check("the store is read from the temp MEMORIA_ROOT", len(real["predikciok"]) == 5, real)
op, old, futo = M._pred_open_summary(real)
C.check("open=4 (the scored item is not open)", len(op) == 4, [p["id"] for p in op])
C.check("abandoned = #302, #303, #304 (no horizon field, older than 7 days)",
        sorted(p["id"] for p in old) == [302, 303, 304], [p["id"] for p in old])
C.check("pending = #301 (explicit future horizon)", [p["id"] for p in futo] == [301], [p["id"] for p in futo])
labels = {p["id"]: M._pred_horizon_cimke(p) for p in op}
C.check("#301 label: ⏳until <horizon>", labels[301].strip() == "⏳until %s" % day(6), labels[301])
C.check("#302 label: empty (no horizon, no date)", labels[302] == "", labels[302])
C.check("#303 label: [?text: date] (horizon-form text date)", labels[303].strip() == "[?text: %s]" % day(5),
        labels[303])
C.check("#304 label: [~mentions: date] (a mention, not a horizon)",
        labels[304].strip() == "[~mentions: %s]" % day(9), labels[304])
cal = M.get_calibration()
C.check("get_calibration marks the pending item with ⏳ and the abandoned ones with 🔴",
        "⏳ **#301**" in cal and "🔴 **#302**" in cal, cal)
C.check("...and points out the text-horizon hint (#303) before calling it abandoned",
        "#303 (%s)" % day(5) in cal, cal)

C.finish()
