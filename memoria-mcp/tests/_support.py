# -*- coding: utf-8 -*-
"""Shared helpers for the memoria-mcp test suite (this file is not a test itself).

Every test loads the REAL server.py through `load_server()`, which guarantees
isolation from any real memory:

- `MEMORIA_ROOT` is pointed at a fresh temporary folder BEFORE the module is
  executed (the server reads it at import time), and the config env vars
  (`MEMORIA_CONTEXT_FILE`, `MEMORIA_PATTERNS_FILE`, `MEMORIA_OWNER_NAME`) are
  removed, so a developer's own environment cannot leak into the results.
- server.py is COPIED into a temporary code folder and loaded from there.
  The server derives the "own code" folder watched by `get_unlogged_changes`
  from its own location (`__file__`); loading the copy keeps it inside the
  temp folder too. (The default audit database lives in `<MEMORIA_ROOT>/_audit/`.) The copy is byte-identical; its mtime is
  set far in the past so it never shows up as a "changed file" in the radars.
- `MEMORIA_AUDIT_DB` is also set explicitly to a temp path.
- `sys.dont_write_bytecode` is set, so no __pycache__ appears next to the
  real server.py.
- The `mcp` package is replaced by a tiny stub, so the suite runs without the
  MCP SDK installed (the decorator just returns the plain function).

All temporary folders are removed at interpreter exit.
"""
import atexit
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

sys.dont_write_bytecode = True

TESTS_DIR = Path(__file__).resolve().parent
SERVER_DIR = TESTS_DIR.parent
SERVER_PATH = SERVER_DIR / "server.py"

_TEMP_DIRS = []
_CONFIG_ENV = ("MEMORIA_CONTEXT_FILE", "MEMORIA_PATTERNS_FILE", "MEMORIA_OWNER_NAME",
               "MEMORIA_AUDIT_DB")
# The server copy gets this mtime (seconds ago): far outside every radar window (max 60 days).
_OLD_MTIME_AGE = 400 * 86400


def _cleanup():
    for d in _TEMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup)


def temp_dir(prefix="memoria_test_") -> Path:
    """A fresh temporary folder, removed at exit."""
    d = Path(tempfile.mkdtemp(prefix=prefix))
    _TEMP_DIRS.append(str(d))
    return d


def make_memory_root(prefix="memoria_test_", subdirs=("kontextus", "projektek", "naplo",
                                                     "archiv", "eszkozok", "claude_sajat")) -> Path:
    """A temporary memory root with the usual sub-folders."""
    root = temp_dir(prefix) / "memoria"
    root.mkdir()
    for sub in subdirs:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def is_inside_temp(path) -> bool:
    """True if `path` lies inside the system temp folder."""
    tmp = os.path.realpath(tempfile.gettempdir())
    p = os.path.realpath(str(path))
    return p == tmp or p.startswith(tmp + os.sep)


def stub_mcp():
    """Install a minimal stand-in for `mcp.server.fastmcp` (framework, not code under test)."""
    pkg = types.ModuleType("mcp")
    srv = types.ModuleType("mcp.server")
    fast = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, *a, **k):
            pass

        def tool(self, *a, **k):
            def deco(fn):
                return fn
            return deco

        def run(self, *a, **k):
            pass

    fast.FastMCP = FastMCP
    sys.modules.update({"mcp": pkg, "mcp.server": srv, "mcp.server.fastmcp": fast})


_LOAD_COUNTER = [0]


def prepare_server_copy(server_path=None) -> Path:
    """Copy server.py (or `server_path`) into a fresh temp code folder; return that folder.

    The copy is named `server.py` and gets an old mtime (see the module docstring).
    """
    src = Path(server_path or SERVER_PATH)
    if not src.exists():
        raise FileNotFoundError(f"server file not found: {src}")
    code_dir = temp_dir("memoria_code_") / "memoria-mcp"
    code_dir.mkdir()
    copy = code_dir / "server.py"
    shutil.copyfile(src, copy)
    old = time.time() - _OLD_MTIME_AGE
    os.utime(copy, (old, old))
    return code_dir


def redirect_env(root, code_dir, env=None):
    """Point MEMORIA_ROOT (and the audit DB) at temp locations; drop config env vars."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not is_inside_temp(root):
        raise RuntimeError(f"refusing to load the server on a non-temporary root: {root}")
    for k in _CONFIG_ENV:
        os.environ.pop(k, None)
    os.environ["MEMORIA_ROOT"] = str(root)
    os.environ["MEMORIA_AUDIT_DB"] = str(Path(code_dir) / "data" / "memoria_audit.db")
    for k, v in (env or {}).items():
        os.environ[k] = v


def load_server(root, server_path=None, name=None, env=None):
    """Load a COPY of server.py (or of `server_path`) with MEMORIA_ROOT -> `root`.

    `env` may add extra environment variables for this load (e.g. a context
    file name). Returns the module object.
    """
    stub_mcp()
    code_dir = prepare_server_copy(server_path)
    redirect_env(root, code_dir, env)
    _LOAD_COUNTER[0] += 1
    modname = name or f"_memoria_server_{_LOAD_COUNTER[0]}"
    spec = importlib.util.spec_from_file_location(modname, code_dir / "server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    # Belt and braces: the module must really work on the temp root.
    if os.path.realpath(mod.MEMORIA_ROOT) != os.path.realpath(str(root)):
        raise RuntimeError(f"MEMORIA_ROOT not redirected: {mod.MEMORIA_ROOT}")
    return mod


def find_backup(pattern):
    """Newest pre-patch server backup matching `pattern` (tests/ or the server folder), or None.

    The backups are NOT shipped with the repository; the negative controls that
    need them are optional.
    """
    cands = []
    for d in (TESTS_DIR, SERVER_DIR):
        cands.extend(sorted(d.glob(pattern)))
    return cands[-1] if cands else None


def write(root, rel, text):
    """Write a UTF-8 text file under `root` (which must be a temp folder), creating folders."""
    p = Path(root) / rel
    if not is_inside_temp(p):
        raise RuntimeError(f"refusing to write outside the temp folder: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class Checker:
    """Collects check results and prints one RESULT line at the end.

    - check():  a normal assertion.
    - xfail():  a KNOWN server bug: the check is expected to fail. If it passes,
                it is reported as XPASS (the bug may be fixed: remove the xfail).
    - skip():   a case that cannot run in this environment (always says why).
    - negative mode: when the script runs against a pre-patch backup, the
                checks are EXPECTED to fail; the script then exits 0 only if at
                least one check failed ("a guard that is green on the buggy
                version is no guard").
    """

    def __init__(self, title, negative=False):
        self.title = title
        self.negative = negative
        self.passed = 0
        self.failed = []
        self.skipped = []
        self.xfailed = []
        self.xpassed = []
        print("=" * 66)
        print(title + ("   [NEGATIVE CONTROL: failures expected]" if negative else ""))
        print("=" * 66)

    def section(self, title):
        print(f"\n=== {title} ===")

    def check(self, label, cond, detail=""):
        if cond:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed.append(label)
            print(f"  FAIL  {label}")
            if detail:
                print(f"        -> {str(detail)[:600]}")
        return bool(cond)

    def xfail(self, label, cond, bug, detail=""):
        if cond:
            self.xpassed.append(label)
            print(f"  XPASS {label}  (known bug seems fixed: {bug} -- remove the xfail)")
        else:
            self.xfailed.append(label)
            print(f"  XFAIL {label}  (known server bug: {bug})")
            if detail:
                print(f"        -> {str(detail)[:300]}")

    def skip(self, label, reason):
        self.skipped.append(label)
        print(f"  SKIP  {label} -- {reason}")

    def skip_no_backup(self, what="negative control"):
        """A requested/embedded negative control whose pre-patch backup is not shipped."""
        self.skipped.append(what)
        print(f"  SKIP (no pre-patch backup) {what} -- the main checks still run against server.py")

    def finish(self):
        print("\n" + "=" * 66)
        print(f"RESULT: {self.passed} passed, {len(self.failed)} failed, "
              f"{len(self.skipped)} skipped, {len(self.xfailed)} xfail, "
              f"{len(self.xpassed)} xpass")
        if self.failed:
            print("FAILED: " + "; ".join(self.failed))
        if self.negative:
            if self.failed:
                print("NEGATIVE CONTROL OK: the pre-patch code fails these checks.")
                sys.exit(0)
            print("NEGATIVE CONTROL BROKEN: the pre-patch code passes every check.")
            sys.exit(1)
        sys.exit(1 if self.failed else 0)
