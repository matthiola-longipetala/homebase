# -*- coding: utf-8 -*-
"""FAIL-CLOSED radar guard (get_open_markers / get_unlogged_changes).

What it proves: if a radar CANNOT run a check (unreadable file, an mtime that
cannot be stat-ed), it SAYS so -- and puts next to the green summary that the
summary does not apply to it. Principle: "a check that cannot run is an ERROR
with a named reason, never a silent pass".

★ WHY IT DOES NOT STUB: "where the test stubs, the test is blind". This test
injects a REAL fault into a real file of a real, temporary memory tree, and
runs the REAL `get_open_markers` / `get_unlogged_changes` with MEMORIA_ROOT
redirected. The only shim is the external `mcp.server.fastmcp` package -- that
is the framework, not the code under test.

Fault injection, per platform:
- POSIX: `chmod 000` on a marker file and on a log file (does not work as root).
- Windows: `chmod 000` has no effect there (it only sets the read-only flag), so
  the test falls back to a byte-range lock (`msvcrt.locking`), which makes a
  second handle's read fail with a real PermissionError.
- If neither takes effect, those cases are SKIPPED with a message.
- A broken symlink named `.md` (os.walk lists it, getmtime raises) exercises the
  stat branch of `_unlogged_collect`; skipped where symlinks cannot be created.
  (`chmod 000` would NOT hit that branch: stat only needs the DIRECTORY's rights.)
- Portable case (added for the public release): a file that is not valid UTF-8.

★ NEGATIVE CONTROL: `--mutans <old_server.py>` runs the same checks against a
pre-patch server, where they MUST fail. A guard that is green on the buggy
version is no guard. The pre-patch file is not shipped; without it the
control is skipped.

Run:
    python -X utf8 test_radar_fail_closed.py
    python -X utf8 test_radar_fail_closed.py --mutans <file.py>
"""
import os
import stat
import sys
import time
from pathlib import Path

import _support as S

MUTANS = None
MUTANS_REQUESTED = "--mutans" in sys.argv
if MUTANS_REQUESTED:
    i = sys.argv.index("--mutans")
    cand = Path(sys.argv[i + 1]).resolve() if len(sys.argv) > i + 1 else None
    MUTANS = cand if (cand and cand.exists()) else None

C = S.Checker("FAIL-CLOSED radar guard", negative=MUTANS is not None)
if MUTANS_REQUESTED and MUTANS is None:
    C.skip_no_backup("negative control (--mutans)")


def build_tree(root: Path):
    """A minimal but REAL memory tree: the radars really walk it."""
    for sub in ("kontextus", "projektek", "eszkozok", "claude_sajat", "naplo"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    S.write(root, "_state.md", "# Current state\n\n## Where I am\nNothing unusual.\n")
    S.write(root, "kontextus/helyzet.md", "# Situation\n\n## URGENT\nNothing.\n")
    S.write(root, "kontextus/mintazatok.md", "# Patterns\nNothing.\n")
    # READABLE file with ONE real marker -- it must ALWAYS show
    S.write(root, "projektek/readable.md",
            "# Readable project\n\n- ⚑ TODO: this marker shows, because the file is readable.\n")
    # The fault-injection target: contains a marker, but will become unreadable
    bad = S.write(root, "projektek/unreadable.md",
                  "# Hidden project\n\n- ⚑ TODO: THE RADAR WILL NOT SEE THIS MARKER.\n")
    # log: a substantive entry for TODAY, so the coverage is green by default
    today = time.strftime("%Y-%m-%d")
    S.write(root, f"naplo/{today}_agencia.md",
            f"---\ndate: {today}\ntype: agencia\n---\n\n# Autonomous session\n"
            "Files touched: readable.md, unreadable.md, broken.md\n")
    # 2nd target: an unreadable LOG file. One more entry for the same day, so the
    # day's coverage stays -- that keeps the GREEN line measurable.
    bad_log = S.write(root, f"naplo/{today}_interactive.md",
                      f"---\ndate: {today}\ntype: interactive\n---\n\n# Conversation\nNothing.\n")
    return bad, bad_log


def broken_link(root: Path):
    """A BROKEN SYMLINK named `.md`: os.walk lists it, getmtime raises FileNotFoundError."""
    t = root / "projektek" / "broken.md"
    try:
        os.symlink(root / "projektek" / "never_exists.md", t)
        return t
    except (OSError, NotImplementedError):
        return None


def is_unreadable(p: Path) -> bool:
    try:
        p.read_text(encoding="utf-8")
        return False
    except OSError:
        return True


_LOCKS = []


def make_unreadable(paths):
    """Inject a real read fault. Returns the method name, or None if nothing worked."""
    for p in paths:
        os.chmod(p, 0)
    if all(is_unreadable(p) for p in paths):
        return "chmod 000"
    for p in paths:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    if os.name == "nt":
        try:
            import msvcrt
            for p in paths:
                fh = open(p, "r+b")
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, max(1, os.path.getsize(p)))
                _LOCKS.append((fh, max(1, os.path.getsize(p))))
            if all(is_unreadable(p) for p in paths):
                return "msvcrt byte-range lock"
        except OSError:
            pass
        release_locks()
    return None


def release_locks():
    if os.name == "nt":
        import msvcrt
        while _LOCKS:
            fh, n = _LOCKS.pop()
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, n)
            except OSError:
                pass
            fh.close()


def green_line(text):
    for line in text.splitlines():
        if "0 SILENT DAYS" in line or "Every change has log coverage." in line:
            return line
    return ""


GREEN_ANCHORS = ("✅ **0 SILENT DAYS** on the closed days.", "✅ Every change has log coverage.")

root = S.make_memory_root("failclosed_", subdirs=())
bad, bad_log = build_tree(root)
try:
    srv = S.load_server(root, server_path=MUTANS)
except Exception as e:  # noqa: BLE001
    print(f"!! the server could not be loaded: {type(e).__name__}: {e}")
    sys.exit(2)
print(f"source: {(MUTANS or S.SERVER_PATH).name}")

# ------------------------------------------------------- 1. BASELINE: no fault
C.section("1. Baseline, no fault injected")
mk0 = srv.get_open_markers()
ul0 = srv.get_unlogged_changes(days=7)
C.check("markers: the readable marker shows", "readable.md" in mk0, mk0[:300])
C.check("markers: NO fail-closed block when nothing failed", "FAIL-CLOSED" not in mk0)
C.check("unlogged: NO fail-closed block when nothing failed", "FAIL-CLOSED" not in ul0)
C.check("unlogged: the existing GREEN anchor sentence is unchanged",
        any(a in ul0 for a in GREEN_ANCHORS), ul0[:400])

# ------------------------------------------------------- 2. REAL FAULT INJECTION
C.section("2. Real fault injection: unreadable marker file + unreadable log file")
method = make_unreadable([bad, bad_log])
link = broken_link(root)
if link is None:
    C.skip("broken-symlink stat fault", "symlinks cannot be created here (permission/OS)")
try:
    if method is None and link is None:
        C.skip("all fault-injection cases",
               "neither chmod 000 nor a file lock took effect (running as root?), and no symlink")
    else:
        mk = srv.get_open_markers()
        ul = srv.get_unlogged_changes(days=7)
        tail_mk = mk.split("FAIL-CLOSED")[-1] if "FAIL-CLOSED" in mk else ""
        tail_ul = ul.split("FAIL-CLOSED")[-1] if "FAIL-CLOSED" in ul else ""
        if method is None:
            C.skip("unreadable-file cases",
                   f"fault injection cannot take effect on this platform ({sys.platform}): "
                   "chmod 000 did not make the file unreadable, and no file lock was possible")
        else:
            print(f"  (fault injected with: {method})")
            C.check("markers: the fail-closed block APPEARS", "⛔ FAIL-CLOSED" in mk, mk[-800:])
            C.check("markers: the block NAMES the file", "unreadable.md" in tail_mk, tail_mk)
            C.check("markers: the CAUSE is there too (PermissionError)", "PermissionError" in mk)
            C.check("markers: the readable file's marker STILL shows "
                    "(the fault does not take the whole sweep down)", "readable.md" in mk)
            C.check("unlogged: the fail-closed block APPEARS", "⛔ FAIL-CLOSED" in ul, ul[-1200:])
            C.check("unlogged: the UNREADABLE LOG is named "
                    "(otherwise its day would look FALSELY uncovered)",
                    "_interactive.md" in tail_ul, ul[-1200:])
            C.check("unlogged: the CAUSE is there too (PermissionError)", "PermissionError" in ul)
        if link is not None:
            C.check("unlogged: the BROKEN SYMLINK's stat error is reported too "
                    "(this is the `_unlogged_collect` branch)", "broken.md" in tail_ul, ul[-1200:])
        # THE POINT: the green summary may NOT stand without qualification
        gl = green_line(ul)
        C.check("unlogged: there is a green summary line", bool(gl), ul[:600])
        C.check("unlogged: NEXT TO the green line stands the fail-closed warning "
                "(the tick may not stand barefoot)", "could not run" in gl, f"the line: {gl!r}")
        C.check("unlogged: the test-anchor substring is preserved VERBATIM",
                any(a in ul for a in GREEN_ANCHORS), gl)
finally:
    release_locks()
    for p in (bad, bad_log):
        try:
            os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

# ------------------------------------------------------- 3. PORTABLE: invalid UTF-8
C.section("3. Portable fault: a file that is not valid UTF-8 (runs on every platform)")
# A memory file with a broken encoding is a realistic fault (a copied-in
# Windows-1250 note, a truncated write). search_memory already reports it as
# FAIL-CLOSED, and so must the two radars below: an unreadable encoding must name
# the file, not abort the whole tool call (they once caught only OSError).
root2 = S.make_memory_root("failclosed_utf8_", subdirs=())
build_tree(root2)
(root2 / "projektek" / "latin2.md").write_bytes(b"# Notes\n\n- TODO: \xe1rv\xedzt\xfbr\xf5 \xff marker\n")
(root2 / "naplo" / (time.strftime("%Y-%m-%d") + "_latin2.md")).write_bytes(b"# Log\n\xff\xfe broken\n")
srv2 = S.load_server(root2, server_path=MUTANS)
for label, call in (("get_open_markers", lambda: srv2.get_open_markers()),
                    ("get_unlogged_changes", lambda: srv2.get_unlogged_changes(days=7))):
    try:
        out = call()
        ok, detail = ("FAIL-CLOSED" in out and "latin2.md" in out.split("FAIL-CLOSED")[-1]), out[-600:]
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"raised {type(e).__name__}: {e}"
    C.check(f"{label}: a non-UTF-8 file is reported as FAIL-CLOSED (the call does not crash)", ok, detail)

if MUTANS:
    print(f"(negative control: {MUTANS.name} -- it MUST fail here)")
C.finish()
