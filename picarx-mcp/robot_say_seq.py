#!/usr/bin/env python3
"""robot_say_seq.py — SENTENCE-BY-SENTENCE speech, LISTENING between sentences.

Why: in half-duplex I am deaf while speaking. This is step 1: I split the text into sentences,
play each sentence separately, RELEASE the lock in a short pause between sentences, and if the
listener heard speech meanwhile (/tmp/last_voice_ts newer than the end of the sentence), I STOP:
I do not say the rest, I print [INTERRUPTED ...], and exit with code 3 -> the
agent knows it should listen. (Step 2 later: real mid-word barge-in based on AEC/DOA.)

The audio of the sentences is synthesised IN ADVANCE, in parallel (REST), so between sentences
there is only the deliberate pause, not network waiting. Usage: robot_say.py calls it when there
are >=2 sentences. SAY_WHOLE=1 -> the old in-one-go path.
"""
import sys, os, re, json, time, subprocess, urllib.request, urllib.error, threading

CFG = os.path.expanduser("~/picarx-mcp/voice_config.json")
ALSA_DEV = "plughw:CARD=sndrpihifiberry,DEV=0"
GAIN_DB = int(os.environ.get("SAY_GAIN_DB", 12))
LOCK = "/tmp/robot_speaking.lock"
VOICE_TS = "/tmp/last_voice_ts"
GAP_SEC = float(os.environ.get("SAY_GAP", 1.5))      # listening pause between sentences
MIN_SENT = 25                                         # a sentence shorter than this is glued to its neighbour

cfg = json.load(open(CFG))
KEY = cfg["elevenlabs_api_key"]
VOICE = __import__("os").environ.get("SAY_VOICE") or cfg["tts"]["voice_id"]
MODEL = cfg["tts"].get("model_id", "eleven_multilingual_v2")
LANG = cfg["tts"].get("language_code", "en")


def last_voice():
    try:
        return float(open(VOICE_TS).read().strip())
    except Exception:
        return 0.0


def split_sentences(text):
    parts = re.split(r'(?<=[.!?…])\s+', text.strip())
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and (len(out[-1]) < MIN_SENT or len(p) < MIN_SENT):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


def tts(text):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}"
    for model, lang in ((MODEL, True), (MODEL, False), ("eleven_multilingual_v2", False)):
        body = {"text": text, "model_id": model}
        if lang:
            body["language_code"] = LANG
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"xi-api-key": KEY, "Content-Type": "application/json",
                                              "Accept": "audio/mpeg"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            print(f"  tts HTTP {e.code} ({model}) -> next attempt", flush=True)
        except Exception as e:
            print(f"  tts error: {e}", flush=True)
    return None


_SEM = threading.Semaphore(int(os.environ.get("SAY_PAR", 3)))   # against ElevenLabs 429: max 3 parallel requests


def synth_to_wav(i, text, results):
    with _SEM:
        audio = tts(text)
    if not audio:
        results[i] = None
        return
    mp3 = f"/tmp/robot_say_seq_{i}.mp3"
    wav = f"/tmp/robot_say_seq_{i}.wav"
    open(mp3, "wb").write(audio)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
                    "-af", f"adelay=600|600,volume={GAIN_DB}dB,alimiter=limit=0.97",
                    "-ar", "44100", "-ac", "2", wav], check=True)
    results[i] = wav


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--file":
        text = open(sys.argv[2], encoding="utf-8").read().strip()
    else:
        text = sys.argv[1] if len(sys.argv) > 1 else "One. Two. Three."
    sents = split_sentences(text)
    t0 = time.monotonic()
    results = [None] * len(sents)
    ths = [threading.Thread(target=synth_to_wav, args=(i, s, results)) for i, s in enumerate(sents)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    print(f"  {len(sents)} sentences synthesised in {time.monotonic()-t0:.1f} s", flush=True)
    try:
        from robot_hat.utils import enable_speaker
        enable_speaker()
    except Exception as e:
        print("  speaker enable warn:", e)
    said = 0
    for i, wav in enumerate(results):
        if wav is None:
            print(f"  [sentence {i+1}: no audio, skipped]", flush=True)
            continue
        open(LOCK, "w").close()
        try:
            subprocess.run(["aplay", "-q", "-D", ALSA_DEV, wav])
            time.sleep(0.5)                           # let the tail of my own voice (echo) die down WHILE locked
        finally:
            try:
                os.remove(LOCK)
            except OSError:
                pass
        t_unlock = time.time()                        # only speech heard AFTER the lock counts as an interruption
        said += 1
        if i < len(results) - 1:
            time.sleep(GAP_SEC)                       # listening pause: this is where the listener hears
            if last_voice() > t_unlock:
                rest = len(results) - i - 1
                print("[INTERRUPTED: I heard someone after sentence " + str(i+1) + "; "
                      + str(rest) + " sentence(s) skipped: " + " ".join(sents[i+1:])[:200] + "]", flush=True)
                sys.exit(3)
    # NO SILENT FAILURE. This branch used to print "OK: spoken (0/N)" even with `said=0` and
    # exit with 0 -- and the caller robot_say.py reads 0 as SUCCESS (rc in (0,3) -> sys.exit).
    # So when the ElevenLabs quota ran out, the whole chain REPORTED SUCCESS while the robot
    # did not say a word. That is the most expensive kind of bug: a silent failure that looks
    # like success. Now: if NOT ONE sentence was spoken -> rc=4, and the caller falls through
    # to the fallback branch. Partial success (said>0) STAYS 0 -- otherwise the caller would
    # say the whole thing again, twice.
    if said == 0:
        print(f"  ⚠️ NOT ONE SENTENCE WAS SPOKEN (out of {len(sents)}) -- "
              "probably the ElevenLabs quota ran out or there is no network. rc=4", flush=True)
        sys.exit(4)
    if said < len(sents):
        print(f"  ⚠️ PARTIAL: {said}/{len(sents)} sentences spoken, {len(sents)-said} stayed SILENT.",
              flush=True)
    print(f"OK: spoken sentence by sentence ({said}/{len(sents)}), total time {time.monotonic()-t0:.1f} s")


if __name__ == "__main__":
    main()
