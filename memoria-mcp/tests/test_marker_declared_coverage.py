# -*- coding: utf-8 -*-
"""DECLARED marker coverage (the follow-up `covers` field) + the token-magnet guard.

Why this test exists: lexical coverage scoring failed on its first live run.
A follow-up whose note DOCUMENTS the marker-coverage problem itself won most
of the lexical tags, because its note quotes the corpus identifiers. Four
weighting schemes were measured on the same corpus -- none helped. It is a
REPRESENTATIONAL limit: bag-of-words cannot tell "I follow this marker" from
"I quote this marker". The answer: a structured `covers` field (a
declaration), plus a guard that SAYS when lexical tagging has degenerated.

Band codes such as "deklaralt", "sor-pontos" (line-exact), "fajl-szintu"
(file-level) and "fajl-szintu-elavult" (stale file-level) are the server's
internal identifiers and are asserted as such.

Run:  python -X utf8 test_marker_declared_coverage.py
"""
import json

import _support as S

C = S.Checker("Declared marker coverage (`covers`)")
ROOT = S.make_memory_root("declcov_")
M = S.load_server(ROOT)
P = M._parse_covers_entry
D = M._declared_index
CF = M._declared_cover_for
T = M._coverage_tag

C.section("1. _parse_covers_entry forms")
C.check("path:line", P("robot-docs/lessons/sensing.md:65") == ("robot-docs/lessons/sensing.md", 65))
C.check("path L<line>", P("sensing.md L65") == ("sensing.md", 65))
C.check("no line = whole file", P("sensing.md") == ("sensing.md", None))
C.check("windows backslash -> /", P("projektek\\a.md:7") == ("projektek/a.md", 7))
C.check("trim + lower-casing", P("  KONTEXTUS/A.MD:3  ") == ("kontextus/a.md", 3))
C.check("empty -> None", P("") is None and P("   ") is None)

C.section("2. suffix matching, on a / boundary")
D1 = D([{"id": 58, "covers": ["sensing.md:65"]}])
C.check("a short path matches the full one", CF("robot-docs/lessons/sensing.md", 65, D1) is not None)
C.check("does NOT match oversensing.md (the / boundary protects it)",
        CF("robot-docs/lessons/oversensing.md", 65, D1) is None)
C.check("wrong line -> no match", CF("robot-docs/lessons/sensing.md", 66, D1) is None)
res = CF("robot-docs/lessons/sensing.md", 65, D1)
C.check("band='deklaralt', with the right id", res and res[0] == "deklaralt" and res[1] == 58, res)

C.section("3. file-level vs line-exact")
D2 = D([{"id": 7, "covers": ["a.md"]}, {"id": 9, "covers": ["a.md:12"]}])
C.check("an entry without a line applies to the whole file", CF("kontextus/a.md", 99, D2)[1] == 7)
C.check("line-exact BEATS file-level on the same line", CF("kontextus/a.md", 12, D2)[1] == 9)

C.section("4. the declared tag looks different from the lexical ones")
td = T(("deklaralt", 58, 99.0, ["sor-pontos"]))
C.check("declared tag: ✔ TRACKED + id", "✔ TRACKED" in td and "#58" in td, td)
C.check("the declared tag does NOT look like a guess", "maybe" not in td and "weak" not in td, td)
C.check("it says it is declared", "declared" in td, td)
te = T(("eros", 3, 5.0, ["bme280", "aaaaa", "bbbbb", "ccccc"]))
C.check("strong band unchanged, with evidence", "maybe covered" in te and "+1" in te, te)
tg = T(("gyenge", 4, 2.0, ["question", "analysis"]))
C.check("weak band unchanged", "CHECK" in tg, tg)

C.section("5. _clean_covers normalisation")
CC = M._clean_covers
C.check("empty -> []", CC(None) == [] and CC([]) == [])
C.check("string -> list", CC("a.md:1") == ["a.md:1"])
C.check("duplicates + trim", CC(["a.md", "a.md", " b.md "]) == ["a.md", "b.md"])
C.check("backslash normalisation", CC(["x\\y.md:2"]) == ["x/y.md:2"])

C.section("6. THE POINT: a quote can NOT act as a declaration")
# #60's note quotes '#58/sensing.md ... L65' verbatim, but its covers field is
# EMPTY -> it cannot get a declared tag.
D3 = D([
    {"id": 58, "covers": ["robot-docs/lessons/sensing.md:65"]},
    {"id": 60, "note": "the marker at #58/sensing.md L65 is the example", "covers": []},
])
hit = CF("robot-docs/lessons/sensing.md", 65, D3)
C.check("the real follower (#58) is returned", hit is not None and hit[1] == 58, hit)
C.check("#60's prose quote did NOT get into the index", all(rec[0] != 60 for rec in D3), D3)

C.section("6b. STALE FILE-LEVEL coverage")
# WHY: a FILE-LEVEL `covers` speaks for the content the declarer SAW. A new line
# added to the file later used to land in the "✔ TRACKED -- not a guess" band,
# although it was about something ELSE entirely, written AFTER the declaration.
DV = D([{"id": 73, "created": "2026-03-26", "covers": ["travel.md"]},
        {"id": 74, "created": "2026-03-26", "covers": ["travel.md:153"]}])
C.check("_declared_index carries the `created` date", len(DV[0]) == 4 and DV[0][3] == "2026-03-26", DV)
r_fresh = CF("projektek/travel.md", 99, DV, "2026-03-26")   # mtime == created
r_stale = CF("projektek/travel.md", 99, DV, "2026-03-27")   # mtime  > created
r_older = CF("projektek/travel.md", 99, DV, "2026-03-20")   # mtime  < created
r_nomt = CF("projektek/travel.md", 99, DV)                  # no mtime (old call form)
C.check("mtime == created -> stays strong file-level", r_fresh[3][0] == "fajl-szintu")
C.check("mtime  > created -> DEGRADES", r_stale[3][0] == "fajl-szintu-elavult")
C.check("mtime  < created -> does not degrade", r_older[3][0] == "fajl-szintu")
C.check("without mtime (old call) -> unchanged (backward compatible)", r_nomt[3][0] == "fajl-szintu")
C.check("the degraded band CARRIES the evidence (declaration + file date)",
        r_stale[3][1] == "2026-03-26" and r_stale[3][2] == "2026-03-27", r_stale)
# a LINE-EXACT declaration never degrades -- it is about one specific line
r_line = CF("projektek/travel.md", 153, DV, "2026-09-30")
C.check("a line-exact declaration does not degrade even if the file changed a lot since",
        r_line[3][0] == "sor-pontos" and r_line[1] == 74, r_line)
t_st, t_ok, t_ln = T(r_stale), T(r_fresh), T(r_line)
C.check("stale tag: says it has to be checked", "FILE-LEVEL" in t_st and "CHECK" in t_st, t_st)
C.check("stale tag: prints both dates", "2026-03-26" in t_st and "2026-03-27" in t_st, t_st)
C.check("a NON-stale file-level tag is unchanged", "not a guess" in t_ok and "CHECK" not in t_ok, t_ok)
C.check("the line-exact tag is unchanged", "not a guess" in t_ln and "sor-pontos" in t_ln, t_ln)

C.section("7. End-to-end: get_open_markers on a synthetic memory (declaration beats lexical guess)")
# Replaces the old smoke check on the real followups.json with a synthetic one
# that exercises the same path: followups.json -> open threads -> covers index
# -> tags in the get_open_markers output.
S.write(ROOT, "projektek/garden.md", "\n".join([
    "# Garden",
    "",
    "- TODO: replace the broken irrigation valve near the greenhouse",   # L3: declared by #1
    "",
    "- TODO: order compost for the raised vegetable beds",               # L5: only a CLOSED thread covers it
    "",
]))
S.write(ROOT, "projektek/notes.md", "# Notes\n\n- TODO: label the seed drawer properly\n")
fus = {"next_id": 5, "followups": [
    {"id": 1, "item": "Valve repair", "status": "open", "created": "2026-01-10",
     "note": "", "covers": ["garden.md:3"]},
    # A note that QUOTES the L3 marker almost verbatim: lexically the strongest
    # candidate, but it declares nothing.
    {"id": 2, "item": "Meta: marker radar review", "status": "open", "created": "2026-01-10",
     "note": "example quote: replace the broken irrigation valve near the greenhouse", "covers": []},
    {"id": 3, "item": "Compost (done)", "status": "resolved", "created": "2026-01-10",
     "note": "", "covers": ["garden.md:5"]},
    # File-level declaration from long ago: the file has changed since -> stale.
    {"id": 4, "item": "Seed drawer", "status": "open", "created": "2020-01-01",
     "note": "", "covers": ["notes.md"]},
]}
S.write(ROOT, "kontextus/followups.json", json.dumps(fus, ensure_ascii=False))
out = M.get_open_markers()
l3 = next((l for l in out.splitlines() if l.startswith("- L3:")), "")
l5 = next((l for l in out.splitlines() if l.startswith("- L5:")), "")
seed = next((l for l in out.splitlines() if "seed drawer" in l), "")
C.check("the L3 marker carries the DECLARED tag of #1", "✔ TRACKED: #1" in l3, out)
C.check("the quoting follow-up #2 does NOT win the declared line", "#2" not in l3, l3)
C.check("a CLOSED follow-up's covers are ignored (L5 is not tracked by #3)",
        l5 != "" and "#3" not in l5 and "TRACKED" not in l5, l5)
C.check("the header counts the declared cover", "1 DECLARED cover(s)" in out, out)
C.check("the stale file-level cover is tagged FILE-LEVEL ... CHECK",
        "FILE-LEVEL cover: #4" in seed and "CHECK" in seed, seed)
C.check("...and counted separately", "1 STALE FILE-LEVEL cover(s)" in out, out)
out2 = M.get_open_markers(include_covered=False)
C.check("include_covered=False hides the declared (tracked) marker", "irrigation valve" not in out2, out2)
C.check("...but NOT the stale file-level one (the declaration does not vouch for it)",
        "seed drawer" in out2, out2)
C.check("...and NOT the untracked one", "order compost" in out2, out2)

C.finish()
