# -*- coding: utf-8 -*-
"""server.py NAME-INTEGRITY guard -- against the blind spot of "extracting" tests.

★ WHY IT EXISTS. A patch once passed every test suite (dozens of cases, with
mutation probes, `ast.parse` OK) -- and the first live call after the restart
died with:

    Error executing tool get_unlogged_changes: name '_unlogged_collect' is not defined

What had happened: the patch replaced a function by cutting the source from
the function's start up to the NEXT `@mcp.tool()` anchor. Between those two
points stood THREE other helper functions (`_unlogged_extra_roots`,
`_unlogged_collect`, `_unlogged_naplo_index`), and all three were lost.

**The point: the tests could not catch this STRUCTURALLY.** The suites that
covered that tool extracted the function under test and STUBBED exactly those
helpers -- the test itself supplied what the patch had deleted; the stub WAS
the thing hiding the bug. `ast.parse` did not catch it either: a missing
GLOBAL name is syntactically perfectly valid and only explodes at run time.
The checker reported its own blindness with a ✅.

WHAT IT DOES: walks every function of the file with a real SCOPE CHAIN
(module -> outer function -> nested function/lambda/comprehension) and reports
the global name reads that cannot be resolved anywhere. This is the gap
between a syntax check and a unit test -- cheap, complete, and it **cannot be
stubbed away**. A built-in negative control re-creates the original incident
(the three helpers deleted from the source) and requires the guard to catch it.

Run:  python -X utf8 test_server_name_integrity.py
      python -X utf8 test_server_name_integrity.py <other_file.py>
"""
import ast
import builtins
import sys
from pathlib import Path

import _support as S

TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else S.SERVER_PATH
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def bind(target, into):
    """The names introduced by an assignment target."""
    if isinstance(target, ast.Name):
        into.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for el in target.elts:
            bind(el, into)
    elif isinstance(target, ast.Starred):
        bind(target.value, into)


def own_nodes(roots):
    """Walk the subtree, but never step into the bodies of NESTED scopes.

    ⚠️ It looks like this after TWO false-positive fixes:
    (1) with a plain `ast.walk`, the PARAMETERS of nested `def`s looked like
        "undefined globals" while analysing the outer function;
    (2) the first fix only filtered the CHILDREN, not the ROOTS -- so starting
        from the module body, the bodies of the top-level `def`s were analysed
        in the MODULE scope, producing dozens of false positives (every tool
        parameter). A scope must be a LEAF even when it is the starting point.
    A guard that makes noise is as useless as one that stays silent -- only
    more expensive, because everyone learns to ignore it.
    """
    stack = list(roots)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, SCOPES):
            continue                      # its body is another scope -- NEVER step in
        for child in ast.iter_child_nodes(node):
            stack.append(child)


def child_scopes(roots):
    """The directly nested scopes (function, lambda, class)."""
    found, stack = [], list(roots)
    while stack:
        node = stack.pop()
        if isinstance(node, SCOPES):
            found.append(node)
            continue
        for child in ast.iter_child_nodes(node):
            stack.append(child)
    return found


def params_of(args):
    out = set()
    for a in (args.posonlyargs + args.args + args.kwonlyargs):
        out.add(a.arg)
    for a in (args.vararg, args.kwarg):
        if a:
            out.add(a.arg)
    return out


def bindings_in(roots):
    """Names introduced in the scope's OWN body (without stepping into nested bodies)."""
    into = set()
    for node in own_nodes(roots):
        if isinstance(node, SCOPES) and not isinstance(node, ast.Lambda):
            into.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                bind(t, into)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            bind(node.target, into)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind(node.target, into)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    bind(item.optional_vars, into)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            into.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                into.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            into.update(node.names)
        elif isinstance(node, ast.NamedExpr):
            bind(node.target, into)
        elif isinstance(node, COMPS):
            for gen in node.generators:      # the comprehension variable lives in its own
                bind(gen.target, into)       # scope, but is visible here too: lenient
    return into


def analyze_tree(tree):
    """Return (sorted unique problems [(lineno, scope_label, name)], scope_count)."""
    problems = []
    count = [0]

    def analyze(roots, visible, label):
        count[0] += 1
        for node in own_nodes(roots):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in visible:
                    problems.append((node.lineno, label, node.id))
        for sc in child_scopes(roots):
            if isinstance(sc, ast.ClassDef):
                inner = visible | bindings_in(sc.body)
                analyze(sc.body, inner, f"{label}.{sc.name}")
            elif isinstance(sc, ast.Lambda):
                inner = visible | params_of(sc.args)
                analyze([sc.body], inner, f"{label}.<lambda>")
            else:
                inner = visible | params_of(sc.args) | bindings_in(sc.body)
                analyze(sc.body, inner, f"{label}.{sc.name}" if label else sc.name)

    module_visible = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__spec__"}
    module_visible |= bindings_in(tree.body)
    analyze(tree.body, module_visible, "")
    seen, uniq = set(), []
    for lineno, label, name in problems:
        if (label, name) not in seen:
            seen.add((label, name))
            uniq.append((lineno, label, name))
    return sorted(uniq), count[0]


C = S.Checker(f"NAME INTEGRITY -- {TARGET.name}")
if not TARGET.exists():
    print(f"!! not found: {TARGET}")
    sys.exit(2)
src = TARGET.read_text(encoding="utf-8")
try:
    tree = ast.parse(src)
except SyntaxError as e:
    C.check(f"syntax OK ({TARGET.name}:{e.lineno} -- {e.msg})", False)
    C.finish()

C.section("1. Every global name read resolves")
uniq, scopes = analyze_tree(tree)
print(f"  scopes analysed: {scopes}")
C.check("no unresolvable name reference (no run-time NameError candidate)", not uniq,
        "\n".join(f"{TARGET.name}:{ln}  {lbl or '<module>'}  ->  '{nm}'  NOT DEFINED" for ln, lbl, nm in uniq))
if uniq:
    print("  ➜ Typical cause: a patch cut too wide a range (up to the next anchor)")
    print("    and took an intermediate function with it.")

C.section("2. Negative control: the guard catches the original incident")
LOST = {"_unlogged_extra_roots", "_unlogged_collect", "_unlogged_naplo_index"}
present = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
if not LOST & present:
    C.skip("re-created incident", f"{TARGET.name} has none of {sorted(LOST)}")
else:
    mutant = ast.parse(src)
    mutant.body = [n for n in mutant.body
                   if not (isinstance(n, ast.FunctionDef) and n.name in LOST)]
    found, _ = analyze_tree(mutant)
    names = {nm for _ln, _lbl, nm in found}
    # Only the deleted helpers that the REMAINING code still calls can be missing
    # (`_unlogged_extra_roots` was only called by the deleted `_unlogged_collect`).
    still_used = {n.id for n in ast.walk(mutant)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)} & LOST
    C.check("the incident is reproduced: the remaining code still calls deleted helpers",
            bool(still_used), sorted(still_used))
    C.check("with the helpers deleted, the guard reports every one that is still called",
            still_used <= names, f"reported={sorted(names)} expected={sorted(still_used)}")
    C.check("...and nothing else (no noise from the mutation)", names <= LOST, sorted(names - LOST))
    try:
        compile(mutant, "<mutant>", "exec")
        compiles = True
    except SyntaxError:
        compiles = False
    C.check("...and the mutant still COMPILES (a syntax check alone would have passed it)", compiles)

C.finish()
