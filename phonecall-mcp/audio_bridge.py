"""Audio bridge: connects Twilio Media Stream ↔ STT/TTS.

State machine:
  IDLE            → call not yet connected
  CONSENT_PENDING → waiting for GDPR consent (DTMF "5"), NO STT active
  LISTENING       → routing callee audio to STT, waiting for DTMF "1"
  PROCESSING      → DTMF received, Claude is thinking (hold tone plays)
  SPEAKING        → TTS audio being sent to Twilio
"""

import asyncio
import base64
import json
import math
import struct
import time

from log_setup import logger
from stt import RealtimeSTT
from tts import synthesize_ulaw


def _generate_hold_tone_ulaw(beep_freq=440, beep_ms=150, silence_ms=1850,
                              amplitude=0.15, sample_rate=8000) -> bytes:
    """Generate a single beep + silence cycle in ulaw format."""
    beep_samples = int(sample_rate * beep_ms / 1000)
    silence_samples = int(sample_rate * silence_ms / 1000)

    pcm = bytearray()
    ramp_samples = int(sample_rate * 0.01)
    for i in range(beep_samples):
        t = i / sample_rate
        env = 1.0
        if i < ramp_samples:
            env = i / ramp_samples
        elif i > beep_samples - ramp_samples:
            env = (beep_samples - i) / ramp_samples
        sample = int(32767 * amplitude * env * math.sin(2 * math.pi * beep_freq * t))
        pcm += struct.pack("<h", sample)

    pcm += b'\x00\x00' * silence_samples
    return _pcm16_to_ulaw(bytes(pcm))


def _pcm16_to_ulaw(pcm_data: bytes) -> bytes:
    """Convert 16-bit signed LE PCM to G.711 mu-law."""
    BIAS = 0x84
    CLIP = 32635
    ulaw = bytearray()
    for i in range(0, len(pcm_data), 2):
        sample = struct.unpack_from("<h", pcm_data, i)[0]
        sign = 0
        if sample < 0:
            sign = 0x80
            sample = -sample
        sample = min(sample, CLIP) + BIAS
        exponent = 7
        for exp_val in [0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100]:
            if sample >= exp_val:
                break
            exponent -= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        ulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        ulaw.append(ulaw_byte)
    return bytes(ulaw)


class AudioBridge:
    """Manages bidirectional audio between Twilio and STT/TTS.

    Design:
    - DTMF "1" is the turn-taking mechanism (callee presses 1 when done)
    - DTMF "1" during SPEAKING triggers barge-in (stops playback)
    - After DTMF "1" in LISTENING: beep until Claude responds
    - Background noise/speech goes to transcript but never interrupts playback
    """

    # DTMF reminder by language
    DTMF_REMINDERS = {
        "hu": "Ha végzett, kérem nyomja meg az egyes gombot a telefonján.",
        "en": "When you're done speaking, please press 1 on your phone.",
        "es": "Cuando termine de hablar, por favor presione 1 en su teléfono.",
    }

    DTMF_REMINDER_DELAY = 20.0  # seconds of callee silence → remind about DTMF

    def __init__(self, config: dict, tts_client, language: str = "en"):
        self._config = config
        self._tts_client = tts_client
        self._language = language

        self._state = "IDLE"
        self._ws = None
        self._stream_sid: str | None = None
        self._stt: RealtimeSTT | None = None

        # Events
        self._dtmf_event = asyncio.Event()
        self._barge_in_event = asyncio.Event()
        self._buffered_dtmf = False

        # Processing loop task
        self._processing_task: asyncio.Task | None = None

        # Pre-rendered audio cache
        self._reminder_audio: bytes | None = None

        # Transcript accumulator for the current turn
        self._turn_transcript_parts: list[str] = []

        # Track last speech activity for DTMF reminder
        self._last_speech_time: float = 0

        # Mark event for playback completion
        self._mark_event = asyncio.Event()
        self._pending_mark: str | None = None

        # Pre-generate hold tone
        self._hold_tone_audio = _generate_hold_tone_ulaw()

        # Inbound audio health tracking
        self._last_audio_received: float = 0

        # Audio throttling counter for non-LISTENING states
        self._feed_skip_counter: int = 0

        # Explicit stop signal for processing loop (avoids state-check race)
        self._stop_processing = asyncio.Event()

        # GDPR consent mechanism
        self._consent_event = asyncio.Event()
        self._consent_given = False

    @property
    def state(self) -> str:
        return self._state

    @property
    def stt(self) -> RealtimeSTT | None:
        return self._stt

    async def start(self, ws, stream_sid: str, require_consent: bool = True):
        """Initialize the bridge when Twilio Media Stream connects."""
        self._ws = ws
        self._stream_sid = stream_sid

        if require_consent:
            # GDPR: STT does NOT start until callee gives consent (DTMF "5")
            self._state = "CONSENT_PENDING"
            logger.info("AudioBridge: started, state=CONSENT_PENDING (waiting for GDPR consent)")
        else:
            # No consent needed (e.g. inbound/voicemail) — start STT immediately
            await self._start_stt()
            self._state = "LISTENING"
            logger.info("AudioBridge: started, state=LISTENING (consent not required)")

        # Pre-render DTMF reminder in background
        asyncio.create_task(self._prerender_audio())

    async def _prerender_audio(self):
        """Pre-render the DTMF reminder."""
        tts_config = self._config.get("tts", {})
        voice_id = tts_config.get("voice_id", "")
        model_id = tts_config.get("model_id", "eleven_v3")

        # DTMF reminder
        reminder_text = self.DTMF_REMINDERS.get(self._language, self.DTMF_REMINDERS["en"])
        try:
            self._reminder_audio = await asyncio.to_thread(
                synthesize_ulaw, text=reminder_text, client=self._tts_client,
                voice_id=voice_id, model_id=model_id, language_code=self._language,
            )
            logger.info("AudioBridge: DTMF reminder pre-rendered (%d bytes)", len(self._reminder_audio))
        except Exception as e:
            logger.error("AudioBridge: DTMF reminder render failed: %s", e)

    def _on_stt_committed(self, text: str):
        """Called when STT commits a transcript segment."""
        self._last_speech_time = time.time()
        if self._state == "LISTENING":
            self._turn_transcript_parts.append(text)
            logger.info("STT committed: '%s'", text)

    async def _start_stt(self):
        """Start STT engine. Called immediately (no consent needed) or after GDPR consent."""
        self._stt = RealtimeSTT(
            api_key=self._config["elevenlabs_api_key"],
            language_code=self._language,
            vad_silence_threshold=1.5,
        )
        self._stt.on_committed(self._on_stt_committed)
        await self._stt.connect()
        self._consent_given = True
        logger.info("AudioBridge: STT started (consent_given=True)")

    async def wait_for_consent(self, timeout: float = 30) -> dict:
        """Wait for callee to press DTMF '5' to give GDPR consent.

        The callee hears the first_message (which includes GDPR notice and
        instructions to press '5'). No transcription occurs until consent.

        Returns dict with event: 'consent_given' or 'consent_timeout'
        """
        if self._consent_given:
            return {"event": "consent_given"}

        try:
            await asyncio.wait_for(self._consent_event.wait(), timeout=timeout)
            return {"event": "consent_given"}
        except asyncio.TimeoutError:
            logger.info("AudioBridge: GDPR consent timeout after %.0fs", timeout)
            return {"event": "consent_timeout"}

    async def feed_twilio_audio(self, payload_b64: str):
        """Feed base64-encoded ulaw audio from Twilio to STT.

        In LISTENING state: every packet is forwarded (full transcription).
        In PROCESSING/SPEAKING: only every 50th packet (~1/sec) as keepalive,
        to avoid Scribe resource_exhausted errors from continuous audio flood.
        """
        self._last_audio_received = time.time()
        if not self._stt:
            return

        if self._state != "LISTENING":
            self._feed_skip_counter += 1
            if self._feed_skip_counter % 50 != 0:  # ~1 packet per second keepalive
                return
        else:
            self._feed_skip_counter = 0

        audio_bytes = base64.b64decode(payload_b64)
        await self._stt.feed_audio(audio_bytes)

    async def handle_dtmf(self, digit: str):
        """Handle DTMF digit from Twilio."""
        # GDPR consent: "5" before consent given → start STT + beeping
        if digit == "5" and not self._consent_given:
            logger.info("AudioBridge: DTMF '5' received → GDPR consent given (state was %s)", self._state)
            await self._start_stt()
            self._consent_event.set()
            # Start beeping immediately so callee hears feedback
            # while Claude composes the confirmation message
            self._state = "PROCESSING"
            self._stop_processing.clear()
            self._processing_task = asyncio.create_task(self._processing_audio_loop())
            return
        elif not self._consent_given:
            # Any other digit before consent → ignore but log
            logger.info("AudioBridge: DTMF '%s' ignored (waiting for '5' consent, state=%s)", digit, self._state)
            return
        elif digit == "1" and self._state == "LISTENING":
            logger.info("AudioBridge: DTMF '1' received → PROCESSING")
            self._state = "PROCESSING"
            self._dtmf_event.set()
            # Start beeping immediately (no wait message)
            self._stop_processing.clear()
            self._processing_task = asyncio.create_task(self._processing_audio_loop())
        elif digit == "1" and self._state == "SPEAKING":
            logger.info("AudioBridge: DTMF '1' during SPEAKING → barge-in")
            self._barge_in_event.set()
        elif digit == "1" and self._state == "PROCESSING":
            logger.info("AudioBridge: DTMF '1' received during PROCESSING → buffered")
            self._buffered_dtmf = True
        elif digit == "1":
            logger.debug("AudioBridge: DTMF '1' ignored (state=%s)", self._state)

    async def wait_for_turn(self, timeout: float = 300) -> dict:
        """Wait for callee to press DTMF "1" (or timeout).

        Returns dict with transcript and event info.
        NOTE: We do NOT clear transcript or DTMF here — the callee may already
        be speaking or have pressed DTMF between speak() returning and this
        call. speak() prepares the next turn at its end.
        """
        # Wait for any ongoing speak() to finish (e.g. first_message task)
        while self._state == "SPEAKING":
            await asyncio.sleep(0.1)

        self._state = "LISTENING"
        self._last_speech_time = time.time()

        # Check for DTMF buffered during PROCESSING state
        if self._buffered_dtmf:
            self._buffered_dtmf = False
            logger.info("AudioBridge: returning buffered DTMF from PROCESSING")
            return {"event": "dtmf_1", "transcript": ""}

        # Start DTMF reminder task
        reminder_task = asyncio.create_task(self._dtmf_reminder_loop())

        try:
            await asyncio.wait_for(self._dtmf_event.wait(), timeout=timeout)
            event = "dtmf_1"
        except asyncio.TimeoutError:
            event = "timeout"
            self._state = "PROCESSING"

        reminder_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass

        # Collect transcript — committed segments + any uncommitted partial text.
        # The callee often presses DTMF faster than the 1.5s VAD silence threshold,
        # so the last chunk of speech may still be in partial_text, not committed yet.
        transcript = " ".join(self._turn_transcript_parts).strip()
        if self._stt and self._stt.partial_text:
            partial = self._stt.partial_text.strip()
            if partial:
                transcript = (transcript + " " + partial).strip()

        # Log STT health info
        stt_connected = self._stt.is_connected if self._stt else False
        logger.info("AudioBridge: wait_for_turn result: event=%s, transcript_len=%d, stt_connected=%s",
                     event, len(transcript), stt_connected)

        return {
            "event": event,
            "transcript": transcript,
        }

    async def speak(self, text: str, prerendered_audio: bytes | None = None) -> dict:
        """Synthesize text and send to Twilio stream.

        DTMF "1" during playback triggers barge-in (stops audio immediately).
        Beeping continues during TTS synthesis to avoid silence gaps.
        """
        # Do NOT set state to SPEAKING yet — _processing_audio_loop checks
        # "while self._state == PROCESSING" and would exit immediately.
        self._barge_in_event.clear()

        # Synthesize TTS FIRST — state stays PROCESSING so beeping continues
        if prerendered_audio:
            audio_bytes = prerendered_audio
            logger.info("AudioBridge: using pre-rendered audio (%d bytes)", len(audio_bytes))
        else:
            tts_config = self._config.get("tts", {})
            try:
                audio_bytes = await asyncio.to_thread(
                    synthesize_ulaw,
                    text=text,
                    client=self._tts_client,
                    voice_id=tts_config.get("voice_id", ""),
                    model_id=tts_config.get("model_id", "eleven_v3"),
                    language_code=self._language,
                )
            except Exception as e:
                logger.error("AudioBridge: TTS error: %s", e)
                self._state = "LISTENING"
                return {"status": "error", "error": str(e)}

        # TTS ready — NOW stop beeping and clear Twilio buffer
        self._stop_processing.set()
        if self._processing_task:
            self._processing_task.cancel()
            try:
                await self._processing_task
            except asyncio.CancelledError:
                pass
            self._processing_task = None
        await self._send_twilio_clear()

        self._state = "SPEAKING"

        # Send TTS audio (checks for DTMF barge-in between batches)
        status = await self._send_audio(audio_bytes, check_barge_in=True)

        # Wait for Twilio to finish playing (unless barged in)
        if status == "completed":
            await self._wait_for_playback_done(audio_bytes)

        # Prepare for the next listening turn BEFORE setting state to LISTENING.
        # This way, anything the callee says/presses after this point is captured,
        # and wait_for_turn() doesn't need to clear (avoiding the race condition).
        self._turn_transcript_parts.clear()
        self._dtmf_event.clear()
        if self._stt:
            self._stt.clear_transcript()

        self._state = "LISTENING"

        return {"status": status}

    async def _send_audio(self, audio_bytes: bytes, check_barge_in: bool = False) -> str:
        """Send ulaw audio to Twilio.

        If check_barge_in is True, checks for DTMF barge-in between batches
        and stops immediately if triggered.
        """
        CHUNK_SIZE = 160  # 20ms at 8kHz
        BATCH_SIZE = 10   # 200ms batches = 200ms of audio

        chunks = [audio_bytes[i:i + CHUNK_SIZE]
                  for i in range(0, len(audio_bytes), CHUNK_SIZE)]

        for batch_start in range(0, len(chunks), BATCH_SIZE):
            # Check for barge-in before each batch
            if check_barge_in and self._barge_in_event.is_set():
                logger.info("AudioBridge: barge-in detected, stopping audio")
                await self._send_twilio_clear()
                return "barged_in"

            batch = chunks[batch_start:batch_start + BATCH_SIZE]
            for chunk in batch:
                payload = base64.b64encode(chunk).decode("ascii")
                message = {
                    "event": "media",
                    "streamSid": self._stream_sid,
                    "media": {"payload": payload},
                }
                try:
                    await self._ws.send_str(json.dumps(message))
                except Exception as e:
                    logger.error("AudioBridge: send error: %s (type=%s)", e, type(e).__name__)
                    return "error"
            # Throttle to ~realtime: 200ms audio per batch, sleep 200ms
            # This prevents flooding Twilio's buffer which causes audio drops
            await asyncio.sleep(0.20)

        return "completed"

    async def _wait_for_playback_done(self, audio_bytes: bytes):
        """Wait for Twilio to finish playing via mark event."""
        self._mark_event.clear()
        mark_name = f"end_{id(audio_bytes)}"
        self._pending_mark = mark_name
        mark_msg = {
            "event": "mark",
            "streamSid": self._stream_sid,
            "mark": {"name": mark_name},
        }
        try:
            await self._ws.send_str(json.dumps(mark_msg))
        except Exception:
            pass

        playback_duration = len(audio_bytes) / 8000
        timeout = max(playback_duration + 2, 5)

        try:
            await asyncio.wait_for(self._mark_event.wait(), timeout=timeout)
            logger.debug("AudioBridge: playback confirmed via mark")
        except asyncio.TimeoutError:
            logger.warning("AudioBridge: mark timeout (%.1fs), assuming playback done", timeout)

        self._pending_mark = None

    def handle_mark(self, mark_name: str):
        """Called when Twilio sends a mark event."""
        if self._pending_mark and mark_name == self._pending_mark:
            self._mark_event.set()

    async def _send_twilio_clear(self):
        """Send clear message to Twilio to stop any queued audio."""
        if not self._ws or not self._stream_sid:
            return
        try:
            await self._ws.send_str(json.dumps({
                "event": "clear",
                "streamSid": self._stream_sid,
            }))
        except Exception:
            pass

    async def _processing_audio_loop(self):
        """After DTMF "1": beep continuously until speak() signals stop.

        Uses _stop_processing Event instead of state check to avoid race
        conditions where wait_for_turn() sets state=LISTENING before speak()
        gets to cancel this loop.
        """
        MAX_CONSECUTIVE_ERRORS = 5

        try:
            beep_duration = len(self._hold_tone_audio) / 8000  # ~2s
            consecutive_errors = 0

            while not self._stop_processing.is_set():
                # Check WebSocket health
                if self._ws is None or self._ws.closed:
                    logger.error("AudioBridge: WebSocket closed, stopping processing loop")
                    break

                result = await self._send_audio(self._hold_tone_audio)
                if result == "error":
                    consecutive_errors += 1
                    logger.warning("AudioBridge: hold tone send failed (%d/%d)",
                                   consecutive_errors, MAX_CONSECUTIVE_ERRORS)
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        logger.error("AudioBridge: too many consecutive errors, stopping processing loop")
                        break
                    await asyncio.sleep(1.0)
                    continue
                consecutive_errors = 0
                # Wait for Twilio to actually play the audio before sending next
                await asyncio.sleep(beep_duration)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("AudioBridge: processing audio loop error: %s", e)

        # Log why the loop ended
        ws_status = "closed" if (self._ws is None or self._ws.closed) else "open"
        logger.info("AudioBridge: processing loop ended (state=%s, ws=%s)", self._state, ws_status)

    async def _dtmf_reminder_loop(self):
        """Remind callee about DTMF if they've been silent too long."""
        try:
            while self._state == "LISTENING":
                await asyncio.sleep(5)
                if (self._state == "LISTENING" and
                        time.time() - self._last_speech_time > self.DTMF_REMINDER_DELAY):
                    if self._reminder_audio:
                        await self._send_audio(self._reminder_audio)
                    self._last_speech_time = time.time()
        except asyncio.CancelledError:
            pass

    async def stop(self):
        """Shut down the bridge."""
        self._state = "IDLE"
        if self._processing_task:
            self._processing_task.cancel()
        if self._stt:
            await self._stt.close()
        logger.info("AudioBridge: stopped")
