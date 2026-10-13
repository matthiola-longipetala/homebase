#!/usr/bin/env python3
"""robot_say_rt.py — REAL-TIME speech with the Eleven v3 Conversational model.

Websocket streaming: the base64 mp3 chunks flow through ffmpeg to the HifiBerry as
they arrive — we do not wait for the full synthesis.
Usage: python3 robot_say_rt.py --file <path>   (UTF-8, audio tags OK)
Measurement: prints the time until the first audio (comparable with robot_say).

Lessons built in: adelay 600 ms (the amplifier's power-on delay), volume+limiter,
half-duplex lock (/tmp/robot_speaking.lock), enable_speaker.

A long text once got cut off around 45 s while the script still printed "OK". Now:
  (1) CHUNKING: the text is split at sentence boundaries into pieces of <= DARAB_MAX
      characters, one websocket per piece, but the SAME ffmpeg pipe and lock stay up the
      whole time -> continuous speech;
  (2) TRUNCATION GUARD: every piece needs is_final, and the received mp3 must not be
      suspiciously short for the text (MIN_BAJT_PER_KAR); otherwise the piece is requested ONCE more;
  (3) HONEST OUTPUT: if a piece is truncated a second time -> "WARNING: truncated …" and rc=5
      (robot_say.py then does NOT repeat the whole thing on the REST path).
"""
import asyncio, base64, json, os, re, subprocess, sys, time

CFG = os.path.expanduser("~/picarx-mcp/voice_config.json")
ALSA_DEV = "plughw:CARD=sndrpihifiberry,DEV=0"
GAIN_DB = int(os.environ.get("SAY_GAIN_DB", 12))
LOCK = "/tmp/robot_speaking.lock"
DARAB_MAX = int(os.environ.get("SAY_RT_DARAB", 380))   # ~25 s of speech; the cut-off came around 45 s
MIN_BAJT_PER_KAR = 300   # 128 kbps ≈ 16 kB/s; speech ~15 chars/s -> ~1000 B/char; below 300 it is surely truncated
UJRA_HATAR = 8000        # ~0.5 s of audio; below this nothing was audible yet -> safe to request again

cfg = json.load(open(CFG))
KEY = cfg["elevenlabs_api_key"]
VOICE = __import__("os").environ.get("SAY_VOICE") or cfg["tts"]["voice_id"]

if len(sys.argv) > 2 and sys.argv[1] == "--file":
    text = open(sys.argv[2], encoding="utf-8").read().strip()
else:
    text = sys.argv[1] if len(sys.argv) > 1 else "Hi, this is the real-time voice test."

URI = ("wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input"
       "?model_id=eleven_v3_conversational&output_format=mp3_44100_128")


def darabol(szoveg, maxlen=DARAB_MAX):
    """Splits at sentence boundaries; an over-long sentence at a comma, as a last resort at a space."""
    mondatok = [m for m in re.split(r'(?<=[.!?…])\s+', szoveg.strip()) if m.strip()]
    darabok, akt = [], ""
    for m in mondatok:
        while len(m) > maxlen:   # a single giant sentence: split at a comma, then at a space
            vag = m.rfind(", ", 0, maxlen)
            if vag < maxlen // 3:
                vag = m.rfind(" ", 0, maxlen)
            if vag <= 0:
                vag = maxlen
            if akt:
                darabok.append(akt); akt = ""
            darabok.append(m[:vag + 1].strip()); m = m[vag + 1:].strip()
        if akt and len(akt) + 1 + len(m) > maxlen:
            darabok.append(akt); akt = m
        else:
            akt = (akt + " " + m).strip()
    if akt:
        darabok.append(akt)
    return darabok or [szoveg]


async def egy_darab(ws_mod, darab, ff, t0, allapot):
    """Streams one piece into the shared ffmpeg pipe. Returns: (bytes, got_is_final)."""
    osszes, vegso = 0, False
    async with ws_mod.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps({"voices": [VOICE], "xi_api_key": KEY}))
        await ws.send(json.dumps({"inputs": [{"text": darab, "voice_id": VOICE}]}))
        await ws.send(json.dumps({"flush": True}))
        await ws.send(json.dumps({"close_socket": True}))
        while True:
            try:
                uzenet = json.loads(await ws.recv())
            except Exception:
                break
            if uzenet.get("audio"):
                chunk = base64.b64decode(uzenet["audio"])
                osszes += len(chunk)
                if allapot["elso_hang"] is None:
                    allapot["elso_hang"] = time.monotonic() - t0
                    print(f"  FIRST AUDIO CHUNK: {allapot['elso_hang']:.2f} s", flush=True)
                try:
                    ff.stdin.write(chunk)
                    ff.stdin.flush()
                except BrokenPipeError:
                    break
            if uzenet.get("is_final"):
                vegso = True
                break
    return osszes, vegso


async def fo():
    import websockets
    t0 = time.monotonic()
    allapot = {"elso_hang": None}
    darabok = darabol(text)
    if len(darabok) > 1:
        print(f"  [chunking] {len(text)} characters → {len(darabok)} pieces (≤{DARAB_MAX})", flush=True)

    # ffmpeg: stdin=mp3 stream -> ALSA (decoding on the fly); EVERY piece flows into the same pipe
    ff = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0",
         "-af", f"adelay=600|600,volume={GAIN_DB}dB,alimiter=limit=0.97",
         "-f", "alsa", ALSA_DEV],
        stdin=subprocess.PIPE)

    try:
        from robot_hat.utils import enable_speaker
        enable_speaker()
    except Exception as e:
        print("  speaker enable warn:", e)

    csonka = []
    osszes_bajt = 0
    open(LOCK, "w").close()
    try:
        for i, darab in enumerate(darabok, 1):
            for probalkozas in (1, 2):
                try:
                    bajt, vegso = await egy_darab(websockets, darab, ff, t0, allapot)
                except Exception as e:
                    bajt, vegso = 0, False
                    print(f"  [chunking] piece {i}: connection error: {e}", flush=True)
                gyanus = (not vegso) or bajt < MIN_BAJT_PER_KAR * len(darab)
                if not gyanus:
                    osszes_bajt += bajt
                    break
                # re-request ONLY if almost nothing was played (otherwise the start of the piece would be heard twice)
                ujra = probalkozas == 1 and bajt < UJRA_HATAR
                print(f"  [chunking] piece {i}/{len(darabok)} SUSPICIOUS (is_final={vegso}, {bajt} B / "
                      f"{len(darab)} chars){' → requesting again' if ujra else ''}", flush=True)
                if not ujra:
                    osszes_bajt += bajt
                    csonka.append(i)
                    break
        print(f"  stream end: {time.monotonic()-t0:.2f} s, total {osszes_bajt} byte mp3", flush=True)
    finally:
        try:
            ff.stdin.close()
        except Exception:
            pass
        ff.wait()
        time.sleep(0.3)
        try:
            os.remove(LOCK)
        except OSError:
            pass
    if csonka:
        print(f"WARNING: piece(s) {csonka} may have been truncated — NOT sure that everything was spoken "
              f"(total time {time.monotonic()-t0:.2f} s)", flush=True)
        sys.exit(5)
    print(f"OK: spoken (realtime), total time {time.monotonic()-t0:.2f} s")


asyncio.run(fo())
