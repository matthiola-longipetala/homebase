#!/usr/bin/env python3
"""robot_say.py — the robot SPEAKS the text in the agent's ElevenLabs voice.

ElevenLabs TTS (REST) -> MP3 -> wav (ffmpeg) -> the robot's HifiBerry speaker (by card NAME).
The key/voice come from voice_config.json.
Usage: python3 robot_say.py "text to say"    (or: --file <utf-8 text file>)
"""
import os, sys, json, subprocess, urllib.request, urllib.error

CFG = os.path.expanduser("~/picarx-mcp/voice_config.json")
MP3 = "/tmp/robot_say.mp3"
WAV = "/tmp/robot_say.wav"
ALSA_DEV = "plughw:CARD=sndrpihifiberry,DEV=0"   # HifiBerry BY NAME (the card number can shift after a reboot!)
GAIN_DB = int(__import__("os").environ.get("SAY_GAIN_DB", 12))

cfg = json.load(open(CFG))
KEY = cfg["elevenlabs_api_key"]
VOICE = __import__("os").environ.get("SAY_VOICE") or cfg["tts"]["voice_id"]
MODEL = __import__("os").environ.get("SAY_MODEL") or cfg["tts"].get("model_id", "eleven_multilingual_v2")  # SAY_MODEL env = one-off model override (for listening tests)
LANG = cfg["tts"].get("language_code", "en")
if len(sys.argv) > 2 and sys.argv[1] == "--file":
    # UTF-8 text from a file (so accented characters survive the PowerShell->ssh->bash chain)
    text = open(sys.argv[2], encoding="utf-8").read().strip()
else:
    text = sys.argv[1] if len(sys.argv) > 1 else "Hi, this is a test."


# Politeness: the listener updates /tmp/last_voice_ts on every speech it hears; if the last word
# is less than 1 s old, I wait (at most 5 s), then speak. SAY_NO_WAIT=1 -> skip.
import os as _os, time as _time
def _wait_for_silence(quiet=1.0, max_wait=5.0):
    t0 = _time.time()
    while _time.time() - t0 < max_wait:
        try:
            last = float(open("/tmp/last_voice_ts").read().strip())
        except Exception:
            return 0.0
        if _time.time() - last >= quiet:
            break
        _time.sleep(0.1)
    return _time.time() - t0
if not _os.environ.get("SAY_NO_WAIT"):
    _w = _wait_for_silence()
    if _w > 0.2:
        print(f"  [politeness: waited {_w:.1f} s until you stopped talking]", flush=True)

# Sentence by sentence, listening between sentences; if someone starts talking, it stops (rc=3). SAY_WHOLE=1 -> in one go.
import re as _re
if not _os.environ.get("SAY_WHOLE") and len([x for x in _re.split(r'(?<=[.!?\u2026])\s+', text.strip()) if x.strip()]) >= 2:
    _rc = subprocess.run([sys.executable, os.path.expanduser("~/picarx-mcp/robot_say_seq.py")] + sys.argv[1:]).returncode
    if _rc in (0, 3):
        sys.exit(_rc)
    if _rc == 4:
        # rc=4: the sentence path could not say a SINGLE sentence (quota/network).
        # The "in one go" path would hit the same wall, only slower -> go straight to the local fallback.
        print("  [the sentence-by-sentence path could not say a single sentence (rc=4) "
              "-> straight to the local machine voice]", flush=True)
        _rc_loc = subprocess.run([sys.executable, os.path.expanduser("~/picarx-mcp/robot_say_local.py")]
                                 + sys.argv[1:]).returncode
        sys.exit(0 if _rc_loc == 0 else 1)
    print(f"  [sentence-by-sentence path failed rc={_rc} -> saying it in one go]", flush=True)

# REALTIME SWITCH: if tts.realtime=true in the config, we speak with the new
# streaming engine (robot_say_rt.py / eleven_v3_conversational);
# if it fails (network hiccup), we fall back here to the old REST path.
# Disable for one call: SAY_NO_RT=1 environment variable.
import os as _os
if cfg["tts"].get("realtime") and not _os.environ.get("SAY_NO_RT"):
    rc = subprocess.run([sys.executable, os.path.expanduser("~/picarx-mcp/robot_say_rt.py")] + sys.argv[1:]).returncode
    if rc == 0:
        sys.exit(0)
    if rc == 5:
        # the realtime path spoke, but one chunk may have been truncated -> we do NOT repeat the
        # whole thing on the REST path (it would be heard twice); robot_say_rt already printed the warning.
        sys.exit(5)
    print(f"  [realtime failed rc={rc} -> fallback to the REST path]", flush=True)


def tts(model_id, with_lang):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}"
    body = {"text": text, "model_id": model_id}
    if with_lang:
        body["language_code"] = LANG
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"xi-api-key": KEY, "Content-Type": "application/json",
                 "Accept": "audio/mpeg"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read(), 200
    except urllib.error.HTTPError as e:
        return e.read(), e.code


# 1) try the configured model (with language code), then fall back
audio, code = tts(MODEL, True)
if code != 200:
    print(f"  proba1 ({MODEL}, lang) HTTP {code}: {audio[:160]!r}", flush=True)
    audio, code = tts(MODEL, False)
if code != 200:
    print(f"  proba2 ({MODEL}, no-lang) HTTP {code}: {audio[:160]!r}", flush=True)
    audio, code = tts("eleven_multilingual_v2", False)
if code != 200:
    # END OF THE CHAIN — LOCAL, NETWORK-INDEPENDENT FALLBACK.
    # This used to be `sys.exit(1)`, i.e. the robot STAYED MUTE (e.g. when the monthly
    # ElevenLabs quota ran out). The three "fallbacks" above (realtime->REST,
    # sentence-by-sentence->in one go, model->model) all live inside the SAME external
    # service, so they fail together. This branch steps outside it.
    # This branch used to be a TOTAL failure anyway -- anything here can only improve it.
    print(f"  ERROR: HTTP {code}: {audio[:200]!r}", flush=True)
    print("  -> falling back to the local machine voice (espeak)", flush=True)
    _rc_loc = subprocess.run([sys.executable, os.path.expanduser("~/picarx-mcp/robot_say_local.py")]
                             + sys.argv[1:]).returncode
    sys.exit(0 if _rc_loc == 0 else 1)

open(MP3, "wb").write(audio)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", MP3,
                "-af", f"adelay=600|600,volume={GAIN_DB}dB,alimiter=limit=0.97",   # adelay: the amplifier's power-on delay used to clip the first half second
                "-ar", "44100", "-ac", "2", WAV], check=True)
try:
    from robot_hat.utils import enable_speaker
    enable_speaker()
except Exception as e:
    print("  speaker enable warn:", e)

# half-duplex: signal to the listening loop NOT to listen while I speak
import os, time
LOCK = "/tmp/robot_speaking.lock"
open(LOCK, "w").close()
try:
    subprocess.run(["aplay", "-D", ALSA_DEV, WAV])
    time.sleep(0.5)   # let the VAD settle after playback
finally:
    try: os.remove(LOCK)
    except OSError: pass
print(f"OK: spoken ({len(audio)} byte mp3)")
