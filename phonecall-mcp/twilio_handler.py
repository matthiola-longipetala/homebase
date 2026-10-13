"""HTTP + WebSocket server for Twilio webhooks and Media Streams."""

import os
import asyncio
import base64
import json
import time

from aiohttp import web
from twilio.request_validator import RequestValidator

from audio_bridge import AudioBridge
from log_setup import logger

# These will be set by init_handler() from server.py
_config = None
_tts_client = None
_on_call_connected = None  # callback: (stream_sid, call_sid) -> (CallState, first_message)
_on_call_status = None     # callback: (call_sid, status)
_on_inbound_call = None    # callback: (call_sid, from_number) -> CallState
_on_end_call = None        # callback: (call_id) -> dict
_request_validator: RequestValidator | None = None


def init_handler(config: dict, tts_client, on_call_connected=None, on_call_status=None,
                 on_inbound_call=None, on_end_call=None):
    """Initialize the handler with config and callbacks."""
    global _config, _tts_client, _on_call_connected, _on_call_status
    global _request_validator, _on_inbound_call, _on_end_call
    _config = config
    _tts_client = tts_client
    _on_call_connected = on_call_connected
    _on_call_status = on_call_status
    _on_inbound_call = on_inbound_call
    _on_end_call = on_end_call

    # Twilio Request Validation
    auth_token = config.get("twilio_auth_token", "")
    if auth_token:
        _request_validator = RequestValidator(auth_token)
        logger.info("Twilio Request Validator initialized")


def _validate_twilio_request(request: web.Request, post_data: dict, url_override: str = None) -> bool:
    """Validate that an HTTP (or WebSocket upgrade) request is genuinely from Twilio.

    FAIL-CLOSED: without a configured twilio_auth_token nothing is accepted, and an
    invalid X-Twilio-Signature is rejected. The server sits behind a public ngrok URL,
    so anyone who learns the URL could otherwise forge /voice, /status and /media-stream.
    For local debugging only: PHONECALL_SKIP_TWILIO_VALIDATION=1 (logged loudly).
    """
    if os.environ.get("PHONECALL_SKIP_TWILIO_VALIDATION") == "1":
        logger.warning("Twilio signature validation SKIPPED (PHONECALL_SKIP_TWILIO_VALIDATION=1) for %s", request.path)
        return True
    if not _request_validator:
        logger.error("Rejecting %s: twilio_auth_token is not configured, so Twilio requests cannot be verified", request.path)
        return False

    ngrok_url = _config["ngrok_url"].rstrip("/")
    url = url_override or (ngrok_url + request.path)
    signature = request.headers.get("X-Twilio-Signature", "")

    is_valid = _request_validator.validate(url, post_data, signature)
    if not is_valid:
        logger.warning("Invalid Twilio signature for %s: rejected", request.path)
    return bool(is_valid)


async def handle_voice(request: web.Request) -> web.Response:
    """POST /voice - Twilio Voice webhook. Returns TwiML to start a bidirectional Media Stream.

    Handles both outbound calls (initiated by Claude) and inbound calls (voicemail mode).
    Direction is determined by comparing the 'To' number with our Twilio number.
    """
    logger.info("Twilio /voice: request received from %s", request.remote)
    post_data = dict(await request.post())

    call_sid = post_data.get("CallSid", "?")
    from_number = post_data.get("From", "")
    to_number = post_data.get("To", "")
    direction = post_data.get("Direction", "")
    logger.info("Twilio /voice: CallSid=%s From=%s To=%s Direction=%s",
                call_sid, from_number, to_number, direction)

    if not _validate_twilio_request(request, post_data):
        return web.Response(status=403, text="Invalid signature")

    # Detect inbound call: Twilio 'Direction' field or 'To' matches our number
    our_number = _config.get("twilio_phone_number", "")
    is_inbound = (direction == "inbound" or
                  (to_number and our_number and to_number == our_number))

    if is_inbound and _on_inbound_call:
        logger.info("Twilio /voice: INBOUND call from %s", from_number)
        _on_inbound_call(call_sid, from_number)

    ngrok_url = _config["ngrok_url"].rstrip("/")
    ws_url = ngrok_url.replace("https://", "wss://").replace("http://", "ws://")

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{ws_url}/media-stream" dtmfDetection="true" />
    </Connect>
</Response>"""

    logger.info("Twilio /voice: returning TwiML with stream URL %s/media-stream", ws_url)
    return web.Response(text=twiml, content_type="application/xml")


async def handle_media_stream(request: web.Request) -> web.WebSocketResponse:
    """WebSocket /media-stream - Twilio bidirectional Media Stream with AudioBridge."""
    _ws_url = _config["ngrok_url"].rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + request.path
    if not _validate_twilio_request(request, {}, url_override=_ws_url):
        return web.Response(status=403, text="Invalid signature")
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    bridge: AudioBridge | None = None
    call_state = None
    stream_sid = None
    logger.info("Twilio Media Stream: WebSocket connected")

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            data = json.loads(msg.data)
            event = data.get("event")

            if event == "connected":
                logger.info("Twilio Media Stream: connected event received")

            elif event == "start":
                stream_sid = data["start"]["streamSid"]
                call_sid = data["start"].get("callSid", "")
                logger.info("Twilio Media Stream: started (streamSid=%s)", stream_sid)

                # Look up the CallState (works for both outbound and inbound)
                first_message = None
                if _on_call_connected:
                    call_state, first_message = _on_call_connected(stream_sid, call_sid)

                if call_state and call_state.audio_bridge:
                    bridge = call_state.audio_bridge
                    require_consent = (call_state.direction == "outbound")
                    await bridge.start(ws, stream_sid, require_consent=require_consent)

                    if call_state.direction == "inbound":
                        # Inbound: run voicemail flow as a background task
                        asyncio.create_task(
                            _run_voicemail_flow(call_state, bridge, ws)
                        )
                    else:
                        # Outbound: play first_message if provided
                        if first_message:
                            prerendered = getattr(call_state, '_prerendered_audio', None)
                            asyncio.create_task(bridge.speak(first_message, prerendered_audio=prerendered))

            elif event == "media":
                # Route incoming audio to AudioBridge → STT
                if bridge:
                    payload = data.get("media", {}).get("payload", "")
                    if payload:
                        await bridge.feed_twilio_audio(payload)

            elif event == "dtmf":
                digit = data.get("dtmf", {}).get("digit", "")
                logger.info("Twilio Media Stream: DTMF digit=%s", digit)
                if bridge:
                    await bridge.handle_dtmf(digit)

            elif event == "mark":
                mark_name = data.get("mark", {}).get("name", "")
                if bridge:
                    bridge.handle_mark(mark_name)

            elif event == "stop":
                logger.info("Twilio Media Stream: stream stopped")
                break

        elif msg.type == web.WSMsgType.CLOSE:
            logger.warning("Twilio WS CLOSE received: code=%s, reason=%s", msg.data, msg.extra)
            break

        elif msg.type == web.WSMsgType.CLOSING:
            logger.warning("Twilio WS CLOSING")

        elif msg.type == web.WSMsgType.CLOSED:
            logger.warning("Twilio WS CLOSED")
            break

        elif msg.type == web.WSMsgType.ERROR:
            logger.error("Twilio Media Stream: WebSocket error: %s", ws.exception())
            break

    # Cleanup
    if bridge:
        await bridge.stop()
    logger.info("Twilio Media Stream: WebSocket closed")
    return ws


async def _run_voicemail_flow(call_state, bridge: AudioBridge, ws):
    """Run the voicemail flow for inbound calls.

    Flow: greeting → beep → listen for message → thanks → hangup.
    No interactivity, no tool use, no conversation — just record one message.
    """
    from audio_bridge import _generate_hold_tone_ulaw
    from models import TranscriptEntry

    call_id = call_state.call_id
    language = call_state.language
    logger.info("Voicemail: starting flow for %s (call_id=%s)", call_state.phone_number, call_id)

    try:
        # 1. Play greeting (log to transcript)
        prerendered = getattr(call_state, '_prerendered_audio', None)
        greeting = call_state.first_message
        if greeting:
            call_state.transcript.append(TranscriptEntry(
                timestamp=time.time(), speaker="system", text=greeting,
            ))
            await bridge.speak(greeting, prerendered_audio=prerendered)

        # 2. Play beep
        beep_audio = _generate_hold_tone_ulaw(beep_freq=800, beep_ms=300, silence_ms=200, amplitude=0.3)
        await bridge._send_audio(beep_audio)
        await asyncio.sleep(0.5)

        # 3. Listen for message (wait for DTMF "1" or timeout after 60s)
        result = await bridge.wait_for_turn(timeout=60)
        transcript = result.get("transcript", "").strip()

        if transcript:
            call_state.transcript.append(TranscriptEntry(
                timestamp=time.time(),
                speaker="callee",
                text=transcript,
            ))
            logger.info("Voicemail: message received: '%s...'", transcript[:80])
        else:
            logger.info("Voicemail: no message left (empty transcript)")

        # 4. Play thanks message (log to transcript)
        voicemail_config = _config.get("voicemail", {})
        thanks_text = voicemail_config.get("thanks", {}).get(language, "Thank you. Goodbye!")
        call_state.transcript.append(TranscriptEntry(
            timestamp=time.time(), speaker="system", text=thanks_text,
        ))
        await bridge.speak(thanks_text)

        # 5. Short pause then end the call
        await asyncio.sleep(1.0)

    except Exception as e:
        logger.error("Voicemail: flow error: %s", e)

    # 6. End the call (save to log)
    try:
        if _on_end_call:
            result = _on_end_call(call_id)
            logger.info("Voicemail: call ended and logged (call_id=%s)", call_id)

            # Print notification to stderr so Claude can see it
            transcript_text = result.get("transcript", "")
            logger.info("=" * 60)
            logger.info("INBOUND CALL — Message received!")
            logger.info("Caller: %s", call_state.phone_number)
            logger.info("Time: %s", result.get("timestamp", "?"))
            logger.info("Duration: %s sec", result.get("duration_seconds", 0))
            logger.info("Message: %s", transcript_text)
            logger.info("=" * 60)
    except Exception as e:
        logger.error("Voicemail: end_call error: %s", e)


async def handle_status(request: web.Request) -> web.Response:
    """POST /status - Twilio StatusCallback webhook."""
    post_data = dict(await request.post())

    if not _validate_twilio_request(request, post_data):
        return web.Response(status=403, text="Invalid signature")

    call_sid = post_data.get("CallSid", "")
    call_status = post_data.get("CallStatus", "")

    logger.info("Twilio Status: %s -> %s", call_sid, call_status)

    if _on_call_status:
        _on_call_status(call_sid, call_status)

    return web.Response(text="OK")


def create_app() -> web.Application:
    """Create the aiohttp application with all routes."""
    app = web.Application()
    app.router.add_post("/voice", handle_voice)
    app.router.add_get("/media-stream", handle_media_stream)
    app.router.add_post("/status", handle_status)
    return app


async def start_server(host: str, port: int):
    """Start the aiohttp server."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Twilio handler server running on %s:%d", host, port)
