# -*- coding: utf-8 -*-
"""robot_mond.py — speak a long text on the robot in CHUNKS (lesson learned:
robot_say cuts the audio above ~50 s, and the listener dies during it too).
Connection settings: see laptop_config.py (PI_HOST / PI_KEY or config.json).
Usage:  python robot_mond.py <textfile.txt> [gain_dB=8] [max_char=260]
It splits the text at sentence boundaries into ~max_char pieces, uploads them with scp, and
speaks them in order with a 1.5 s pause. The pieces go into the robot's "mese" folder.
"""
import sys, re, io, os, subprocess, tempfile
import laptop_config
K = laptop_config.ssh_key(); PI = laptop_config.pi_target()
PROJ = laptop_config.pi_project_dir()                  # project folder on the robot (PI_PROJECT_DIR)
PROJ_SH = PROJ if PROJ.startswith("/") else "~/" + PROJ  # shell form for cd
REM = laptop_config.pi_remote_path("mese") + "/"       # scp target (relative = robot user's home)
src = sys.argv[1]; gain = sys.argv[2] if len(sys.argv) > 2 else "8"; mx = int(sys.argv[3]) if len(sys.argv) > 3 else 260
s = io.open(src, encoding="utf-8").read().strip()
sent = re.split(r"(?<=[.!?…])\s+", s); chunks = []; cur = ""
for x in sent:
    if len(cur) + len(x) > mx and cur: chunks.append(cur); cur = x
    else: cur = (cur + " " + x).strip()
if cur: chunks.append(cur)
tag = re.sub(r"\W", "_", os.path.splitext(os.path.basename(src))[0])[:20]
tmp = tempfile.mkdtemp(); files = []
for i, c in enumerate(chunks, 1):
    p = os.path.join(tmp, f"{tag}_{i:02d}.txt"); io.open(p, "w", encoding="utf-8").write(c); files.append(p)
subprocess.run(["scp", "-i", K, "-q", *files, PI + ":" + REM], check=True)
names = " ".join(f"mese/{os.path.basename(f)}" for f in files)
whole = "SAY_WHOLE=1 " if os.environ.get("SAY_WHOLE") == "1" else ""
cmd = f'cd {PROJ_SH} && for f in {names}; do SAY_GAIN_DB={gain} {whole}timeout 70 python3 robot_say.py --file "$f" 2>&1 | tail -1; sleep 1.5; done'
r = subprocess.run(["ssh", "-i", K, "-o", "BatchMode=yes", PI, cmd], capture_output=True, text=True, encoding="utf-8", errors="ignore")
print(f"{len(chunks)} pieces, lengths: {[len(c) for c in chunks]}"); print(r.stdout.strip());
if r.returncode: print("ERROR:", r.stderr.strip()[-300:])
