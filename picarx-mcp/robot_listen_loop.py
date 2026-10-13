#!/usr/bin/env python3
"""robot_listen_loop.py — CONTINUOUS listening: energy VAD -> ElevenLabs Scribe -> every
recognised sentence is one STDOUT line. Run under Monitor, every line becomes an EVENT in the
agent's chat (= someone talks to the robot, the agent wakes up and answers).

- STDOUT: ONLY the recognised sentence (clean line) -> this becomes the event.
- STDERR: all status/debug output (Monitor does not turn it into events).
- While /tmp/robot_speaking.lock exists it does NOT listen (half-duplex; robot_say.py creates it
  while speaking), and an empty queue under the lock does NOT count as data silence (no XMOS
  reboot during speech).
"""
import sys, os, json, subprocess, re, io, wave, ctypes, signal, select, time
import threading, queue as _queue
import numpy as np

# STT mishearing annotator (garble_fix). If missing/broken, the raw line goes out unchanged
# — listening must NEVER crash because of it.
try:
    from garble_fix import annotate as _garble_annotate
except Exception as _e:
    print(f"garble_fix cannot be loaded ({_e}) -> annotation disabled", file=sys.stderr, flush=True)
    def _garble_annotate(line):
        return line


def _pdeathsig():
    """The child (arecord) gets SIGTERM if the parent (this python) dies for any reason.
    So when Monitor/ssh stops, no orphaned arecord is left behind to wedge the ReSpeaker
    USB capture endpoint (an orphaned arecord sticks the capture in I/O errors, which
    only an XMOS REBOOT control command fixed)."""
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG=1
    except Exception:
        pass

CFG = os.path.expanduser("~/picarx-mcp/voice_config.json")
REC_DEV = "plughw:CARD=Array,DEV=0"   # ReSpeaker BY NAME (the card number shifts after a reboot!)
LOCK = "/tmp/robot_speaking.lock"
VOICE_TS = "/tmp/last_voice_ts"     # the mouth (robot_say) learns from this whether someone is talking (politeness + stop between sentences)
_last_touch = 0.0
def _touch_voice():
    global _last_touch
    now = time.time()
    if now - _last_touch >= 0.2:
        _last_touch = now
        try:
            open(VOICE_TS, "w").write(f"{now:.3f}")
        except OSError:
            pass
STALL_SEC = 5.0     # EAR WATCHDOG: this much data SILENCE = dead capture (a silent room STILL yields samples!)
MAX_REVIVE = 5      # exit after this many consecutive failed revives (Monitor reports it)
SR = 16000
FRAME = 480                      # 30 ms @ 16k
SILENCE_SEC = 1.5
MIN_SPEECH_SEC = 0.4
SPEECH_START_FRAMES = 3

cfg = json.load(open(CFG))
KEY = cfg["elevenlabs_api_key"]
STT = cfg.get("stt", {})
MODEL = STT.get("model_id", "scribe_v2")
LANG = STT.get("language_code", "en")
VOL_THRESH = cfg.get("vad", {}).get("volume_threshold", 500)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def transcribe(frames):
    audio = np.concatenate(frames).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
        wf.writeframes(audio.tobytes())
    open("/tmp/utt.wav", "wb").write(buf.getvalue())
    cc = f'header = "xi-api-key: {KEY}"\n'
    # ONE retry on timeout (curl rc 28) or a non-JSON response
    t = None
    for kiserlet in (1, 2):
        p = subprocess.run(
            ["curl", "-s", "--connect-timeout", "5", "--max-time", "45", "-X", "POST", "https://api.elevenlabs.io/v1/speech-to-text",
             "-K", "-", "-F", f"model_id={MODEL}", "-F", f"language_code={LANG}",
             "-F", "file=@/tmp/utt.wav;type=audio/wav"],
            input=cc, capture_output=True, text=True)
        try:
            t = (json.loads(p.stdout).get("text") or "").strip()
            break
        except Exception:
            log(f"scribe non-JSON (attempt {kiserlet}):", p.stdout[:160], "| curl rc", p.returncode, "|", p.stderr.strip()[-120:])
    if t is None:
        log("scribe: no response after two attempts, dropped"); return ""
    # RAW TEST MODE: if /tmp/listen_raw exists, NOTHING is filtered (for inspecting labels).
    if os.path.exists("/tmp/listen_raw"):
        return t.strip()
    # - if the tag stands next to SPEECH, it stays (context: "said it laughing");
    # - on its own, only NOTABLE sounds wake the agent (laughter, knocking, bell,
    #   cat/dog, scream) — continuous music/chirping/clicking is not an alarm.
    # These are Hungarian keywords matched against Scribe's Hungarian sound tags
    # (e.g. 'csörg' = rattle: Scribe v2 hears a cat's purring as '[csörgés]', and it also
    # catches a phone ringing).
    NOTABLE = r"nevet|kacag|sohaj|sóhaj|taps|kopog|cseng|csörg|csorg|nyávog|nyavog|dorombol|ugat|sikolt|sikoly|kiált|kialt|sír|siras|sírás"
    has_speech = bool(re.sub(r"\([^)]*\)|\[[^\]]*\]", "", t).strip())
    def _tag(m):
        s = m.group(0)
        if has_speech:
            return s
        if os.path.exists("/tmp/no_labels"):
            return ""   # speech-only mode: a standalone tag does not wake
        return s if re.search(NOTABLE, s, re.I) else ""
    return re.sub(r"\([^)]*\)|\[[^\]]*\]", _tag, t).strip()


SPEECH_BAND = (300.0, 3400.0)   # Hz — the core band of human speech
SPEECH_BAND_MIN_RATIO = 0.35    # band ratio below this = not speech-like -> not sent to STT (on a beach: relax to 0.10!)

def speech_band_ratio(frames):
    """Noise pre-filter: what fraction of the segment's energy falls into the speech band.
    Clanging/thumping/hissing has a low ratio -> not even sent to STT (saves API calls +
    fewer false events). Rumble below 80 Hz (air movement, motor hum) counts in neither
    the numerator nor the denominator."""
    audio = np.concatenate(frames).astype(float)
    if len(audio) < SR // 2:
        return 1.0  # too short for a reliable spectrum -> let it through
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1.0 / SR)
    total = spec[freqs >= 80.0].sum()
    if total <= 0:
        return 0.0
    band = spec[(freqs >= SPEECH_BAND[0]) & (freqs <= SPEECH_BAND[1])].sum()
    return float(band / total)


# ---- SOUNDSCAPE (YAMNet ambient sound labelling) — ADDITIVE, NEVER breaks the ear ----
# Called from the noise gate's drop branch: the already recorded buffer (frames) goes to YAMNet.
# It does NOT open a new microphone (respects half-duplex), it works from the existing recording.
# YAMNet runs in a separate python 3.12 venv (there is no litert wheel for the system 3.13) ->
# we call it as a subprocess (model load ~34 ms, inference ~8 ms/window, cheap).
HANGKEP_VENV_PY = os.path.expanduser("~/picarx-mcp/.yamnet-venv/bin/python")
HANGKEP_SCRIPT = os.path.expanduser("~/picarx-mcp/hangcimke.py")
HANGKEP_WAV = "/tmp/hangkep_in.wav"
HANGKEP_OFF = "/tmp/hangkep_off"   # if it exists -> sound labelling disabled

# ---- SPEAKER IDENTIFICATION — ADDITIVE, NEVER breaks the ear ----
# speaker_daemon.py runs in .spk-venv (venv isolation) and answers on a UNIX socket.
# If the daemon is not running or /tmp/speaker_off exists -> no label, everything continues.
SPEAKER_OFF = "/tmp/speaker_off"       # if it exists -> no speaker label
SPEAKER_SOCK = "/tmp/speaker.sock"     # speaker_daemon.py's socket

def speaker_label(wav_path):
    """Speaker label for /tmp/utt.wav from the daemon. Any error/timeout -> "" (no label).
    Listening must NEVER crash because of it (same as hangkep/garble)."""
    if os.path.exists(SPEAKER_OFF) or not os.path.exists(SPEAKER_SOCK):
        return ""
    try:
        import socket as _s
        c = _s.socket(_s.AF_UNIX, _s.SOCK_STREAM); c.settimeout(6.0)
        c.connect(SPEAKER_SOCK)
        c.sendall(wav_path.encode("utf-8"))
        resp = c.recv(256).decode("utf-8", "replace").strip(); c.close()
        # format: "name|name|score|[label]"
        parts = resp.split("|")
        return parts[-1] if parts else ""   # the "[label]" part
    except Exception as e:
        log("speaker_label error:", e); return ""

_hangkep = None
try:
    if os.path.exists(HANGKEP_VENV_PY) and os.path.exists(HANGKEP_SCRIPT):
        import hangkep as _hk
        _hangkep = _hk.HangkepEngine()
        log("hangkep: YAMNet sound labelling ACTIVE (disable: touch /tmp/hangkep_off)")
    else:
        log("hangkep: DISABLED (venv/script missing)")
except Exception as _e:
    log(f"hangkep: DISABLED (init error: {_e})")
    _hangkep = None


def hangkep_classify(frames):
    """Labels a non-speech-like (noise-gated) segment with YAMNet.
    Updates /tmp/hangkep.json and, if a NEW sound appears, writes an event to STDOUT.
    Returns SILENTLY on any error — listening can never crash because of it."""
    if _hangkep is None or os.path.exists(HANGKEP_OFF):
        return
    try:
        audio = np.concatenate(frames).astype(np.int16)
        with wave.open(HANGKEP_WAV, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SR)
            wf.writeframes(audio.tobytes())
        p = subprocess.run([HANGKEP_VENV_PY, HANGKEP_SCRIPT, "--json", HANGKEP_WAV],
                           capture_output=True, text=True, timeout=12)
        lines = [l for l in (p.stdout or "").splitlines() if l.strip()]
        if not lines:
            return
        data = json.loads(lines[-1])
        labels = [(d["en"], d["hu"], float(d["score"])) for d in data.get("labels", [])]
        for ev in _hangkep.ingest(labels, time.time()):
            print(f"[hang: {ev}]", flush=True)   # STDOUT -> event for the agent
    except Exception as e:
        log("hangkep error:", e)


def start_arecord():
    return subprocess.Popen(
        ["arecord", "-D", REC_DEV, "-f", "S16_LE", "-r", str(SR), "-c", "1", "-t", "raw", "-q"],
        stdout=subprocess.PIPE, preexec_fn=_pdeathsig)


def revive_mic(old):
    """EAR WATCHDOG: if capture gets stuck in data silence (typically an XMOS wedge —
    also after every Pi boot!), it heals itself:
    stop arecord -> XMOS reboot control command -> restart arecord."""
    try:
        old.kill(); old.wait(timeout=3)
    except Exception:
        pass
    log("EAR WATCHDOG: data silence/EOF -> XMOS reboot + revive...")
    try:
        subprocess.run(["python3", os.path.expanduser("~/picarx-mcp/respeaker_reboot.py")],
                       capture_output=True, timeout=40)
    except Exception as e:
        log("EAR WATCHDOG: respeaker_reboot error:", e)
    time.sleep(2)
    return start_capture()   # fresh arecord + fresh capture thread + fresh queue


# ---- DOUBLE BUFFERING (lesson from an ALSA overrun) ----
# The old code read the arecord pipe in the main thread, so during slow STT/hangkep rounds
# (on 4G even 20+ s!) nobody drained it -> the ALSA buffer overflowed and up to 5+ s of
# audio was lost ("overrun!!!"). Now a separate CAPTURE THREAD reads non-stop, the audio
# waits in a queue; the main thread works from the queue, and speech uttered during
# processing is kept too (it comes out later, in order).
# The robot's own speech (LOCK) is already dropped in the capture thread — so the delayed
# processing still does not hear itself; the hole is marked with a "GAP" marker.

_CAP_Q = [None]   # the current capture queue (a revive brings a fresh one)

def _capture_worker(proc, q):
    fd = proc.stdout.fileno()
    dropped = False
    while True:
        try:
            d = os.read(fd, FRAME * 2 * 4)
        except OSError:
            d = b""
        if not d:
            q.put(b"")    # EOF -> the main thread revives
            return
        if os.path.exists(LOCK):
            dropped = True
            continue
        if dropped:
            dropped = False
            q.put("GAP")
        q.put(d)

def start_capture():
    proc = start_arecord()
    q = _queue.Queue()
    threading.Thread(target=_capture_worker, args=(proc, q), daemon=True).start()
    _CAP_Q[0] = q
    _RBUF[0] = b""
    return proc

_RBUF = [b""]

def read_frame():
    """Exactly FRAME*2 bytes from the capture queue.
    None=stall (data silence), b''=EOF (arecord dead), 'GAP'=hole after the lock."""
    q = _CAP_Q[0]
    while len(_RBUF[0]) < FRAME * 2:
        try:
            d = q.get(timeout=STALL_SEC)
        except _queue.Empty:
            # While the robot SPEAKS (LOCK), the capture thread deliberately drops
            # everything -> the queue is empty, but this is NOT data silence. This used to
            # trigger an XMOS reboot after 5 s during speech (the listener "died" during a
            # long story/answer, and the reboot cut the playback too).
            if os.path.exists(LOCK):
                continue
            return None
        if d == b"":
            return b""
        if d == "GAP":
            _RBUF[0] = b""
            return "GAP"
        _RBUF[0] += d
    out, _RBUF[0] = _RBUF[0][:FRAME * 2], _RBUF[0][FRAME * 2:]
    return out


proc = start_capture()
log(f"listening starts (threshold RMS>={VOL_THRESH}, silence {SILENCE_SEC}s, ear watchdog {STALL_SEC}s)")

frames = []
in_speech = False
speech_cnt = silence_cnt = 0
sil_needed = int(SILENCE_SEC / 0.03)
revive_cnt = 0
# Short-sound path: a single loud frame that does not start speech used to be ignored, so a
# click/bang never reached the labeller. If one frame is loud and no speech starts, we collect
# ~1 s and ONLY the sound labeller gets it (not STT). If speech starts meanwhile, the burst is
# dropped (the speech segment is labelled anyway). Rate limit: BURST_GAP_S.
BURST_FRAMES = 33          # ~1 s (YAMNet's window is ~0.96 s)
BURST_GAP_S = 3.0
burst = None; burst_left = 0; burst_last = 0.0
try:
    while True:
        chunk = read_frame()
        if chunk is None or chunk == b"":
            revive_cnt += 1
            if revive_cnt > MAX_REVIVE:
                log(f"EAR WATCHDOG: {MAX_REVIVE} revives did not help -> EXITING"); break
            proc = revive_mic(proc)
            frames = []; in_speech = False; speech_cnt = silence_cnt = 0
            continue
        revive_cnt = 0
        if chunk == "GAP":                # the robot spoke meanwhile (lock) -> segment boundary
            frames = []; in_speech = False; speech_cnt = silence_cnt = 0; burst = None
            continue
        f = np.frombuffer(chunk, dtype=np.int16).astype(float)
        rms = float(np.sqrt(np.mean(f * f))) if len(f) else 0.0
        # short-sound path — independent of the speech branch, must never raise
        try:
            if in_speech:
                burst = None
            elif burst is not None:
                burst.append(np.frombuffer(chunk, dtype=np.int16)); burst_left -= 1
                if burst_left <= 0:
                    if not os.path.exists('/tmp/no_labels'):
                        hangkep_classify(burst)
                    burst = None; burst_last = time.time()
            elif rms >= VOL_THRESH and time.time() - burst_last >= BURST_GAP_S:
                burst = [np.frombuffer(chunk, dtype=np.int16)]; burst_left = BURST_FRAMES
        except Exception as _e:
            log("burst error:", _e); burst = None
        if rms >= VOL_THRESH:
            speech_cnt += 1; silence_cnt = 0
            if not in_speech and speech_cnt >= SPEECH_START_FRAMES:
                in_speech = True; log("speech...")
            if in_speech:
                frames.append(np.frombuffer(chunk, dtype=np.int16))
                _touch_voice()
        else:
            silence_cnt += 1
            if in_speech:
                frames.append(np.frombuffer(chunk, dtype=np.int16))
                if silence_cnt >= sil_needed:
                    dur = len(frames) * FRAME / SR
                    if dur >= MIN_SPEECH_SEC:
                        sbr = speech_band_ratio(frames)
                        # ADDITIVE: sound labelling on EVERY segment (it used to run only on the
                        # drop branch, but tonal animal sounds — meowing, barking — pass the gate
                        # as speech band and Scribe mislabels them; YAMNet should see these too)
                        if not os.path.exists('/tmp/no_labels'):
                            hangkep_classify(frames)
                        if sbr < SPEECH_BAND_MIN_RATIO and not os.path.exists("/tmp/listen_raw"):
                            log(f"noise gate: not speech-like (band ratio {sbr:.2f}) -> dropped")
                            t = ""
                        else:
                            if sbr < SPEECH_BAND_MIN_RATIO:
                                log(f"noise gate: passed because of RAW mode (band ratio {sbr:.2f})")
                            t = transcribe(frames)
                        if t:
                            try:
                                out = _garble_annotate(t)   # garble suggestion appended to the line (raw text unchanged)
                            except Exception as e:
                                log("garble_annotate error:", e); out = t
                            spk = speaker_label("/tmp/utt.wav")   # speaker label
                            if spk:
                                out = f"{spk} {out}"
                            print(out, flush=True)    # STDOUT -> event for the agent
                    frames = []; in_speech = False; speech_cnt = silence_cnt = 0
            else:
                speech_cnt = 0
finally:
    proc.terminate()
