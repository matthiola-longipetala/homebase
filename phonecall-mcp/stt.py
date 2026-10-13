"""ElevenLabs Scribe v2 Realtime STT client for phonecall-mcp."""

import asyncio
import base64
import json
import re
import time

import websockets

from log_setup import logger


class RealtimeSTT:
    """WebSocket client for ElevenLabs Scribe v2 Realtime Speech-to-Text.

    Features auto-reconnect if the WebSocket drops.
    """

    BASE_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"

    def __init__(self, api_key: str, language_code: str = "hu",
                 vad_silence_threshold: float = 1.5):
        self._api_key = api_key
        self._language_code = language_code
        self._vad_silence_threshold = vad_silence_threshold

        self._ws = None
        self._session_id: str | None = None
        self._running = False
        self._connected = False

        # Transcript accumulation
        self._partial_text = ""
        self._committed_segments: list[str] = []

        # Callbacks
        self._on_committed = None

        # Receiver task
        self._recv_task: asyncio.Task | None = None
        self._session_generation: int = 0  # incremented on each connect

        # Health tracking
        self._last_audio_sent: float = 0
        self._reconnect_lock = asyncio.Lock()
        self._reconnect_attempts: int = 0

    @property
    def partial_text(self) -> str:
        return self._partial_text

    @property
    def committed_text(self) -> str:
        return " ".join(self._committed_segments)

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    def on_committed(self, callback):
        """Register callback for committed transcripts: callback(text)"""
        self._on_committed = callback

    async def connect(self):
        """Open WebSocket connection to Scribe v2 Realtime."""
        await self._do_connect()

    async def _do_connect(self):
        """Internal connect logic, used by both connect() and reconnect."""
        # Close existing connection if any
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except (asyncio.CancelledError, Exception):
                pass

        # Increment generation AFTER old task is fully stopped,
        # so old task's finally block won't clobber new state
        self._session_generation += 1

        params = (
            f"?audio_format=ulaw_8000"
            f"&language_code={self._language_code}"
            f"&commit_strategy=vad"
            f"&vad_silence_threshold_secs={self._vad_silence_threshold}"
            f"&model_id=scribe_v2_realtime"
        )
        url = self.BASE_URL + params

        logger.info("STT: connecting to Scribe...")

        try:
            self._ws = await websockets.connect(
                url,
                additional_headers={"xi-api-key": self._api_key},
                ping_interval=20,
                ping_timeout=10,
            )
            self._running = True
            self._connected = True
            self._reconnect_attempts = 0  # reset on successful connect
            gen = self._session_generation
            self._recv_task = asyncio.create_task(self._receive_loop(gen))
            logger.info("STT: connected (generation=%d)", gen)
        except Exception as e:
            self._connected = False
            logger.error("STT: connection failed: %s", e)
            raise

    async def reconnect(self):
        """Reconnect to Scribe with exponential backoff. Safe to call from any state."""
        async with self._reconnect_lock:
            if self._connected and self._ws:
                # Already connected, no need
                return

            # Exponential backoff: 1s, 2s, 4s, 8s, ... max 30s
            if self._reconnect_attempts > 0:
                delay = min(2 ** self._reconnect_attempts, 30)
                logger.warning("STT: reconnecting (attempt %d, backoff %.0fs)...",
                               self._reconnect_attempts + 1, delay)
                await asyncio.sleep(delay)
            else:
                logger.warning("STT: reconnecting...")

            self._reconnect_attempts += 1
            try:
                await self._do_connect()
            except Exception as e:
                logger.error("STT: reconnect failed (attempt %d): %s",
                             self._reconnect_attempts, e)

    async def ensure_connected(self):
        """Make sure we have a live connection. Reconnect if needed."""
        if not self._connected or not self._ws:
            await self.reconnect()

    async def _receive_loop(self, generation: int):
        """Background task that reads messages from the WebSocket."""
        try:
            async for raw_msg in self._ws:
                if not self._running:
                    break

                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("message_type", "")

                if msg_type == "session_started":
                    self._session_id = msg.get("session_id")
                    logger.info("STT: session started (id=%s)", self._session_id)

                elif msg_type == "partial_transcript":
                    text = msg.get("text", "")
                    self._partial_text = text

                elif msg_type in ("committed_transcript", "committed_transcript_with_timestamps"):
                    text = msg.get("text", "")
                    # Filter out Scribe noise descriptions in parentheses
                    text = re.sub(r'\([^)]*\)', '', text).strip()
                    if text:
                        self._committed_segments.append(text)
                        self._partial_text = ""
                        logger.info("STT: committed: '%s'", text)
                        if self._on_committed:
                            self._on_committed(text)

                elif msg_type == "error":
                    err = msg.get("error", "unknown")
                    logger.error("STT: error from server: %s", err)

        except websockets.exceptions.ConnectionClosed as e:
            logger.error("STT: WebSocket closed unexpectedly: code=%s reason=%s", e.code, e.reason)
        except Exception as e:
            logger.error("STT: receive loop error: %s (type=%s)", e, type(e).__name__)
        finally:
            # Only mark disconnected if no newer session has started
            if self._session_generation == generation:
                self._connected = False
                logger.warning("STT: receive loop ended (gen=%d), will reconnect on next feed", generation)
            else:
                logger.info("STT: old receive loop ended (gen=%d, current=%d)", generation, self._session_generation)

    async def feed_audio(self, ulaw_chunk: bytes):
        """Send a ulaw audio chunk to the STT service.

        Auto-reconnects if the connection dropped.
        """
        if not self._running:
            return

        # Auto-reconnect if connection lost
        if not self._connected or not self._ws:
            try:
                await self.reconnect()
            except Exception:
                return  # Can't connect, skip this chunk

        payload = {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(ulaw_chunk).decode("ascii"),
        }

        try:
            await self._ws.send(json.dumps(payload))
            self._last_audio_sent = time.time()
        except Exception as e:
            self._connected = False
            logger.warning("STT: send failed, marking for reconnect: %s", e)

    def clear_transcript(self):
        """Clear accumulated transcript."""
        self._committed_segments.clear()
        self._partial_text = ""

    async def reset_session(self):
        """Force a fresh Scribe session. Use between listen cycles.

        This ensures a clean VAD state and avoids stale session issues.
        """
        logger.info("STT: resetting session (fresh reconnect)")
        try:
            await self._do_connect()
        except Exception as e:
            logger.error("STT: session reset failed: %s", e)

    async def close(self):
        """Close the WebSocket connection."""
        self._running = False
        self._connected = False
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("STT: connection closed")
