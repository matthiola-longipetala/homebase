# Speaker identification — local, on the Pi

Adds local speaker identification to the robot's listen loop: every transcribed sentence gets
a label in front of it — `[Alice]:` / `[Bob]:` / `[valoszinuleg X]:` ("probably X") /
`[ismeretlen hang]:` ("unknown voice"). The label strings are Hungarian and are matched by
`../robot_listen_loop.py` and `../robot_olvaso.py`, so keep them as they are.

**Privacy:** the voiceprints (256-dim d-vectors) stay **local on the Pi** in `profiles.json`
and are never uploaded anywhere. `profiles.json` is created by `enroll.py` and is not part of
this repository — do not commit it.

## Model choice — why Resemblyzer
Candidates: **Resemblyzer** (GE2E d-vector, ~1.4M params) vs **SpeechBrain ECAPA-TDNN**.
Chosen: **Resemblyzer**, because:

1. **The task is easy.** Separating a few household voices plus an "unknown" class is trivial
   in d-vector space; ECAPA's better EER on hard, many-speaker, noisy benchmarks brings no
   practical gain here.
2. **Simpler API and footprint.** Enroll = mean of embeddings, identify = cosine similarity.
   No HuggingFace Hub download/cache on the Pi (ECAPA's model comes from there).
3. **Works on aarch64 / Python 3.13.** torch cp313 aarch64 wheels exist. The real risk is
   librosa -> numba -> llvmlite (LLVM build); piwheels' pre-built wheels plus a **dedicated
   venv** (`.spk-venv`) solve it without touching the system Python or the MCP server.

Fallbacks if Resemblyzer will not install: (a) a Python 3.12 venv (`install.sh` uses
`python3.12` first if present), or (b) ECAPA exported to ONNX (no torch).

## Files
- `speaker_id_lib.py` — shared core: encoder loading, `embed_wav`, enroll/identify, `profiles.json` I/O
- `enroll.py` — enrolment: `enroll.py Alice` (3 x 10 s recordings) or `--wav ...`
- `identify.py` — `identify.py /tmp/utt.wav [--json|--pipe]` for testing
- `speaker_daemon.py` — persistent server on `/tmp/speaker.sock`, used by the listen loop (avoids reloading torch per sentence)
- `restart_spk.sh` — (re)starts the daemon
- `install.sh` — dependencies into a dedicated venv (piwheels first, self-check import)
- `speaker_config.json` — device, sample rate, **thresholds** (tunable without code changes)
- `LISTEN_LOOP_PATCH.md` — how it is wired into `robot_listen_loop.py`
- `profiles.json` — created by enrolment; the local voiceprints (not shipped)

## First setup
```bash
bash ~/picarx-mcp/speaker_id/install.sh                       # ~10-20 min on the Pi
V=~/picarx-mcp/speaker_id/.spk-venv/bin/python
$V ~/picarx-mcp/speaker_id/enroll.py Alice                    # 3 samples
$V ~/picarx-mcp/speaker_id/enroll.py Bob
$V ~/picarx-mcp/speaker_id/identify.py /tmp/enroll_Alice_0.wav --pipe   # self-test: [Alice], high score
bash ~/picarx-mcp/speaker_id/restart_spk.sh                   # start the daemon
```
For `robot_olvaso.py` to recognise the enrolled names, list them in `ROBOT_SPEAKERS`
(e.g. `ROBOT_SPEAKERS=Alice,Bob`).

**The daemon is not a service:** start it with `restart_spk.sh` after every boot (and after
restarting the microphone). Without it the listen loop still works, just without labels.

## Confidence policy
- `cosine >= thresholds.name` -> `[Name]`
- `thresholds.maybe <= cosine < thresholds.name` -> `[valoszinuleg Name]`
- below `thresholds.maybe` -> `[ismeretlen hang]` (also a handy "guest present" signal)

Code defaults are 0.75 / 0.5; the shipped config uses stricter values (0.82 / 0.78). The
thresholds were tuned against unknown male and female voices (e.g. video audio played through
a speaker): keep a gap between the strangers' maximum score and the enrolled speaker's minimum
live score. If an enrolled speaker scores too low in a noisy or distant situation, add an
enrolment sample from that situation (`enroll.py <Name> --wav ...`, weighted average) rather
than lowering the thresholds. Expect slightly lower scores outdoors. Every identification is
logged to `/tmp/speaker_scores.jsonl`, and the last 20 WAVs are kept in `/tmp/spk_ring/` for
re-checking.

## Notes
- `setuptools<81` is required in the venv (webrtcvad imports `pkg_resources`); `install.sh`
  pins it.
- `rec_device` is addressed by ALSA card name, because the card number can change after a reboot.
- Restart the daemon only via `restart_spk.sh` over ssh: a `pkill -f` with the daemon's name
  in the ssh command line would also kill the calling shell.
