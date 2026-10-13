#!/usr/bin/env python3
"""Cause of slow Scribe responses: Wi-Fi UPLOAD or the ElevenLabs API?
Two separate measurements from the Pi:
  1) neutral upload (httpbin.org/post, 400 kB of random data) -> speed_upload
  2) ElevenLabs Scribe with a TINY (1 s of silence) wav -> time_starttransfer − upload ≈ the server's thinking time
The key comes from voice_config.json and is NEVER printed."""
import json, os, subprocess, wave, io, struct
cfg = json.load(open(os.path.expanduser('~/picarx-mcp/voice_config.json'))); KEY = cfg['elevenlabs_api_key']
FMT = '%{http_code} up=%{speed_upload}B/s conn=%{time_connect} start=%{time_starttransfer} total=%{time_total} size_up=%{size_upload}'

def curl(args, stdin=None):
    p = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', FMT, '--connect-timeout', '5', '--max-time', '60'] + args,
                       input=stdin, capture_output=True, text=True)
    return p.stdout.replace(KEY, '***')

print('--- wifi:'); os.system("iwconfig wlan0 2>/dev/null | grep -oE 'Bit Rate=[^ ]*|Link Quality=[^ ]*|Signal level=[^ ]*' | xargs echo; nmcli -t -f NAME,DEVICE con show --active | grep wlan0")
open('/tmp/rand400k.bin', 'wb').write(os.urandom(400_000))
for i in range(3):
    print('httpbin 400k:', curl(['-X', 'POST', '-F', 'file=@/tmp/rand400k.bin', 'https://httpbin.org/post']))
buf = io.BytesIO()
with wave.open(buf, 'wb') as wf:
    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(b'\x00\x00' * 16000)
open('/tmp/csend1s.wav', 'wb').write(buf.getvalue())
for i in range(3):
    print('scribe 1s silence:', curl(['-X', 'POST', 'https://api.elevenlabs.io/v1/speech-to-text', '-K', '-',
                                    '-F', 'model_id=scribe_v1', '-F', 'language_code=' + cfg.get('stt', {}).get('language_code', 'en'), '-F', 'file=@/tmp/csend1s.wav;type=audio/wav'],
                                   stdin=f'header = "xi-api-key: {KEY}"\n'))
for i in range(2):
    print('scribe ~400k wav:', curl(['-X', 'POST', 'https://api.elevenlabs.io/v1/speech-to-text', '-K', '-',
                                    '-F', 'model_id=scribe_v1', '-F', 'language_code=' + cfg.get('stt', {}).get('language_code', 'en'), '-F', 'file=@/tmp/utt.wav;type=audio/wav'],
                                   stdin=f'header = "xi-api-key: {KEY}"\n') if os.path.exists('/tmp/utt.wav') else 'no /tmp/utt.wav')
