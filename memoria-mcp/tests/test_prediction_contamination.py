# -*- coding: utf-8 -*-
"""CONTAMINATED (non-measurement) prediction marking -- FUNCTIONAL test.

Built on two lessons: "a verification that a failure would also pass" and
"where the test stubs, the test is blind":
- the logic under test (score_prediction, get_calibration) is the REAL server
  code, not a copy or a rewrite;
- ONLY the store (`_load_preds` / `_save_preds`) is replaced -- that is the
  right test seam ("a module that cannot be redirected cannot be tested");
- every case asserts an EXPECTED value that would look DIFFERENT on failure.

Storage keys (`eredmeny` = outcome, `konfidencia` = confidence, `tipus` = kind,
`szennyezett` = contaminated, `szennyezes_indok` = contamination reason,
`szennyezes_utolag` = marked later) are the server's JSON field names.

NEGATIVE CONTROL: `--backup` runs against a pre-patch
`server_backup_pre_szennyezett_*.py` (if present); there the new cases fail.

Run:  python -X utf8 test_prediction_contamination.py
"""
import re
import sys
from datetime import datetime, timedelta

import _support as S

NEG_REQUESTED = "--backup" in sys.argv
server_path = S.find_backup("server_backup_pre_szennyezett_*.py") if NEG_REQUESTED else None
C = S.Checker("Contaminated (non-measurement) predictions", negative=server_path is not None)
if NEG_REQUESTED and server_path is None:
    C.skip_no_backup()
M = S.load_server(S.make_memory_root("contam_"), server_path=server_path)
print(f"source: {(server_path or S.SERVER_PATH).name}")

# --- the STORE is the stub, not the logic ---
STORE = {"predikciok": [], "next_id": 1}
_saves = []
M._load_preds = lambda: STORE
M._save_preds = lambda d: _saves.append(len(d["predikciok"]))
score_prediction = M.score_prediction
get_calibration = M.get_calibration

now = datetime.now()


def mk(pid, conf, outcome, kind="world", **extra):
    p = {"id": pid, "konfidencia": conf, "tipus": kind, "allitas": "a%d" % pid,
         "status": "scored", "eredmeny": outcome,
         "created": (now - timedelta(days=3)).isoformat(timespec="seconds"),
         "scored": (now - timedelta(days=3)).isoformat(timespec="seconds")}
    p.update(extra)
    return p


def reset(preds, nxt=100):
    STORE["predikciok"] = preds
    STORE["next_id"] = nxt


HIT_RE = r"actual hit rate: \*\*(\d+)%\*\*"
BRIER_RE = r"Brier score: \*\*([\d,\.]+)\*\*"

# =====================================================================
C.section("[A] score_prediction -- reason required, basic behaviour")
# 1. contaminated WITHOUT a reason -> ERROR, and NOTHING changes
reset([mk(1, 90, "hit")])
STORE["predikciok"][0]["status"] = "open"
r = score_prediction(1, "hit", contaminated=True)
C.check("contaminated without a reason = ERROR", r.startswith("ERROR") and "contamination_reason" in r, r[:120])
C.check("...and the item was NOT closed", STORE["predikciok"][0]["status"] == "open")

# 2. fresh scoring, contaminated
reset([dict(mk(2, 90, "hit"), status="open", eredmeny=None)])
r = score_prediction(2, "hit", contaminated=True, contamination_reason="I had already seen the outcome")
p = STORE["predikciok"][0]
C.check("fresh scoring: contaminated=True", p.get("szennyezett") is True)
C.check("fresh scoring: reason saved", p.get("szennyezes_indok") == "I had already seen the outcome")
C.check("fresh scoring: marked later=False", p.get("szennyezes_utolag") is False)
C.check("fresh scoring: the message SAYS it", "NON-MEASUREMENT" in r, r[:160])

# 3. normal scoring -> NO contaminated key (no side effect)
reset([dict(mk(3, 70, "miss"), status="open", eredmeny=None)])
r = score_prediction(3, "miss")
C.check("normal scoring untouched", not STORE["predikciok"][0].get("szennyezett"))

C.section("[B] later marking of an ALREADY scored item")
reset([mk(4, 92, "hit")])
r = score_prediction(4, "hit", contaminated=True, contamination_reason="the order was broken")
p = STORE["predikciok"][0]
C.check("a scored item can be marked later", p.get("szennyezett") is True, r[:160])
C.check("...marked later=True", p.get("szennyezes_utolag") is True)
C.check("...the outcome is UNCHANGED", p.get("eredmeny") == "hit")

# it cannot be overwritten twice
r2 = score_prediction(4, "hit", contaminated=True, contamination_reason="another reason")
C.check("a second marking does not overwrite", p.get("szennyezes_indok") == "the order was broken"
        and "ALREADY marked as a non-measurement" in r2, r2[:160])

reset([mk(5, 80, "hit")])
r = score_prediction(5, "hit", owner_verdict="agrees", owner_note="ok")
C.check("REGRESSION: a late owner_verdict works",
        STORE["predikciok"][0].get("owner_verdict") == "agrees" and "Outside verdict" in r, r[:160])
r = score_prediction(5, "hit", owner_verdict="nuances")
C.check("REGRESSION: an existing verdict is not overwritten",
        STORE["predikciok"][0]["owner_verdict"] == "agrees" and "ALREADY HAS" in r, r[:160])
# both at once
reset([mk(6, 80, "hit")])
r = score_prediction(6, "hit", owner_verdict="agrees",
                     contaminated=True, contamination_reason="not a measurement")
p = STORE["predikciok"][0]
C.check("verdict + contamination both go through in one call",
        p.get("owner_verdict") == "agrees" and p.get("szennyezett") is True, r[:160])
# scored item, nothing extra -> the old message
reset([mk(7, 80, "hit")])
r = score_prediction(7, "hit")
C.check("REGRESSION: scored item, nothing extra -> the old message", "is already scored" in r, r[:160])

C.section("[C] get_calibration -- the exclusion WORKS and SHOWS")
# 4 clean items: 70% confidence, 2 hit / 2 miss -> hit rate 50%
clean = [mk(10, 70, "hit"), mk(11, 70, "hit"), mk(12, 70, "miss"), mk(13, 70, "miss")]
reset(list(clean))
out_clean = get_calibration()
m = re.search(HIT_RE, out_clean)
C.check("baseline: hit rate 50%", bool(m) and m.group(1) == "50", out_clean[:300])
C.check("baseline: no exclusion block", "NOT A MEASUREMENT" not in out_clean)

# + one contaminated, 100% guaranteed hit
reset(clean + [mk(14, 100, "hit", szennyezett=True,
                  szennyezes_indok="I had already seen the outcome", szennyezes_utolag=True)])
out = get_calibration()
m = re.search(HIT_RE, out)
C.check("the contaminated item does NOT raise the hit rate", bool(m) and m.group(1) == "50",
        (m.group(0) if m else out[:300]))
C.check("n is the number of clean items", "**4 scored prediction(s).**" in out, out[:300])
C.check("the exclusion block APPEARS", "Excluded from the record — NOT A MEASUREMENT (1 item(s))" in out, out)
C.check("...the REASON is printed", "I had already seen the outcome" in out)
C.check("...its outcome shows too", "**#14** [hit] 100%" in out, out)
C.check("...it says it was marked later", "marked later" in out)
C.check("...the number computed TOGETHER is there too", "Computed TOGETHER with the excluded ones: n=5" in out, out)
m2 = re.search(r"TOGETHER with the excluded ones: n=5 · hit (\d+)%", out)
C.check("...and the together-number REALLY is higher (60%)", bool(m2) and m2.group(1) == "60",
        (m2.group(0) if m2 else "missing"))

# Brier: the contaminated item cannot improve the Brier score either
b_c = re.search(BRIER_RE, out_clean)
b_u = re.search(BRIER_RE, out)
C.check("the Brier score is unchanged next to the contaminated item",
        bool(b_c and b_u) and b_c.group(1) == b_u.group(1),
        "%s vs %s" % (b_c and b_c.group(1), b_u and b_u.group(1)))

# the band table does not contain it either
C.check("the 90-100 band does NOT appear (only the contaminated item would fall there)",
        "| 90-100% |" not in out)

# fast list: contaminated + fast must not land in the suspicious list
reset(clean + [mk(15, 100, "hit", szennyezett=True, szennyezes_indok="x", gyors=True)])
out3 = get_calibration()
C.check("the contaminated item is not in the 'suspiciously fast' list",
        "suspiciously fast" not in out3, out3[-300:])

# every item contaminated -> empty branch, but the block is visible THEN too
reset([mk(20, 90, "hit", szennyezett=True, szennyezes_indok="all of it")])
out4 = get_calibration()
C.check("empty branch: no number, but the exclusion SHOWS",
        "No scored (measurement) prediction yet" in out4 and "NOT A MEASUREMENT (1 item(s))" in out4, out4[:400])

# works with the kind filter too
reset(clean + [mk(21, 100, "hit", szennyezett=True, szennyezes_indok="y")])
out5 = get_calibration(kind="world")
C.check("the exclusion works with the kind filter too",
        "**4 scored prediction(s).**" in out5 and "NOT A MEASUREMENT (1 item(s))" in out5, out5[:300])

C.finish()
