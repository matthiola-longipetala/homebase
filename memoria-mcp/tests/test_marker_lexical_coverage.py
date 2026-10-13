# -*- coding: utf-8 -*-
"""Lexical follow-up coverage scoring of get_open_markers (`_best_coverage` / `_coverage_tag`).

Background: the old rule (">=2 shared >=5-letter tokens -> maybe covered: #N")
had a PRECISION of about 20% on a real corpus: of 5 printed tags 1 was real,
the rest rested on generic words. Null model: among a few hundred
(marker x open follow-up) pairs a few percent reach >=2 overlap by pure chance.

The scorer has three bands, EACH printed with its evidence:
  strong ("eros", >=4.0 points) -> `[~ maybe covered: #N · tokens]`
  weak ("gyenge", 2.0-3.9)      -> `[? weak word match: … — probably chance, CHECK]`
  none (<2.0)                   -> no tag
Identifier-like tokens (digit/underscore: bme280, vl53l1x) count DOUBLE.
("eros"/"gyenge" are the server's internal band codes.)

Run:  python -X utf8 test_marker_lexical_coverage.py
"""
import _support as S

C = S.Checker("Lexical marker coverage scoring")
M = S.load_server(S.make_memory_root("lexcov_"))
T = M._marker_tokens


def band(marker_line, fu_blob):
    """The band of comparing one marker line with ONE follow-up text."""
    res = M._best_coverage(T(marker_line), [(99, T(fu_blob))])
    return res[0] if res else "none"


C.section("1. REAL COVERAGE (must land in the strong band)")
# The one verified true pair of a measured corpus (many shared tokens, among them `bme280`).
fu58 = ("BME280 (0x77) I2C bus collision -- sanity filter into sentinel.py. "
        "The patch: sentinel.py should drop any BME280 reading outside the spec range "
        "(T < -40 or > 85 C; p < 300 or > 1100 hPa), and NOT turn it into an event.")
m58 = ("**TODO:** a sanity filter in `sentinel.py`: DROP any BME280 value outside the spec "
       "range (T < -40 or > 85 C; p < 300 or > 1100 hPa), do not turn it into an event")
C.check("#58 BME280 marker -> strong", band(m58, fu58) == "eros", band(m58, fu58))

C.section("2. MEASURED FALSE POSITIVES (weak band, NOT strong)")
b = band("OPEN QUESTION.** The analysis was written that same morning, only this paragraph stayed behind",
         "Reply from the reviewer about the report. The 5 QUESTION points in the letter: "
         "is the length of the analysis measured in pages or in characters")
C.check("'question'+'analysis' by chance -> weak", b == "gyenge", b)
b = band('battery_pct = "unknown"  # TODO: exact reading method in the Robot HAT v4 docs',
         "BME280 bus collision: corrupted register reading, because the sentinel and the "
         "battery guard touch the I2C bus at the same time on the robot")
C.check("'reading'+'robot' by chance -> weak", b == "gyenge", b)
b = band("REPORT -- final draft deadline (online form, agreed with the "
         "internal + external reviewer)",
         "Publication of the new audit checklist -- not expected before autumn; "
         "the internal and external reviewer are independent of it")
C.check("'internal'+'external'+'reviewer' by chance -> weak", b == "gyenge", b)

C.section("3. BELOW THRESHOLD (no tag at all)")
b = band("TODO: painting the fence in spring", "The deadline slipped, painting comes later")
C.check("1 shared token -> none", b == "none", b)
b = band("TODO: camera calibration", "The meeting time is not fixed yet")
C.check("0 shared tokens -> none", b == "none", b)

C.section("4. IDENTIFIER WEIGHT (a token with digits counts double)")
# Two identifier-like tokens (2+2=4.0) are ALREADY strong; two common nouns (1+1=2.0) only weak.
b = band("TODO: recalibrate the vl53l1x and the bno055",
         "The vl53l1x laser distance and the bno055 IMU offsets drifted")
C.check("2 identifier tokens -> strong", b == "eros", b)
b = band("TODO: using the camera and the sonar at once",
         "The camera image and the sonar signal contradict each other")
C.check("2 common-noun tokens -> weak", b == "gyenge", b)

C.section("5. THE BEST MATCH WINS (not the first)")
# Regression: the FIRST >=2 hit used to win, in listing order. The weak
# follow-up below DOES reach the weak band on its own, so it is a real competitor.
weak_fu = (11, T("an unrelated thread that mentions a sanity filter only in passing"))
strong_fu = (58, T(fu58))
C.check("the weak competitor really matches on its own (the race is real)",
        (M._best_coverage(T(m58), [weak_fu]) or ("none",))[0] == "gyenge",
        M._best_coverage(T(m58), [weak_fu]))
res = M._best_coverage(T(m58), [weak_fu, strong_fu])
C.check("the follow-up with the higher score wins", (res[1] if res else None) == 58, res)

C.section("6. THE TAG PRINTS ITS EVIDENCE")
tag = M._coverage_tag(M._best_coverage(T(m58), [strong_fu]))
C.check("the strong tag contains #58", "#58" in tag, tag)
C.check("the strong tag contains the bme280 evidence", "bme280" in tag, tag)
C.check("the strong tag says 'maybe covered'", "maybe covered" in tag, tag)
weak_marker = "OPEN QUESTION about the analysis"
weak_fu45 = (45, T("the question part of the analysis is decided by the reviewer"))
gy = M._coverage_tag(M._best_coverage(T(weak_marker), [weak_fu45]))
C.check("the weak tag says CHECK", "CHECK" in gy, gy)
C.check("the weak tag does NOT say 'covered'", "covered" not in gy, gy)

C.section("7. include_covered=False does NOT hide a weak match")
# The caller decides with `covered[0] in ("eros", "deklaralt")`; here we check that
# the weak band really is marked weak.
g = M._best_coverage(T(weak_marker), [weak_fu45])
C.check("the band of the weak match is 'gyenge' (weak)", (g[0] if g else None) == "gyenge", g)

C.finish()
