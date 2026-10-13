# -*- coding: utf-8 -*-
"""Associative recall — the client for the watchers ("kliens"). Standard library ONLY (no torch, no venv needed).

Use in a watcher (the watchers in karbantartas/ do this when EMLEK_INDEX_DIR points to this folder):
    sys.path.insert(0, "{REPO}/emlek_index")
    from emlek_kliens import emlek_sor
    s = emlek_sor(incoming_text)        # "" when there is no memory, or the server is not running
    if s:
        print(s, flush=True)

Never raises, and waits at most `idokorlat` ("time limit") seconds: the watcher matters more than the memory.
Server address: EMLEK_URL (default http://127.0.0.1:8770).
"""
import os
import urllib.parse
import urllib.request

ALAP_URL = os.environ.get("EMLEK_URL", "http://127.0.0.1:8770")


def emlek_sor(szoveg, idokorlat=3.0, url=ALAP_URL):
    try:
        cel = f"{url}/sor?q={urllib.parse.quote(szoveg[:2000])}"
        with urllib.request.urlopen(cel, timeout=idokorlat) as v:
            if v.status != 200:
                return ""
            s = v.read().decode("utf-8").strip()
            return s if s.startswith("[MEMORY]") else ""
    except Exception:
        return ""


if __name__ == "__main__":
    import sys
    print(emlek_sor(" ".join(sys.argv[1:]) or "the taste of gummy bears") or "(empty)")
