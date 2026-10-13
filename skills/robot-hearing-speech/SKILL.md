---
name: robot-hearing-speech
description: The PiCar-X robot's ears and voice — hearing (ReSpeaker + Scribe STT), continuous listening, speech (ElevenLabs voice), speaking long texts, sound direction (DOA), speaker identification, YAMNet soundscape. Load it when the robot must hear or speak, or a live spoken conversation starts. Triggers: "listen", "hear this", "pay attention", "speak", "say it", "who is talking", "sound direction", "what do you hear", "let's talk through the robot".
---

# Robot — HEARING AND SPEECH

**Canonical knowledge: `picarx-mcp/technika/eszkoztar.md` — "Ear / voice", "Speaker identification",
"Soundscape", the "robot_say …" sections; `tanulsagok/erzekeles.md` (noise gate, double buffering, USB).
This skill is navigation only.** Thresholds, tuning numbers and score tables belong THERE.

## Start BY HAND after every boot (like the MCP server)
1. `python3 respeaker_reboot.py` — **after every Pi boot** (XMOS wedge).
2. Speaker daemon (for labels), with nohup, from its own venv:
   `nohup ~/picarx-mcp/speaker_id/.spk-venv/bin/python ~/picarx-mcp/speaker_id/speaker_daemon.py > /tmp/speaker_daemon.log 2>&1 &`
   Without it: no speaker label, everything else works. **Part of the boot protocol** — check it yourself.
3. Test recording: `arecord -D plughw:<card>,0 -d 1 /tmp/t.wav` — healthy is ~32 kB.

## Hearing
| Command | For |
|---|---|
| `python3 robot_hear.py [s]` | one-shot hearing → Scribe STT |
| `python3 robot_listen_loop.py 2>/tmp/listen.err` | continuous listener (started under Monitor) |

**Continuous listening (Monitor = the agent lives on the robot):**
```
ssh -i {HOME}/.ssh/<robot-key> -o ServerAliveInterval=15 -l <pi-user> <Pi-IP> \
  "cd ~/picarx-mcp && PYTHONUTF8=1 python3 -u robot_listen_loop.py 2>/tmp/listen.err"
```
- `2>/tmp/listen.err` → only recognised sentences arrive as events (debug goes to the file).
- **Half-duplex:** while speaking, `/tmp/robot_speaking.lock` exists → the loop does not hear itself.
- **Stop BY HAND** (neither TaskStop nor `ssh -tt` reliably carries SIGHUP):
  `kill -TERM $(pgrep -f '[r]obot_listen_loop')`, then check that
  `/proc/asound/card<N>/pcm0c/sub0/status` says `closed`.
- **Never put `pkill -f robot_listen_loop` inside the Monitor command itself** — it kills itself, exit 255.
  Same pgrep self-match trap when stopping `speaker_daemon`: kill it in a SEPARATE ssh call.
- **From the laptop, as a one-shot waiter:** `PYTHONUTF8=1 python -u {REPO}/karbantartas/robot_fulelo_varo.py`
  ("listener-waiter") in background Bash — exits at the first speech line, and the NEXT start resumes where it
  stopped (`karbantartas/_robot_fulelo_offset.txt`), so no sentence is lost between re-arms.
- **RAW/SMART switch:** `touch /tmp/listen_raw` → everything raw (outdoors, guarding); `rm` → smart mode
  (noise gate + label policy; indoor conversation).
- **Off switches:** `/tmp/speaker_off` (speaker label off), `/tmp/hangkep_off` (soundscape off).

## Speech
| Command | For |
|---|---|
| `python3 robot_say.py --file <path>` | **ALWAYS from a UTF-8 file** — accents get mangled in argv |
| `SAY_GAIN_DB=4 python3 robot_say.py --file <path>` | quieter (evening, outdoors) |
| `python robot_mond.py <textfile> [gain] [max_char]` | **runs on the LAPTOP** (`picarx-mcp/`, "say it"): splits long text at sentence boundaries, copies it over, speaks it in order |

- **Over ~300 characters ALWAYS use `robot_mond.py`** — `robot_say` cuts long text.
- **IN SEQUENCE, NEVER IN PARALLEL.** Of two `robot_say` calls started close together the second dies silently
  (`audio open error: Device or resource busy`), **yet the script still prints OK** — its OK is NOT proof of
  playback. `audio open error` in the output = the message was NOT heard → repeat it.
- **Emotion tags** (eleven_v3): `[whispers]`, `[laughs]`… steer the mood, are not spoken. Sparingly.
- **Politeness (default):** `robot_say.py` first waits for silence (the listener writes `/tmp/last_voice_ts`);
  `SAY_NO_WAIT=1` = deliberate interruption. With ≥2 sentences it speaks sentence by sentence and listens in
  between; if someone starts talking it stops with **rc=3** and prints what was skipped → STOP, answer what they
  said. `SAY_WHOLE=1` = in one go. While you speak you are deaf (lock); you only hear in the gaps.

## Sound direction (DOA) and speaker
| Command | For |
|---|---|
| `python3 doa.py once\|loop\|raw\|calib` | sound direction |
| `python3 face_voice.py [tol] [max]` | turn towards the voice |
| `$VENV/bin/python speaker_id/enroll.py <name> [...]` | extend a voice profile |

- **The ear aims ROUGHLY, the camera refines.** Next to hard surfaces DOA measures the reflection.
- Voice prints stay **LOCAL on the Pi, NEVER in the cloud** — a privacy commitment to the household.
- Mislabelled speaker: **FIRST look at the score log** (`/tmp/speaker_scores.jsonl` + `/tmp/spk_ring/`, the last
  20 wavs) — `/tmp/utt.wav` is overwritten by the next sentence.
- Poor recognition in a noisy/distant situation: **do NOT lower the threshold — take a SAMPLE from that situation**
  (`enroll`, weighted mean). Do not pile up many damped samples.
- `[ismeretlen hang]` ("unknown voice") = a guest signal, not an error.

## Soundscape (YAMNet) — what you hear that is not speech
- **ON** by default. State: `/tmp/hangkep.json` (`now`, `background`, `recent`) — ask for it during active work.
- A new distinct sound → chat event `[hang: <label> <score>]` ("sound"); continuous sound settles into background.
- Tuning: `hangkep_config.json` (your own, reloaded live). **Never write into `voice_config.json`** — it holds a secret.

## Traps
- **During the speech lock an empty capture queue is NOT silence** — an ear watchdog may trigger a needless XMOS
  reboot that kills the listener and cuts playback. Another reason to use `robot_mond.py` for long text.
- **ALSA card numbers WANDER** → address by NAME (`sndrpihifiberry` / `Array`); "works but no sound" → `aplay -l`.
- **Outdoors:** higher VAD threshold + raw mode — **restore BOTH at home** (eszkoztar "FIELD RECIPE").
- **ReSpeaker gone from `lsusb`?** `lsusb | grep 2886` → `dmesg` plug event → if neither, **hub unbind/bind**
  (not cables) → `respeaker_reboot.py` → test recording. Recipe: eszkoztar "ReSpeaker USB DISAPPEARANCE".
- **Voice-led driving is the normal mode** — the human may not see the screen; SAY everything important.

## Where to write new knowledge
Ear/voice fact, threshold, recipe → `technika/eszkoztar.md` · perception lesson (noise gate, buffering) →
`tanulsagok/erzekeles.md`. Then **scp it to the Pi**.
