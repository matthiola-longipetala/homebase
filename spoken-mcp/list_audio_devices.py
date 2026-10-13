"""List audio devices — helper for choosing input/output devices.

Usage:  python list_audio_devices.py

Prints (a) the INPUT devices as sounddevice sees them — use one for
config["audio"]["input_device"]; and (b) the OUTPUT devices as SDL/pygame sees
them — use one for config["tts"]["output_device"].

A unique PART of the name is enough in the config (e.g. "Out B2"); you do not
need the full device name with the host API in brackets.

Typical use: routing Claude's voice into a call or recording app through a
virtual audio cable (e.g. VB-Audio VoiceMeeter or VB-Cable): set output_device
to the cable's input and input_device to the cable's output.
"""

import sys

# Some Windows consoles use a legacy code page and choke on non-ASCII device
# names -> force UTF-8 output, replacing what cannot be printed.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def inputs():
    import sounddevice as sd
    print("=" * 70)
    print("INPUT devices — sounddevice   ->  config['audio']['input_device']")
    print("=" * 70)
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = None
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            mark = "  <- DEFAULT" if i == default_in else ""
            api = sd.query_hostapis(d["hostapi"])["name"]
            print(f"  [{i:2}] {d['name']}   ({api}, {d['max_input_channels']} ch){mark}")
    print()


def outputs():
    import pygame
    from pygame._sdl2 import audio as sdl2_audio
    print("=" * 70)
    print("OUTPUT devices — SDL/pygame   ->  config['tts']['output_device']")
    print("=" * 70)
    pygame.mixer.init()
    names = list(sdl2_audio.get_audio_device_names(False))
    pygame.mixer.quit()
    for n in names:
        print(f"  - {n}")
    print()
    print("(The first one is usually the system default.)")
    print()


if __name__ == "__main__":
    try:
        inputs()
    except Exception as e:
        print(f"[input list error] {e}", file=sys.stderr)
    try:
        outputs()
    except Exception as e:
        print(f"[output list error] {e}", file=sys.stderr)
