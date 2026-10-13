"""Call lifecycle management for phonecall-mcp."""

import asyncio
import json
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from twilio.rest import Client as TwilioClient

from models import CallState, TranscriptEntry
from audio_bridge import AudioBridge
from log_setup import logger

CALL_LOG_PATH = Path(__file__).parent / "call_log.json"

# Single active call constraint
_active_call: CallState | None = None
_lock = threading.Lock()

# Twilio client (initialized by init_manager)
_twilio_client: TwilioClient | None = None
_config: dict | None = None
_tts_client = None  # ElevenLabs client for AudioBridge


def init_manager(config: dict, tts_client=None):
    """Initialize the call manager with config."""
    global _twilio_client, _config, _tts_client
    _config = config
    _tts_client = tts_client
    _twilio_client = TwilioClient(
        config["twilio_account_sid"],
        config["twilio_auth_token"],
    )
    logger.info("Call manager initialized")


def get_active_call() -> CallState | None:
    """Get the currently active call, if any."""
    return _active_call


def initiate_call(phone_number: str, language: str, context: str,
                  first_message: str = "") -> CallState:
    """Start an outbound call via Twilio."""
    global _active_call

    with _lock:
        if _active_call and _active_call.status not in ("completed", "failed"):
            raise RuntimeError(
                f"A call is already active (call_id={_active_call.call_id}, "
                f"status={_active_call.status}). End it first."
            )

        call_state = CallState(
            phone_number=phone_number,
            language=language,
            context=context,
            first_message=first_message,
        )

        # Create AudioBridge for this call
        call_state.audio_bridge = AudioBridge(
            config=_config,
            tts_client=_tts_client,
            language=language,
        )

        _active_call = call_state

    ngrok_url = _config["ngrok_url"].rstrip("/")

    # Pre-render first_message TTS while the phone is ringing
    if first_message:
        logger.info("Pre-rendering first_message TTS...")
        from tts import synthesize_ulaw
        tts_config = _config.get("tts", {})
        try:
            call_state._prerendered_audio = synthesize_ulaw(
                text=first_message,
                client=_tts_client,
                voice_id=tts_config.get("voice_id", ""),
                model_id=tts_config.get("model_id", "eleven_v3"),
                language_code=language,
            )
            logger.info("First message pre-rendered (%d bytes)", len(call_state._prerendered_audio))
        except Exception as e:
            logger.error("Pre-render failed, will synthesize on connect: %s", e)
            call_state._prerendered_audio = None

    logger.info("Initiating call to %s...", phone_number)

    try:
        twilio_call = _twilio_client.calls.create(
            to=phone_number,
            from_=_config["twilio_phone_number"],
            url=f"{ngrok_url}/voice",
            status_callback=f"{ngrok_url}/status",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        call_state.twilio_call_sid = twilio_call.sid
        call_state.status = "ringing"
        logger.info("Call created: SID=%s", twilio_call.sid)

    except Exception as e:
        call_state.status = "failed"
        logger.error("Call initiation failed: %s", e)
        raise RuntimeError(f"Failed to start call: {e}")

    return call_state


def create_inbound_call(call_sid: str, from_number: str) -> CallState:
    """Create a CallState for an inbound call (voicemail mode)."""
    global _active_call

    # Determine language based on caller's number
    voicemail_config = _config.get("voicemail", {})
    default_lang = voicemail_config.get("default_language", "en")
    local_prefixes = voicemail_config.get("local_prefixes", [])

    # If caller's number matches a local prefix, use default language;
    # otherwise fall back to English
    language = default_lang
    if local_prefixes and from_number:
        is_local = any(from_number.startswith(p) for p in local_prefixes)
        if not is_local:
            language = "en"

    greeting = voicemail_config.get("greeting", {}).get(language, "")

    call_state = CallState(
        phone_number=from_number,
        language=language,
        context="Inbound call (voicemail)",
        first_message=greeting,
        direction="inbound",
    )
    call_state.twilio_call_sid = call_sid
    call_state.audio_bridge = AudioBridge(
        config=_config,
        tts_client=_tts_client,
        language=language,
    )

    # Pre-render greeting
    if greeting:
        from tts import synthesize_ulaw
        tts_config = _config.get("tts", {})
        try:
            call_state._prerendered_audio = synthesize_ulaw(
                text=greeting,
                client=_tts_client,
                voice_id=tts_config.get("voice_id", ""),
                model_id=tts_config.get("model_id", "eleven_v3"),
                language_code=language,
            )
            logger.info("Inbound: greeting pre-rendered (%d bytes)", len(call_state._prerendered_audio))
        except Exception as e:
            logger.error("Inbound: greeting pre-render failed: %s", e)

    with _lock:
        _active_call = call_state

    logger.info("Inbound call from %s (lang=%s, call_id=%s)", from_number, language, call_state.call_id)
    return call_state


def on_media_stream_connected(stream_sid: str, call_sid: str) -> tuple:
    """Called when the Twilio Media Stream WebSocket connects.

    Returns (CallState, first_message) tuple.
    """
    global _active_call

    with _lock:
        if _active_call and (_active_call.twilio_call_sid == call_sid or not _active_call.stream_sid):
            _active_call.stream_sid = stream_sid
            _active_call.status = "connected"
            first_msg = _active_call.first_message or None
            logger.info("Call %s: media stream connected (direction=%s)",
                        _active_call.call_id, _active_call.direction)
            return (_active_call, first_msg)

    logger.warning("Media stream connected but no matching call for SID %s", call_sid)
    return (None, None)


def on_call_status_update(call_sid: str, status: str):
    """Called when Twilio sends a status callback."""
    global _active_call

    with _lock:
        if _active_call and _active_call.twilio_call_sid == call_sid:
            status_map = {
                "initiated": "initiating",
                "ringing": "ringing",
                "in-progress": "connected",
                "completed": "completed",
                "busy": "failed",
                "no-answer": "failed",
                "canceled": "failed",
                "failed": "failed",
            }
            new_status = status_map.get(status, status)
            _active_call.status = new_status
            logger.info("Call %s: status -> %s", _active_call.call_id, new_status)


async def listen(call_id: str, timeout: float = 300) -> dict:
    """Wait for callee to finish speaking (DTMF "1").

    Returns dict with transcript and event info.
    """
    with _lock:
        if not _active_call or _active_call.call_id != call_id:
            raise RuntimeError(f"No active call with id={call_id}")
        if _active_call.status != "connected":
            raise RuntimeError(f"Call not connected (status={_active_call.status})")
        bridge = _active_call.audio_bridge

    if not bridge:
        raise RuntimeError("AudioBridge not initialized")

    # GDPR consent check — wait for consent before first listen
    if not bridge._consent_given:
        consent_result = await bridge.wait_for_consent(timeout=30)
        if consent_result["event"] == "consent_timeout":
            # Callee didn't consent within 30s — signal Claude to end call
            return {
                "event": "consent_timeout",
                "transcript": "",
            }
        # Consent given — return to Claude so it can send confirmation + instructions
        return {
            "event": "consent_given",
            "transcript": "",
        }

    result = await bridge.wait_for_turn(timeout=timeout)

    # Record in transcript
    if result["transcript"]:
        with _lock:
            if _active_call:
                _active_call.transcript.append(TranscriptEntry(
                    timestamp=time.time(),
                    speaker="callee",
                    text=result["transcript"],
                ))

    return result


async def respond(call_id: str, message: str) -> dict:
    """Send Claude's response as TTS to the callee.

    Returns dict with status (completed or barged_in).
    """
    with _lock:
        if not _active_call or _active_call.call_id != call_id:
            raise RuntimeError(f"No active call with id={call_id}")
        if _active_call.status != "connected":
            raise RuntimeError(f"Call not connected (status={_active_call.status})")
        bridge = _active_call.audio_bridge

    if not bridge:
        raise RuntimeError("AudioBridge not initialized")

    # Record in transcript
    with _lock:
        if _active_call:
            _active_call.transcript.append(TranscriptEntry(
                timestamp=time.time(),
                speaker="claude",
                text=message,
            ))

    result = await bridge.speak(message)
    return result


def end_call(call_id: str) -> dict:
    """End an active call and return summary with transcript."""
    global _active_call

    with _lock:
        if not _active_call or _active_call.call_id != call_id:
            raise RuntimeError(f"No active call with id={call_id}")

        call = _active_call
        duration = time.time() - call.start_time

    # Hang up via Twilio API
    if call.twilio_call_sid and call.status not in ("completed", "failed"):
        try:
            _twilio_client.calls(call.twilio_call_sid).update(status="completed")
            logger.info("Call %s: hung up via Twilio API", call_id)
        except Exception as e:
            logger.warning("Call %s: hangup error (may already be ended): %s", call_id, e)

    # Format transcript
    transcript_lines = []
    for entry in call.transcript:
        label = {"claude": "Claude", "callee": "Callee", "system": "System"}.get(
            entry.speaker, entry.speaker.capitalize()
        )
        transcript_lines.append(f"[{label}] {entry.text}")

    transcript_text = "\n".join(transcript_lines) if transcript_lines else "(no transcript)"

    # Build timestamp in configured timezone
    tz_name = _config.get("timezone", "UTC")
    try:
        tz = ZoneInfo(tz_name)
    except KeyError:
        logger.warning("Unknown timezone '%s', falling back to UTC", tz_name)
        tz = timezone.utc
    call_timestamp = datetime.fromtimestamp(call.start_time, tz=tz).isoformat()

    result = {
        "call_id": call_id,
        "timestamp": call_timestamp,
        "direction": call.direction,
        "phone_number": call.phone_number,
        "status": "completed",
        "duration_seconds": round(duration, 1),
        "language": call.language,
        "context": call.context,
        "transcript": transcript_text,
    }

    # Save to call log
    _save_to_call_log(result)

    with _lock:
        call.status = "completed"
        _active_call = None

    return result


def _save_to_call_log(call_result: dict):
    """Append a call record to the local call log JSON file."""
    try:
        if CALL_LOG_PATH.exists():
            data = json.loads(CALL_LOG_PATH.read_text(encoding="utf-8"))
        else:
            data = {"calls": []}

        data["calls"].append(call_result)

        CALL_LOG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Call log: saved to %s", CALL_LOG_PATH)
    except Exception as e:
        logger.error("Call log: save failed: %s", e)
