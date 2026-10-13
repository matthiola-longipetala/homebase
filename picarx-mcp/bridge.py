#!/usr/bin/env python3
# bridge.py -- temporary HTTP command bridge for the duration of a chat session
# Why it exists: from the Windows-MCP PowerShell context ssh.exe does not run (silent 46 ms exit),
# so the chat agent drives the robot through this bridge with Invoke-WebRequest.
#
# ⚠️ WHAT IT IS: with /run switched on, this is a REMOTE SHELL as the Pi user (the request body runs
# with shell=True). Treat it like ssh access. It is a gate: OFF unless the owner starts it, knowing that.
#
# Safety switches (all from the environment, nothing hard-coded):
#   PICARX_BRIDGE_TOKEN   required; a long random string, sent in the X-Token header (compared in constant time)
#   PICARX_BRIDGE_BIND    the address to listen on (default 127.0.0.1 = this Pi only; set the Pi's LAN IP
#                         to reach it from the laptop — never 0.0.0.0 on a network you don't control)
#   PICARX_BRIDGE_ALLOW   comma-separated client IPs allowed to connect (e.g. the laptop's IP); empty = any
#   PICARX_BRIDGE_RUN=1   switches on POST /run (shell commands). Default OFF.
#   PICARX_BRIDGE_FILE=1  switches on GET /file (read a file under the project folder or /tmp). Default OFF.
#   PICARX_BRIDGE_PORT    default 8731
# Plain HTTP: the token travels unencrypted, so use it only on your own home network.
# It disappears when the robot is switched off. Manual stop: POST /quit (with token), or kill the process.
import hmac, json, os, re, signal, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

TOKEN = os.environ.get("PICARX_BRIDGE_TOKEN") or ""
if len(TOKEN) < 16:
    raise SystemExit("bridge.py: PICARX_BRIDGE_TOKEN is not set (or shorter than 16 characters) -- "
                     "the bridge refuses to start without a real token.")
BIND = os.environ.get("PICARX_BRIDGE_BIND") or "127.0.0.1"
PORT = int(os.environ.get("PICARX_BRIDGE_PORT") or 8731)
ALLOW = {ip.strip() for ip in (os.environ.get("PICARX_BRIDGE_ALLOW") or "").split(",") if ip.strip()}
RUN_ON = os.environ.get("PICARX_BRIDGE_RUN") == "1"
FILE_ON = os.environ.get("PICARX_BRIDGE_FILE") == "1"
PROJECT = os.path.realpath(os.path.expanduser("~/picarx-mcp"))
FILE_ROOTS = (PROJECT, os.path.realpath("/tmp"))
SECRET_NAMES = re.compile(r"(^|[._-])(config|voice_config|secrets?)\.json$|\.(env|key|pem)$|^id_", re.I)


class H(BaseHTTPRequestHandler):
    def _allowed(self):
        if ALLOW and self.client_address[0] not in ALLOW:
            return False
        try:
            return hmac.compare_digest((self.headers.get("X-Token") or "").encode("utf-8", "surrogateescape"),
                                       TOKEN.encode("utf-8"))
        except Exception:
            return False

    def _deny(self):
        self.send_response(403); self.end_headers()

    def _ok(self, data, ctype="application/json; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._allowed():
            return self._deny()
        u = urlparse(self.path)
        if u.path == "/ping":
            info = {"pong": True, "who": "bridge.py on the Pi", "run": RUN_ON, "file": FILE_ON}
            return self._ok(json.dumps(info).encode("utf-8"))
        if u.path == "/file" and FILE_ON:
            p = os.path.realpath(parse_qs(u.query).get("path", [""])[0])
            if (not any(p == r or p.startswith(r + os.sep) for r in FILE_ROOTS) or not os.path.isfile(p)
                    or SECRET_NAMES.search(os.path.basename(p))):   # never hand out a key file
                self.send_response(404); self.end_headers(); return
            with open(p, "rb") as f:
                data = f.read()
            return self._ok(data, "application/octet-stream")
        self.send_response(404); self.end_headers()

    def do_POST(self):
        if not self._allowed():
            return self._deny()
        u = urlparse(self.path)
        try:
            n = max(0, min(int(self.headers.get("Content-Length", 0)), 1_000_000))
        except ValueError:
            n = 0
        body = self.rfile.read(n).decode("utf-8", "replace")
        if u.path == "/quit":
            self._ok(b'{"bye": true}')
            os._exit(0)
        if u.path == "/run" and RUN_ON:
            q = parse_qs(u.query)
            try:
                t = min(max(float(q.get("t", ["90"])[0]), 1.0), 600.0)
            except ValueError:
                t = 90.0
            if t != t:   # nan
                t = 90.0
            p = subprocess.Popen(body, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PROJECT,
                                 start_new_session=True)   # own process group: a timeout kills the whole tree
            try:
                so, se = p.communicate(timeout=t)
                out = {"rc": p.returncode,
                       "out": so.decode("utf-8", "replace"),
                       "err": se.decode("utf-8", "replace")}
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    p.kill()
                p.communicate()
                out = {"rc": -1, "out": "", "err": "TIMEOUT %.0fs" % t}
            return self._ok(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"bridge.py listening on {BIND}:{PORT} (run={'ON' if RUN_ON else 'off'}, file={'ON' if FILE_ON else 'off'}, "
          f"allowed clients: {', '.join(sorted(ALLOW)) or 'any'})", flush=True)
    ThreadingHTTPServer((BIND, PORT), H).serve_forever()
