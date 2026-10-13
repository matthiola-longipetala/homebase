"""exportalo.py ("exportáló" = exporter) — builds a cleaned copy of the GitHub package, FAIL-CLOSED.

Usage:
  python exportalo.py --manifest manifest.json --forras <root> --cel <NEW, non-existent folder> [--szaraz]
                      [--tiltolista <path>] [--jelentes <path>] [--munkapeldany]
  English aliases: --source (= --forras), --target (= --cel), --dry-run (= --szaraz), --blocklist (= --tiltolista),
                   --report (= --jelentes), --working-copy (= --munkapeldany)

What it does:
  1. Collects the files listed by the manifest (include/exclude globs). A binary file can go out only with an
     allowed extension, and even then it is listed in the report (an image may carry EXIF data).
  2. Path replacements (the manifest's "csere" ("replace") list: regex -> placeholder, e.g. the user folder -> {HOME}).
  3. Cuts out personal COMMENTS: if a #/// comment contains a blocklisted word
     (e.g. "# 10-11 X asked for this: …"), the comment is dropped — it is not rewritten, because a rewrite always
     leaves a trace. The knowledge is re-described in general terms in the README. Docstrings and strings are NOT
     comments: a finding there blocks and must be fixed by hand.
  4. The filter (szuro.py, "szűrő" = filter) scans the result. If ANYTHING remains, no output is created (the
     temporary folder is deleted), the exit code is 2, and the report (masked, stored OUTSIDE the repo) says where.
  5. --szaraz (dry run): report only, never any output.
The output is always a fresh folder (never an existing git history); `git init` is a manual step AFTER review.
"""
import argparse, fnmatch, json, os, re, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import szuro  # noqa: E402

SZOVEG_KIT = {".py", ".ps1", ".md", ".json", ".txt", ".toml", ".yaml", ".yml", ".cfg", ".ini", ".sh", ".bat",
              ".js", ".html", ".css", ".example", ".minta", ".service", ".pyw", ".gitignore", ".scad", ""}
KOMMENT_JEL = {".py": "#", ".ps1": "#", ".sh": "#", ".toml": "#", ".yaml": "#", ".yml": "#", ".cfg": "#",
               ".ini": ";", ".js": "//", ".css": None, ".bat": "REM", ".service": "#", ".pyw": "#",
               ".gitignore": "#", ".scad": "//"}


def gyujt(forras, man):
    inc, exc = man.get("include", []), man.get("exclude", [])
    ki = []
    for gy, mappak, fajlok in os.walk(forras):
        rel_gy = os.path.relpath(gy, forras).replace("\\", "/")
        mappak[:] = [m for m in mappak if not any(fnmatch.fnmatch(f"{rel_gy}/{m}".lstrip("./"), e.rstrip("/"))
                                                   or fnmatch.fnmatch(m, e) for e in exc)]
        for f in fajlok:
            rel = f"{rel_gy}/{f}".lstrip("./") if rel_gy != "." else f
            if any(fnmatch.fnmatch(rel, i) for i in inc) and not any(fnmatch.fnmatch(rel, e) or
                                                                     fnmatch.fnmatch(f, e) for e in exc):
                ki.append(rel)
    return sorted(ki)


def _komment_vag(sor, jel, tilto):
    """If the line's comment contains a blocklisted word, the comment is dropped. None = drop the whole line."""
    if not jel:
        return sor
    if jel == "REM":
        return None if sor.lstrip().upper().startswith("REM") and szuro.tilto_talal(sor, tilto) else sor
    i = _komment_kezdet(sor, jel)
    if i < 0:
        return sor
    if not szuro.tilto_talal(sor[i:], tilto):
        return sor
    eleje = sor[:i].rstrip()
    return eleje if eleje else None


def _komment_kezdet(sor, jel):
    """First occurrence of the comment marker OUTSIDE a string (simple, single-line strings only)."""
    idez, k = None, 0
    while k < len(sor):
        c = sor[k]
        if idez:
            if c == "\\":
                k += 2; continue
            if c == idez:
                idez = None
        elif c in "\"'":
            idez = c
        elif sor.startswith(jel, k):
            return k
        k += 1
    return -1


def tisztit(szoveg, kit, man, tilto):
    csere_db = 0
    for cs in man.get("csere", []):
        szoveg, n = re.subn(cs["minta"], cs["uj"].replace("\\", "\\\\"), szoveg)
        csere_db += n
    jel = KOMMENT_JEL.get(kit)
    kivagott, ki = 0, []
    for sor in szoveg.splitlines(keepends=True):
        veg = "\n" if sor.endswith("\n") else ""
        uj = _komment_vag(sor.rstrip("\r\n"), jel, tilto)
        if uj is None:
            kivagott += 1
            continue
        if uj != sor.rstrip("\r\n"):
            kivagott += 1
        ki.append(uj + veg)
    return "".join(ki), kivagott, csere_db


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--forras", "--source", required=True)
    ap.add_argument("--cel", "--target", required=True)
    ap.add_argument("--tiltolista", "--blocklist")
    ap.add_argument("--jelentes", "--report")
    ap.add_argument("--szaraz", "--dry-run", action="store_true")
    ap.add_argument("--munkapeldany", "--working-copy", action="store_true",
                    help="for manual generalization: writes the output even with name findings, but NEVER with a secret")
    a = ap.parse_args(argv)
    man = json.load(open(a.manifest, encoding="utf-8"))
    tilto = szuro.tiltolista_betolt(a.tiltolista)
    if os.path.exists(a.cel):
        print(f"ERROR: the target already exists ({a.cel}) — I only write into a fresh folder", file=sys.stderr)
        return 1
    forras = os.path.abspath(a.forras)
    bin_ok = set(man.get("binaris_engedely", []))
    jel_ut = a.jelentes or os.path.join(os.path.dirname(szuro.ALAP_TILTOLISTA), "utolso_jelentes.txt")
    os.makedirs(os.path.dirname(jel_ut), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="gh_export_")
    leletek, statisztika = [], {"fajl": 0, "kivagott_komment": 0, "binaris": 0}
    try:
        for rel in gyujt(forras, man):
            kit = os.path.splitext(rel)[1].lower()
            cel_f = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(cel_f), exist_ok=True)
            statisztika["fajl"] += 1
            if kit not in SZOVEG_KIT:
                if kit in bin_ok:
                    shutil.copyfile(os.path.join(forras, rel), cel_f)
                    statisztika["binaris"] += 1
                    leletek.append((rel, 0, "binary (review by hand: EXIF/content)", "-"))
                else:
                    leletek.append((rel, 0, "binary not allowed", "-"))
                continue
            try:
                sz = open(os.path.join(forras, rel), encoding="utf-8-sig").read()
            except UnicodeDecodeError:
                leletek.append((rel, 0, "not UTF-8 text", "-"))
                continue
            sz, kv, cs_db = tisztit(sz, kit, man, tilto)
            statisztika["kivagott_komment"] += kv
            if cs_db and kit in KOMMENT_JEL:
                # a placeholder in CODE = the file can't run yet → not ready, blocks
                leletek.append((rel, 0, f"placeholder in code ({cs_db}×) — must be rewritten to use config/env", "-"))
            for n, fajta, m in szuro.vizsgal(sz, tilto) + szuro.vizsgal(rel, tilto):
                leletek.append((rel, n, fajta, m))
            open(cel_f, "w", encoding="utf-8", newline="").write(sz)
        blokkolo = [l for l in leletek if not l[2].startswith("binary (review by hand")]
        with open(jel_ut, "w", encoding="utf-8") as j:
            j.write(f"files: {statisztika['fajl']} · personal comments cut: {statisztika['kivagott_komment']}"
                    f" · binary: {statisztika['binaris']} · BLOCKING findings: {len(blokkolo)}\n")
            for rel, n, fajta, m in leletek:
                j.write(f"{rel}:{n}  {fajta}  {m}\n")
        print(f"{statisztika} · blocking: {len(blokkolo)} · report: {jel_ut}")
        if a.munkapeldany and not a.szaraz:
            # working copy: names may remain (they must be generalized by hand), but secrets NEVER
            titok_fajtak = {f for f, _ in szuro.TITOK}
            titkos = [l for l in leletek if l[2] in titok_fajtak or l[2] == "binary not allowed"]
            if titkos:
                print(f"STOP: {len(titkos)} secret/binary finding(s) — the working copy is not created either", file=sys.stderr)
                return 2
            shutil.move(tmp, a.cel)
            tmp = None
            print(f"WORKING COPY READY: {a.cel} — {len(blokkolo)} finding(s) await manual generalization (NOT publishable)")
            return 0
        if blokkolo or a.szaraz:
            return 2 if blokkolo else 0
        shutil.move(tmp, a.cel)
        tmp = None
        print(f"DONE: {a.cel} (git init and publishing are manual steps, after review)")
        return 0
    finally:
        if tmp and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
