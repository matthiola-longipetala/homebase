# -*- coding: utf-8 -*-
"""Handoff expiry (`_expire_daily_handoff`) -- WITH NEGATIVE CONTROLS.

What it proves: the dated line of the "morning handoff" section of _state.md
is EXPIRED in the rendered get_context() view when it is not from today. The
morning brief must not reuse yesterday's handoff as if it were today's; a
missing handoff has to be visible as missing (fail-closed). Text outside the
section must stay bit-identical.

The server PARSES two section headers: the Hungarian "ÁTADÓ A REGGELINEK"
and the English "MORNING HANDOFF" -- every case runs with both.

Run:  python -X utf8 test_handoff_expiry.py
"""
import re

import _support as S

C = S.Checker("Handoff expiry (_expire_daily_handoff)")
M = S.load_server(S.make_memory_root("handoff_"))
f = M._expire_daily_handoff

# `today` is passed explicitly, so the fixed dates below never rot.
TODAY = "2026-03-12"
HEADERS = {
    "hu": "## 📤 ÁTADÓ A REGGELINEK\n_(Written by the night shift.)_\n",
    "en": "## 📤 MORNING HANDOFF\n_(Written by the night shift.)_\n",
}
STALE = "STALE HANDOFF"
NO_DATED = "NO DATED HANDOFF LINE"

for lang, SEC in HEADERS.items():
    C.section(f"header: {lang}")

    # 1. TODAY's line -> unchanged
    b1 = SEC + "- **2026-03-12 · DEEDS LEDGER: yesterday 6 deeds · carried over 6/6 · missing: —**\n\n## Focus\nsomething\n"
    r1 = f(b1, TODAY)
    C.check(f"[{lang}] 1. today's line is unchanged", r1 == b1, repr(r1[-120:]))

    # 2. YESTERDAY's line -> STALE warning
    b2 = SEC + "- **2026-03-11 · DEEDS LEDGER: yesterday 5 deeds · carried over 4/5 · missing: X**\n\n## Focus\nsomething\n"
    r2 = f(b2, TODAY)
    C.check(f"[{lang}] 2a. the stale warning appears", STALE in r2, r2)
    C.check(f"[{lang}] 2b. the age is right (1 day)", re.search(r"\b1 days? old\b", r2) is not None, r2)
    C.check(f"[{lang}] 2c. the original line stays quoted (it does not vanish without a trace)",
            "DEEDS LEDGER: yesterday 5 deeds" in r2)
    C.check(f"[{lang}] 2d. the old bullet is no longer a STANDALONE line (only quoted inside the warning)",
            not any(l.startswith("- **2026-03-11") for l in r2.split("\n")), r2)
    C.check(f"[{lang}] 2e. the content after the section is intact", r2.rstrip().endswith("something"))

    # 3. section present, but NO dated line -> loud
    b3 = SEC + "- I wrote something, but without a date\n\n## Focus\nsomething\n"
    r3 = f(b3, TODAY)
    C.check(f"[{lang}] 3. no dated line -> loud", NO_DATED in r3, r3)

    # 5. NEGATIVE CONTROL: dated bullet OUTSIDE the section -> left alone
    b5 = (SEC + "- **2026-03-12 · DEEDS LEDGER: yesterday 6 deeds**\n\n"
          "## Where I am\n- **2026-03-11**: pointer to yesterday's shift log\n- **2026-03-10**: older\n")
    r5 = f(b5, TODAY)
    C.check(f"[{lang}] 5a. the 03-11 bullet outside the section is unchanged",
            "- **2026-03-11**: pointer to yesterday's shift log" in r5)
    C.check(f"[{lang}] 5b. no stale noise outside the section", STALE not in r5)
    C.check(f"[{lang}] 5c. full match (bit-identical)", r5 == b5)

    # 6. nonsense date -> does not raise, and expires it
    b6 = SEC + "- **2026-13-45 · DEEDS LEDGER: broken date**\n"
    try:
        r6 = f(b6, TODAY)
        C.check(f"[{lang}] 6. broken date: no exception, but expired",
                STALE in r6 and "unknown age" in r6, r6)
    except Exception as e:  # noqa: BLE001
        C.check(f"[{lang}] 6. broken date: no exception", False, f"exception: {e!r}")

    # 7. the section is at the END of the file (no ## after it) -> the loud line is added anyway
    b7 = SEC + "- no date here\n"
    C.check(f"[{lang}] 7. section at the end of the file -> loud line", NO_DATED in f(b7, TODAY))

    # 8. two handoff sections (should not happen) -> both handled, no crash
    b8 = SEC + "- **2026-03-11 · A**\n\n" + SEC + "- **2026-03-12 · B**\n"
    r8 = f(b8, TODAY)
    C.check(f"[{lang}] 8. two sections: one stale + one from today",
            r8.count(STALE) == 1 and "**2026-03-12 · B**" in r8, r8)

    # 9. IDEMPOTENCE: re-running on an already expired output does not multiply the warning
    r9 = f(f(b2, TODAY), TODAY)
    C.check(f"[{lang}] 9. idempotent (no duplication)", r9.count(STALE) == 1, r9)

C.section("header-independent")
# 4. NEGATIVE CONTROL: no handoff section -> unchanged
b4 = "## Focus\nblah 2026-03-01 a date in prose\n- **2026-03-11 · some bullet**\n"
C.check("4. no section -> bit-identical (negative control)", f(b4, TODAY) == b4)

C.finish()
