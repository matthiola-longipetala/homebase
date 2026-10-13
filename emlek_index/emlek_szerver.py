# -*- coding: utf-8 -*-
"""Associative recall — the local server ("szerver"): the model loads once (~10 s), after that a
query takes ~0.1 s. It listens ONLY on 127.0.0.1; nothing goes out.

Start (in the background, from the venv):
  python -X utf8 emlek_szerver.py [--port 8770] [--archiv]
Endpoints:
  GET /sor?q=<text>              → one line of text ("[MEMORY] …" or empty)
  GET /json?q=<text>&k=3         → the hits as JSON
  GET /egeszseg                  → "ok <chunk count> <model>"   ("egészség" = health)
It notices the nightly index rebuild (emlek_epito.py) by itself (mtime); no restart needed.
The queried sentences are NOT logged: they are the family's words.
"""
import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emlek import Emlekezo  # noqa: E402

ZAR = threading.Lock()   # the model serves one request at a time


def kezelo_gyar(e):
    class Kezelo(BaseHTTPRequestHandler):
        def _ki(self, kod, szoveg, tipus="text/plain; charset=utf-8"):
            b = szoveg.encode("utf-8")
            self.send_response(kod)
            self.send_header("Content-Type", tipus)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            p = parse_qs(u.query)
            q = (p.get("q") or [""])[0].strip()
            try:
                if u.path == "/egeszseg":
                    return self._ki(200, f"ok {len(e.D)} {e.modell_nev}")
                if u.path in ("/sor", "/json"):
                    if not q:
                        return self._ki(200, "" if u.path == "/sor" else "[]")
                    kdb = int((p.get("k") or ["3"])[0])
                    with ZAR:
                        t = e.keres(q[:2000], k_db=kdb)
                    if u.path == "/sor":
                        return self._ki(200, e.formaz(t))
                    return self._ki(200, json.dumps(t, ensure_ascii=False),
                                    "application/json; charset=utf-8")
                return self._ki(404, "unknown endpoint")
            except Exception as ex:   # a watcher never gets a traceback, only a short error
                return self._ki(500, f"error: {type(ex).__name__}")

        def log_message(self, *a):   # the queried sentences are NOT logged
            pass
    return Kezelo


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--archiv", action="store_true")
    a = ap.parse_args()
    e = Emlekezo(archiv=a.archiv)
    e._modell()                       # load at start, not at the first request
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), kezelo_gyar(e))
    print(f"emlek_szerver ready: 127.0.0.1:{a.port}, {len(e.D)} chunks", flush=True)
    srv.serve_forever()
