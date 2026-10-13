"""
phonecall-mcp — MCP server for Claude Desktop
Enables phone calls via Twilio + ElevenLabs TTS/STT.

Claude sends commands here via MCP tools,
and the server manages outbound phone calls with bidirectional audio.
"""

import asyncio
import json
import sys
import threading
import time

from mcp.server.fastmcp import FastMCP

from log_setup import logger
from config import config
from tts import create_client as create_tts_client
from call_manager import (
    init_manager,
    initiate_call,
    end_call,
    get_active_call,
    on_media_stream_connected,
    on_call_status_update,
    create_inbound_call,
    listen,
    respond,
)
from twilio_handler import init_handler, start_server

# --- Initialize components ---

tts_client = create_tts_client(config["elevenlabs_api_key"])
init_manager(config, tts_client=tts_client)
init_handler(
    config=config,
    tts_client=tts_client,
    on_call_connected=on_media_stream_connected,
    on_call_status=on_call_status_update,
    on_inbound_call=create_inbound_call,
    on_end_call=end_call,
)

# --- Background asyncio event loop for Twilio HTTP/WS server ---

_bg_loop: asyncio.AbstractEventLoop | None = None


def _start_background_server():
    global _bg_loop
    _bg_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_bg_loop)

    host = config.get("server", {}).get("host", "127.0.0.1")   # ngrok runs on this machine: no LAN exposure needed
    port = config.get("server", {}).get("port", 8765)

    _bg_loop.run_until_complete(start_server(host, port))
    _bg_loop.run_forever()


_server_thread = threading.Thread(target=_start_background_server, daemon=True)
_server_thread.start()
time.sleep(0.5)  # Let the server start


def _run_async(coro):
    """Run an async coroutine in the background event loop and wait for result."""
    if not _bg_loop:
        raise RuntimeError("Background event loop not running")
    future = asyncio.run_coroutine_threadsafe(coro, _bg_loop)
    return future.result(timeout=600)  # 10 min max


# --- Create MCP server ---

mcp = FastMCP(name="phonecall-mcp")


@mcp.tool()
def phone_call_start(
    phone_number: str,
    language: str,
    context: str,
    first_message: str = "",
) -> str:
    """Start an outbound phone call via Twilio.

    IMPORTANT: Before calling this tool, always describe to the user who you're
    calling and why, and wait for their explicit approval.

    The first_message should include a brief explanation of the DTMF turn-taking
    mechanism: "When you're done speaking, please press 1 on your phone."

    Args:
        phone_number: Phone number in E.164 format (e.g. "+14155551234")
        language: Language code for the call (e.g. "hu", "en", "es").
                  Determines TTS language, STT language, and filler messages.
        context: Briefing about the call - who we're calling, why, the relationship,
                 and what register to use. Claude uses this to determine greeting style.
        first_message: The greeting/introduction to say when connected.
                       Should explain the DTMF mechanism to the callee.

    Returns:
        JSON with call_id and status, or error message
    """
    try:
        call_state = initiate_call(
            phone_number=phone_number,
            language=language,
            context=context,
            first_message=first_message,
        )
        return json.dumps({
            "call_id": call_state.call_id,
            "status": call_state.status,
            "phone_number": call_state.phone_number,
        })
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def phone_call_listen(call_id: str, timeout: float = 120) -> str:
    """Listen to the callee and return their transcript when they press DTMF "1".

    This tool blocks until the callee presses "1" on their phone keypad,
    signaling they've finished speaking. While waiting:
    - The callee's speech is transcribed in real-time via ElevenLabs Scribe STT
    - If the callee is silent for 20+ seconds, a DTMF reminder plays automatically
    - If you (Claude) take too long to respond after this returns, filler
      messages play automatically ("One moment please...")

    TYPICAL USAGE LOOP:
    1. phone_call_start → call connects, first_message plays
    2. phone_call_listen → wait for callee response
    3. (think, use tools, search, etc.)
    4. phone_call_respond → send your reply
    5. phone_call_listen → wait for next response
    6. ... repeat until conversation is done ...
    7. phone_call_end → hang up

    Args:
        call_id: The call_id from phone_call_start
        timeout: Maximum seconds to wait (default 120)

    Returns:
        JSON with:
        - event: "dtmf_1" (callee pressed 1) or "timeout"
        - transcript: What the callee said (transcribed text)
    """
    try:
        result = _run_async(listen(call_id, timeout=timeout))
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def phone_call_respond(call_id: str, message: str) -> str:
    """Send a spoken response to the callee via TTS.

    The message is synthesized to speech and played to the callee.
    If the callee starts speaking during playback (barge-in), playback
    stops immediately and you should call phone_call_listen again.

    Tips for natural responses:
    - Keep messages concise (1-3 sentences)
    - Use conversational language appropriate to the context
    - If you need time to research something, say so briefly first,
      then call your tools, then phone_call_respond with the answer

    Args:
        call_id: The call_id from phone_call_start
        message: The text to speak to the callee

    Returns:
        JSON with status: "completed" (fully played) or "barged_in" (callee interrupted)
    """
    try:
        result = _run_async(respond(call_id, message))
        return json.dumps(result)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def phone_call_control(call_id: str, action: str) -> str:
    """Control the active call behavior.

    Args:
        call_id: The call_id from phone_call_start
        action: One of:
            - "status": Get current call status and bridge state
            - "nudge": Play a short filler to reassure the callee you're still there

    Returns:
        JSON with action result
    """
    call = get_active_call()
    if not call or call.call_id != call_id:
        return json.dumps({"error": f"No active call with id={call_id}"})

    if action == "status":
        bridge_state = call.audio_bridge.state if call.audio_bridge else "none"
        return json.dumps({
            "call_id": call_id,
            "status": call.status,
            "bridge_state": bridge_state,
            "phone_number": call.phone_number,
            "duration_seconds": round(time.time() - call.start_time, 1),
        })

    elif action == "nudge":
        if call.audio_bridge:
            try:
                _run_async(call.audio_bridge._play_filler("short"))
                return json.dumps({"status": "nudge_played"})
            except Exception as e:
                return json.dumps({"error": str(e)})
        return json.dumps({"error": "No audio bridge"})

    else:
        return json.dumps({"error": f"Unknown action: {action}. Use 'status' or 'nudge'."})


@mcp.tool()
def phone_call_end(call_id: str) -> str:
    """End the active phone call and get transcript + summary.

    After ending the call, you should provide the user with:
    - A brief summary of the conversation
    - The full transcript
    - Any action items or follow-ups discussed

    Args:
        call_id: The call_id from phone_call_start

    Returns:
        JSON with call summary including duration and full transcript
    """
    try:
        result = end_call(call_id)
        return json.dumps(result, ensure_ascii=False)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})


# --- Start MCP server ---

if __name__ == "__main__":
    logger.info("phonecall-mcp server starting...")
    ngrok_url = config.get("ngrok_url", "NOT SET")
    logger.info("Twilio webhook URL: %s", ngrok_url)
    mcp.run(transport="stdio")
