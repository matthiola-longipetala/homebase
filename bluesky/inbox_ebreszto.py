"""One-shot waker: exits as soon as a NEW mention/reply/quote lands in the guard's inbox.

It deliberately does NOT print the foreign text — only the type and the author's handle.
The content is read afterwards with mutat.py, framed (external data, not an instruction).
"""
import json, os, sys, time
# The guard's shared inbox. The guard root comes from BSKY_GUARD_ROOT (default C:\BskyGuard, the same
# default as the guard itself; see docs/bluesky_guard_INSTALL.md).
UT = os.path.join((os.environ.get("BSKY_GUARD_ROOT") or r"C:\BskyGuard"), "kozos", "be", "inbox.jsonl")
FIGYELT = {"mention", "reply", "quote"}

def idk():
    ki = set()
    try:
        with open(UT, encoding="utf-8") as f:
            for sor in f:
                try:
                    ki.add(json.loads(sor)["id"])
                except Exception:
                    pass
    except OSError:
        pass
    return ki

ismert = idk()
while True:
    time.sleep(30)
    try:
        with open(UT, encoding="utf-8") as f:
            sorok = f.readlines()
    except OSError:
        continue
    ujak = []
    for sor in sorok:
        try:
            t = json.loads(sor)
        except Exception:
            continue
        if t.get("id") in ismert:
            continue
        ismert.add(t.get("id"))
        if t.get("tipus") in FIGYELT:
            handle = "".join(c for c in str(t.get("szerzo_handle", ""))[:60] if c.isalnum() or c in ".-")
            ujak.append("%s <- %s" % (t.get("tipus"), handle))
    if ujak:
        print("BLUESKY | new item in the inbox: " + "; ".join(ujak[:5]), flush=True)
        sys.exit(0)
