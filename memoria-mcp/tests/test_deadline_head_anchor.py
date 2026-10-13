# -*- coding: utf-8 -*-
"""Head-of-bullet date anchor in get_deadlines -- regression test.

What it measures: inside the deadline section, only the date at the HEAD of a
bullet (`- **YYYY-MM-DD: ...`) is a deadline; ISO dates in the BODY of a bullet
are references (history, reasoning, file names, period bounds). Before this
anchor, one exam bullet produced three deadline rows. The filter must not be
silent: future body dates go into their own advisory block.

The fixture reconstructs the real failure shape (one event, several ISO dates
in its BODY), plus negative controls so that the anchor does not drop a REAL
deadline. All dates are relative to today, so the fixture never rots.

Negative control (optional): if a pre-patch `server_backup_pre_head_anchor_*.py`
is present (tests/ or the server folder), the old code is run on the same input
and MUST produce the phantom deadlines. The backup is not shipped; without it
those checks are skipped and the main checks still run.

The Hungarian section header "SURGOS / KOZELGO HATARIDOK" is a parsing marker;
the English "URGENT / UPCOMING DEADLINES" variant is tested too.

Run:  python -X utf8 test_deadline_head_anchor.py
"""
import re
from datetime import datetime, timedelta

import _support as S

C = S.Checker("Head-of-bullet date anchor (get_deadlines)")


def day(n):
    return (datetime.now() + timedelta(days=n)).strftime("%Y-%m-%d")


# head dates
A, B, Cc = day(18), day(104), day(24)
# body dates of the B bullet (period bounds) and a prose reference
B1, B2, B3, B4 = day(62), day(69), day(116), day(83)
D = day(168)       # file-name style prose reference in the body of the Cc bullet
E = day(209)       # a future date OUTSIDE the section
PAST = day(-68)
BODY_DATES = (B1, B2, B3, B4, D)


def situation(header):
    return f"""# Current situation

{header}
- **{A}: QUARTERLY REPORT -- final version due** (verified from the team's planning table)
- **★ {B}: ANNUAL AUDIT STARTS** (window of about ten days). From the same source:
  audit period {B1} - {B3}, office closure {B2} - {B4}.
- **{Cc}: SAFETY TRAINING** (busy day)
  _(I deliberately do not write the file name: {D} would be the next round.)_

## PROJECTS
- The draft went to the reviewer on {PAST}.
- Next planned review meeting: {E} (a reference, NOT a deadline by convention).
"""


LABELS_NEW = ("OVERDUE", "[!!!] CRITICAL", "[!!] URGENT", "[!] UPCOMING", "LATER")
LABELS_OLD = ("LEJART", "[!!!] KRITIKUS", "[!!] SURGOS", "[!] KOZELGO", "TAVOLI")


def dl_lines(txt):
    out = []
    for ln in txt.splitlines():
        if "|" in ln and ln.split("|")[0].strip() in LABELS_NEW + LABELS_OLD:
            out.append(ln)
    return out


def dl_dates(txt):
    # from the "| 2026-10-01 (11 days) |" field only the ISO date is needed
    return [l.split("|")[1].strip().split()[0] for l in dl_lines(txt)]


def context_file_of(mod, path):
    """The context-file path a server version reads (old versions hard-coded it)."""
    if hasattr(mod, "CONTEXT_FILE"):
        return mod.CONTEXT_FILE
    m = re.search(r'["\'](kontextus/\w*helyzet\w*\.md)["\']', path.read_text(encoding="utf-8"))
    return m.group(1) if m else "kontextus/helyzet.md"


OLD_SRV = S.find_backup("server_backup_pre_head_anchor_*.py")

for header in ("## ⏰ SURGOS / KOZELGO HATARIDOK", "## ⏰ URGENT / UPCOMING DEADLINES"):
    tag = "hu" if "SURGOS" in header else "en"
    root = S.make_memory_root(f"anchor_{tag}_")
    new = S.load_server(root)
    S.write(root, new.CONTEXT_FILE, situation(header))
    n = new.get_deadlines()
    n_dates = dl_dates(n)

    C.section(f"[{tag}] 1. The OLD code's phantoms (the bug is reproduced on the pre-patch code)")
    if OLD_SRV is None:
        C.skip_no_backup(f"[{tag}] old-code phantom checks")
    else:
        old = S.load_server(root, server_path=OLD_SRV)
        S.write(root, context_file_of(old, OLD_SRV), situation(header))
        o_dates = dl_dates(old.get_deadlines())
        C.check(f"[{tag}] OLD: the body dates are phantom deadlines (>=3 of the 4 period bounds)",
                sum(d in o_dates for d in (B1, B2, B3, B4)) >= 3, o_dates)
        C.check(f"[{tag}] OLD: the prose file-name reference became a deadline too", D in o_dates, o_dates)

    C.section(f"[{tag}] 2. The current code: only head-of-bullet dates are deadlines")
    C.check(f"[{tag}] exactly 3 deadlines (A, the ★ B, Cc)", sorted(n_dates) == sorted([A, B, Cc]), n)
    C.check(f"[{tag}] the ★-decorated B IS in (it did not drop out)", B in n_dates, n_dates)
    C.check(f"[{tag}] 0 phantoms from the body", not any(d in n_dates for d in BODY_DATES), n_dates)

    C.section(f"[{tag}] 3. The filter is NOT silent: an advisory block for the body dates")
    has_block = "Future date in the BODY of a deadline-section bullet" in n
    C.check(f"[{tag}] there is an advisory block", has_block, n)
    blk = n.split("Future date in the BODY")[1] if has_block else ""
    for d in BODY_DATES:
        C.check(f"[{tag}] {d} appears in the advisory block", d in blk)
    C.check(f"[{tag}] the out-of-section {E} stays in the old 'might be in the wrong place' block",
            E in n and "Might be in the wrong place" in n, n)

C.section("4. _is_head_date negative/positive controls")
M = S.load_server(S.make_memory_root("anchor_unit_"))
HEAD_POS = [("- **2026-10-01: X**", "2026-10-01"),
            ("- **★ 2099-03-15: X**", "2099-03-15"),
            ("  - 2026-11-06 X", "2026-11-06"),
            ("- ⚠️ **2026-12-15: X**", "2026-12-15")]
HEAD_NEG = [("  _(I do not write the file name: 2027-03-30 would be.)_", "2027-03-30"),
            ("- **2026-10-01: X** (history: went out on 2026-08-06)", "2026-08-06"),
            ("- the deadline is 2026-11-20, but it may slip", "2026-11-20"),
            ("(2026-12-15) to the portal", "2026-12-15")]
for ln, d in HEAD_POS:
    C.check(f"head POSITIVE: {ln[:34]!r}", M._is_head_date(ln, d))
for ln, d in HEAD_NEG:
    C.check(f"head NEGATIVE: {ln[:34]!r}", not M._is_head_date(ln, d))

C.section("5. No regression on genuine deadlines (synthetic stand-in for a real context file)")
# Replaces the old comparison on the real context file: seven genuine deadline
# bullets in every style the convention allows, plus distractors that must NOT
# add rows (a body reference, a closed item, an out-of-section date).
g = [day(n) for n in (2, 5, 9, 15, 15, 30, 60)]
root = S.make_memory_root("anchor_regress_")
M2 = S.load_server(root)
S.write(root, M2.CONTEXT_FILE, "\n".join([
    "# Current situation", "",
    "## ⏰ URGENT / UPCOMING DEADLINES",
    f"- **{g[0]}: Plain bold deadline** (prepared since {day(-20)})",
    f"- **★ {g[1]}: Star-decorated deadline**",
    f"- ⚠️ **{g[2]}: Warning-decorated deadline**",
    f"- {g[3]}: bare bullet without bold",
    f"- **{g[4]} (evening): a second deadline on the same day**",
    f"* **{g[5]}: asterisk bullet**, the form arrives by {day(45)}",
    f"- **{g[6]}: far deadline**",
    f"- ✅ **{day(12)}: already handed in -- LEZÁRVA**",
    "",
    "## Notes",
    f"- Reference only: {day(75)} (not a deadline by convention).",
    "",
]))
out = M2.get_deadlines()
got = dl_dates(out)
C.check("exactly the 7 genuine deadline rows (two on the same day)", sorted(got) == sorted(g), out)
C.check("the body reference did not become a deadline", day(45) not in got and day(-20) not in got, got)
C.check("the closed item is not a deadline, but it is reported", day(12) not in got
        and "Filtered out as completed" in out, out)
if OLD_SRV is None:
    C.skip_no_backup("old-vs-new comparison on the genuine deadlines")
else:
    # Compared on the genuine bullets ONLY: the body reference above is exactly
    # what the old code turned into a phantom, so it is left out here.
    genuine = "\n".join(["# Current situation", "", "## ⏰ URGENT / UPCOMING DEADLINES"]
                        + [f"- **{d}: genuine deadline {i}**" for i, d in enumerate(g)] + [""])
    S.write(root, M2.CONTEXT_FILE, genuine)
    old2 = S.load_server(root, server_path=OLD_SRV)
    S.write(root, context_file_of(old2, OLD_SRV), genuine)
    C.check("OLD and NEW give the same genuine deadline list (0 regression)",
            sorted(dl_dates(old2.get_deadlines())) == sorted(dl_dates(M2.get_deadlines())) == sorted(g))

C.finish()
