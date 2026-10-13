#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""staged_health.py — out-of-band health check for STAGED MCP-server patches.

============================================================================
WHY THIS EXISTS
============================================================================
A patch is "staged" when the file on disk already holds the new code but the
running server still keeps the old code in memory. At that point nothing
inside the running server tells you the truth about the file. A syntax error
or a duplicated tool name stays invisible until the next restart, and then it
is not one tool that fails but the WHOLE server.

The checking tool itself is stuck in the old world, so the check has to look
at the file from OUTSIDE. This script does NOT import the module (no fastmcp
dependency, runs anywhere); it reads the source with `ast`.

★ THE MOST USEFUL OUTPUT — the TOOL-COUNT DIFF:
  the number of @mcp.tool() functions in the files vs. the number of tools
  visible in the model's live tool list. If they differ, EXACTLY that many
  tools are staged, and the name diff tells you WHICH ones. This is more
  robust than any timestamp comparison, because it works even if the staged
  file would NOT START at all.

============================================================================
v2 — TWO BLIND SPOTS FIXED
============================================================================
For one server, v1 printed "0 tools ... ✅ clean — safe to restart". Both
claims were empty:

  (1) SINGLE-FILE ASSUMPTION. v1 parsed only `<project>/server.py`. But
      picarx-mcp, for example, registers its tools through helper calls like
      `register_*_tool(mcp, config)`, and the real tools live in
      `tools/*.py`, so all of them were invisible. A duplicated tool name
      staged into `tools/scan.py` would have passed as "✅ ALL CLEAN".
  (2) MODULE-LEVEL ASSUMPTION. v1 walked only `tree.body`, so it missed a
      `@mcp.tool()` nested INSIDE a helper function — exactly that pattern.

  ➜ v2 starts from server.py and walks the IMPORT GRAPH (only inside the
    project folder, so backup copies like `server_backup_*.py` drop out by
    themselves: nothing imports them), and uses `ast.walk` to find tool
    decorators at any depth. The duplicate-tool-name check now works ACROSS
    FILES.

★★ THE BIGGER LESSON — THE SILENT ZERO:
  the real bug was not the 4 missing tools but that the script reported its
  OWN blindness with a ✅. "0 tools found" and "0 problems found" got the same
  green tick. A missing result must be said out loud: since v2 a 0-tool
  result gets its own loud 🟡 BLIND SPOT bucket, and the summary line flags
  it too.

============================================================================
WHAT IT CHECKS
============================================================================
  1. syntax (ast.parse) on every reached file  -> 🔴 a restart would break it
  2. duplicated tool name (ACROSS FILES)       -> 🔴 the later one SILENTLY overrides
  3. duplicated module-level def (in one file) -> 🟡 a forgotten old version
  4. missing tool docstring                    -> 🟡 the model can't tell what it's for
  5. risky module-level call that runs at import time (open/run/load)
  6. 0 tools found                             -> 🟡 BLIND SPOT (the diff is useless)
  7. list of tool names                        -> used for the diff against the live list

============================================================================
USAGE
============================================================================
  python3 staged_health.py                  # all known MCP servers
  python3 staged_health.py <file.py> ...    # targeted (follows its imports too)
  python3 staged_health.py --names          # print tool names (for the diff)
  python3 staged_health.py --files          # which tool comes from which file

Exit code: 0 = clean · 1 = warning / blind spot · 2 = ERROR (a restart would break it)
"""
import ast
import collections
import os
import sys

# Our own MCP servers (paths relative to the parent folder of this script's folder)
DEFAULT_TARGETS = [
    "memoria-mcp/server.py",
    "telegram-mcp/server.py",
    "image-mcp/server.py",
    "video-mcp/server.py",
    "phonecall-mcp/server.py",
    "picarx-mcp/server.py",
]

_SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "node_modules", "archiv", "data"}


def _tool_name(fn):
    """Return the tool name if the function is decorated with @<something>.tool(...), otherwise None.

    Also works for nested definitions (inside a function) — v1 did not look at those.
    """
    for d in fn.decorator_list:
        f = d.func if isinstance(d, ast.Call) else d
        if isinstance(f, ast.Attribute) and f.attr == "tool":
            if isinstance(d, ast.Call):
                for kw in d.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        return kw.value.value
            return fn.name
    return None


# ---------------------------------------------------------------------------
# Import graph: which files RELIABLY belong to this server?
# ---------------------------------------------------------------------------
def _resolve(root, modname):
    """Module name -> file path inside the project folder, or None (external dependency)."""
    rel = modname.replace(".", os.sep)
    for cand in (os.path.join(root, rel + ".py"),
                 os.path.join(root, rel, "__init__.py")):
        if os.path.isfile(cand):
            return os.path.abspath(cand)
    return None


def _imports_of(tree, root, cur_file):
    """All imports in the file (at any depth); only the project-internal ones resolve later."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                base = os.path.dirname(cur_file)
                for _ in range(node.level - 1):
                    base = os.path.dirname(base)
                pkg = os.path.relpath(base, root).replace(os.sep, ".")
                pkg = "" if pkg == "." else pkg
                mod = f"{pkg}.{node.module}" if (pkg and node.module) else (node.module or pkg)
            else:
                mod = node.module or ""
            if mod:
                out.add(mod)
                for a in node.names:          # `from tools import status`
                    out.add(f"{mod}.{a.name}")
    return out


def reachable_files(entry):
    """Project-internal .py files reachable from the entry point through the import graph.

    This automatically leaves out backup copies like `server_backup_*.py`: they exist
    in the folder, but NOTHING imports them, so they are not part of the running server.
    """
    entry = os.path.abspath(entry)
    root = os.path.dirname(entry)
    seen, queue, trees = set(), [entry], {}
    while queue:
        path = queue.pop()
        if path in seen or not os.path.isfile(path):
            continue
        if any(part in _SKIP_DIRS for part in path.split(os.sep)):
            continue
        seen.add(path)
        try:
            src = open(path, encoding="utf-8").read()
            tree = ast.parse(src, filename=path)
        except (OSError, SyntaxError) as e:
            trees[path] = ("ERR", e, "")
            continue
        trees[path] = ("OK", tree, src)
        for mod in _imports_of(tree, root, path):
            tgt = _resolve(root, mod)
            if tgt and tgt not in seen:
                queue.append(tgt)
    return root, trees


# ---------------------------------------------------------------------------
def check(entry):
    """Inspect one MCP server (entry point + its imports)."""
    errs, warns = [], []
    if not os.path.isfile(entry):
        return [f"UNREADABLE: {entry}"], [], None

    root, trees = reachable_files(entry)

    tools = []          # (tool name, file, line, docstring)
    ndef = 0
    total_src = 0

    for path in sorted(trees):
        kind, payload, src = trees[path]
        rel = os.path.relpath(path, root)
        if kind == "ERR":
            e = payload
            if isinstance(e, SyntaxError):
                errs.append(f"SYNTAX ERROR {rel}:{e.lineno} -> {e.msg}")
            else:
                errs.append(f"UNREADABLE: {rel} ({e})")
            continue
        tree = payload
        total_src += len(src)

        # --- tools: at ANY depth (v2) ---
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                t = _tool_name(node)
                if t:
                    tools.append((t, rel, node.lineno, ast.get_docstring(node)))

        # --- duplicated module-level defs + calls that run at import time ---
        defs = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append((node.name, node.lineno))
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                fn = node.value.func
                nm = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if nm in ("open", "load", "read_text", "run", "system"):
                    warns.append(f"{rel}:L{node.lineno}: module-level {nm}() — runs at import time")
        ndef += len(defs)
        cdef = collections.Counter(n for n, _ in defs)
        for name, n in cdef.items():
            if n > 1:
                lines = ", ".join(str(l) for nm, l in defs if nm == name)
                warns.append(f"{rel}: duplicated module-level def: '{name}' x{n} (L{lines})")

    # --- duplicated tool name: ACROSS FILES (v2) ---
    cnt = collections.Counter(t for t, _, _, _ in tools)
    for name, n in cnt.items():
        if n > 1:
            hol = ", ".join(f"{f}:L{l}" for t, f, l, _ in tools if t == name)
            errs.append(f"DUPLICATE TOOL NAME: '{name}' x{n} ({hol}) — the later one SILENTLY overrides the earlier")

    for t, f, l, doc in tools:
        if not doc:
            warns.append(f"{f}:L{l}: '{t}' tool — NO docstring")

    meta = {
        "tools": sorted(t for t, _, _, _ in tools),
        "by_file": sorted((f, t) for t, f, _, _ in tools),
        "nfiles": len(trees),
        "ndef": ndef,
        "kb": total_src // 1024,
        "mtime": _mtime_max(trees),
    }
    return errs, warns, meta


def _mtime_max(trees):
    """mtime of the NEWEST reached file (not just the entry point)."""
    import datetime
    best = None
    for p in trees:
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if best is None or m > best:
            best = m
    if best is None:
        return "?"
    return datetime.datetime.fromtimestamp(best).strftime("%Y-%m-%d %H:%M")


def main(argv):
    show_names = "--names" in argv
    show_files = "--files" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        paths = [os.path.join(root, p) for p in DEFAULT_TARGETS]
        paths = [p for p in paths if os.path.exists(p)]

    print("# STAGED-patch health check (out-of-band, AST-based)  ·  v2")
    print("_The tool COUNT is what matters: compare it with your own live tool list._\n")

    rc = 0
    vak = []
    for p in paths:
        errs, warns, meta = check(p)
        label = os.path.basename(os.path.dirname(p)) or os.path.basename(p)
        if meta is None:
            print(f"## {label}\n  🔴 {errs[0]}\n")
            rc = 2
            continue
        fj = f"{meta['nfiles']} files" if meta["nfiles"] > 1 else "1 file"
        print(f"## {label}  ·  {len(meta['tools'])} tools  ·  {fj}  ·  {meta['ndef']} defs  ·  "
              f"{meta['kb']} KB  ·  newest {meta['mtime']}")
        if show_names and meta["tools"]:
            print("   tools: " + ", ".join(meta["tools"]))
        if show_files:
            for f, t in meta["by_file"]:
                print(f"      {f:32s} {t}")
        if errs:
            rc = 2
            print(f"   🔴 {len(errs)} ERROR(S) — a restart WOULD BREAK the server:")
            for x in errs:
                print("      -", x)
        if warns:
            rc = max(rc, 1)
            print(f"   🟡 {len(warns)} warning(s):")
            for x in warns:
                print("      -", x)
        # --- v2: the silent zero is now loud ---
        if not meta["tools"]:
            rc = max(rc, 1)
            vak.append(label)
            print("   🟡 BLIND SPOT: 0 tools found — the tool-count diff is NOT usable")
            print("      for this server. It does not mean there are no tools; it means")
            print("      this script can't see them (different registration pattern / external file).")
        elif not errs and not warns:
            print("   ✅ clean — safe to restart (at AST level)")
        print()

    print("---")
    zaro = {0: "✅ ALL CLEAN",
            1: "🟡 warning(s)",
            2: "🔴 ERROR — do NOT restart until you have fixed it"}[rc]
    if vak:
        zaro += f"  ·  ⚠️ BLIND SPOT: {', '.join(vak)} (0 tools — the diff is worthless there)"
    print(zaro)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
