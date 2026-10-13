"""ElevenLabs TTS for phonecall-mcp. Generates ulaw_8000 audio for Twilio."""

from elevenlabs import ElevenLabs

from log_setup import logger


def create_client(api_key: str) -> ElevenLabs:
    """Create an ElevenLabs client."""
    return ElevenLabs(api_key=api_key)


def synthesize_ulaw(text: str, client: ElevenLabs, voice_id: str,
                    model_id: str = "eleven_v3",
                    language_code: str | None = None) -> bytes:
    """Synthesize text to ulaw_8000 audio bytes for Twilio.

    Args:
        text: Text to synthesize
        client: ElevenLabs client instance
        voice_id: ElevenLabs voice ID
        model_id: TTS model ID
        language_code: Optional language hint (e.g. "hu", "en")

    Returns:
        Raw ulaw audio bytes at 8kHz
    """
    kwargs = {
        "text": text,
        "voice_id": voice_id,
        "model_id": model_id,
        "output_format": "ulaw_8000",
    }
    if language_code:
        kwargs["language_code"] = language_code

    logger.info("TTS: synthesizing '%s...' (ulaw_8000)", text[:60])

    audio_stream = client.text_to_speech.convert(**kwargs)
    audio_bytes = b"".join(chunk for chunk in audio_stream if isinstance(chunk, bytes))

    if not audio_bytes:
        logger.warning("TTS: WARNING - empty audio received from API!")

    logger.info("TTS: generated %d bytes", len(audio_bytes))
    return audio_bytes


if __name__ == "__main__":
    # Test: generate a ulaw file
    import os
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("Set ELEVENLABS_API_KEY in .env to test")
        sys.exit(1)

    client = create_client(api_key)

    # Use a default voice for testing if none configured
    voice_id = os.environ.get("TEST_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")

    audio = synthesize_ulaw(
        text="Hello, this is a test of the phone MCP text to speech pipeline.",
        client=client,
        voice_id=voice_id,
    )

    out_path = "test_output.ulaw"
    with open(out_path, "wb") as f:
        f.write(audio)
    print(f"Saved {len(audio)} bytes to {out_path}")
    print(f"Play with: ffplay -f mulaw -ar 8000 -ac 1 {out_path}")
