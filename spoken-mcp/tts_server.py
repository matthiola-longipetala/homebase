"""
Spoken MCP — TTS Server for Claude Desktop
with ElevenLabs Text-to-Speech integration.

Claude sends text here via the 'speak' tool,
and the server reads it aloud through ElevenLabs.
"""

import json
import sys
import os
import tempfile
import time
import threading
from pathlib import Path

# TTS playback queue — only one voice can play at a time
_tts_lock = threading.Lock()

# Lockfile to signal STT companion that TTS is playing (prevents feedback loop)
SPEAKING_LOCK = Path(__file__).parent / ".speaking.lock"

from mcp.server.fastmcp import FastMCP

# --- Load configuration ---

# Config location: the SPOKEN_MCP_CONFIG environment variable, if set
# (lets you keep the file with your API key outside the repo folder);
# otherwise config.json next to this script.
CONFIG_PATH = Path(os.environ.get("SPOKEN_MCP_CONFIG") or Path(__file__).parent / "config.json")

def load_config() -> dict:
    """Loads the config.json file."""
    if not CONFIG_PATH.exists():
        print(f"ERROR: config not found at {CONFIG_PATH}! Copy config.example.json as config.json (or point SPOKEN_MCP_CONFIG to your file) and fill it in.", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_config()

# A lockfile left behind by a crashed/killed run would keep the STT companion
# deaf forever -> clear it on startup.
SPEAKING_LOCK.unlink(missing_ok=True)

# --- Initialize ElevenLabs client ---

from elevenlabs import ElevenLabs

elevenlabs_client = ElevenLabs(api_key=config["elevenlabs_api_key"])

# --- Initialize pygame mixer once (avoids popping sounds) ---

import pygame

# Optional output device: config["tts"]["output_device"].
# Value: the full SDL playback device name or a unique part of it
# (e.g. a virtual audio cable). If missing or not found, the system default
# output is used. Run list_audio_devices.py to see the available names.
_MIXER_KW = dict(frequency=44100, size=-16, channels=2, buffer=4096)


def _resolve_sdl_output_device(wanted: str | None) -> str | None:
    """Finds the actual SDL device name by exact or partial match."""
    if not wanted:
        return None
    try:
        from pygame._sdl2 import audio as sdl2_audio
        # Listing devices needs the SDL audio subsystem. IMPORTANT: quit()
        # afterwards, otherwise the mixer stays initialized on the default
        # device and the devicename init() below silently has no effect.
        pygame.mixer.init()
        names = list(sdl2_audio.get_audio_device_names(False))
        pygame.mixer.quit()
    except Exception as e:  # old pygame / no SDL2 audio module
        print(f"TTS: device list unavailable ({e}) -> using default output",
              file=sys.stderr)
        return None
    w = wanted.strip().lower()
    for n in names:
        if n.lower() == w:
            return n
    for n in names:
        if w in n.lower():
            return n
    print(f"TTS: requested output device not found: {wanted!r}. "
          f"Available: {names} -> using default output", file=sys.stderr)
    return None


_OUT_DEV = _resolve_sdl_output_device(config.get("tts", {}).get("output_device"))
try:
    if _OUT_DEV:
        pygame.mixer.init(devicename=_OUT_DEV, **_MIXER_KW)
        print(f"TTS: output device = {_OUT_DEV}", file=sys.stderr)
    else:
        pygame.mixer.init(**_MIXER_KW)
except (TypeError, pygame.error) as e:
    # devicename not supported (pygame < 2.0) or device busy -> fall back
    print(f"TTS: selecting the output device failed ({e}) -> using default output",
          file=sys.stderr)
    pygame.mixer.init(**_MIXER_KW)

# --- Create MCP server ---

mcp = FastMCP(name="spoken-mcp")


@mcp.tool()
def speak(text: str, voice: str | None = None) -> str:
    """Reads text aloud to the user using ElevenLabs TTS.

    Call this with your reply (or the part of it worth hearing) when the user
    talks to you by voice. Playback happens in the background; this returns
    immediately.

    Args:
        text: The text to read aloud
        voice: Optional voice ID (if not provided, uses the one from config.json)

    Returns:
        Status message
    """
    try:
        # Voice ID: from parameter or config
        voice_id = voice or config["tts"]["voice_id"]
        model_id = config["tts"].get("model_id", "eleven_multilingual_v2")
        language_code = config["tts"].get("language_code", "hu")

        print(f"TTS: reading aloud '{text[:50]}...' ({voice_id})", file=sys.stderr)

        # Start TTS in a background thread — the tool returns IMMEDIATELY to Claude
        # This way Claude doesn't wait for playback to finish, the text response appears right away
        def _tts_background():
            with _tts_lock:  # Queues the calls — two voices won't overlap
                try:
                    # MP3 format — better audio quality
                    audio_stream = elevenlabs_client.text_to_speech.convert(
                        text=text,
                        voice_id=voice_id,
                        model_id=model_id,
                        language_code=language_code,
                        output_format="mp3_44100_128",
                    )

                    # Collect audio
                    audio_bytes = b"".join(
                        chunk for chunk in audio_stream if isinstance(chunk, bytes)
                    )

                    if audio_bytes:
                        # Signal STT companion to pause VAD (prevents feedback loop)
                        SPEAKING_LOCK.touch()
                        try:
                            _play_audio_mp3(audio_bytes)
                            time.sleep(0.5)  # Let VAD settle after playback ends
                        finally:
                            SPEAKING_LOCK.unlink(missing_ok=True)
                    else:
                        print("TTS: empty audio received from API!", file=sys.stderr)
                except Exception as e:
                    print(f"TTS background error: {e}", file=sys.stderr)

        threading.Thread(target=_tts_background, daemon=True).start()

        return f"Read aloud: {text[:100]}{'...' if len(text) > 100 else ''}"

    except Exception as e:
        error_msg = f"TTS error: {str(e)}"
        print(error_msg, file=sys.stderr)
        return error_msg


def _play_audio_mp3(audio_bytes: bytes):
    """Plays MP3 audio using pygame."""

    # Save the MP3 to a temp file
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            pygame.time.wait(100)
        pygame.mixer.music.unload()
        time.sleep(0.1)  # Give Windows time to release the file handle
    except Exception as e:
        print(f"Playback error: {e}", file=sys.stderr)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# --- Start server ---

if __name__ == "__main__":
    print("Spoken MCP server starting...", file=sys.stderr)
    mcp.run(transport="stdio")
