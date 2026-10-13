#!/usr/bin/env python3
"""play_audio.py <mp3/wav> [gain_dB] -- plays an audio file on the robot's speaker with the robot_say chain
(ffmpeg volume+limiter -> enable_speaker -> /tmp/robot_speaking.lock -> aplay -> unlock).
Use case: playing a long audio story through the robot.
Thanks to the lock the listener loop does not transcribe the story for its whole length. To interrupt: kill -TERM the process (the lock gets removed)."""
import sys, os, subprocess, time
ALSA_DEV = "plughw:CARD=sndrpihifiberry,DEV=0"
src = sys.argv[1]; gain = float(sys.argv[2]) if len(sys.argv) > 2 else 4.0
WAV = "/tmp/play_audio.wav"; LOCK = "/tmp/robot_speaking.lock"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
                "-af", f"adelay=600|600,volume={gain}dB,alimiter=limit=0.97", "-ar", "44100", "-ac", "2", WAV], check=True)
try:
    from robot_hat.utils import enable_speaker; enable_speaker()
except Exception as e:
    print("speaker enable warn:", e, flush=True)
open(LOCK, "w").close()
t0 = time.time()
try:
    subprocess.run(["aplay", "-q", "-D", ALSA_DEV, WAV])
    time.sleep(0.5)
finally:
    try: os.remove(LOCK)
    except OSError: pass
print("OK: played %.0f s" % (time.time() - t0), flush=True)
