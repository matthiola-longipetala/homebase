"""Data models for phonecall-mcp."""

from dataclasses import dataclass, field
import time
import uuid


@dataclass
class TranscriptEntry:
    timestamp: float
    speaker: str  # "claude", "callee", or "system"
    text: str


@dataclass
class CallState:
    phone_number: str
    language: str
    context: str
    first_message: str = ""
    direction: str = "outbound"  # "outbound" or "inbound"
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    twilio_call_sid: str | None = None
    stream_sid: str | None = None
    status: str = "initiating"  # initiating, ringing, connected, completed, failed
    start_time: float = field(default_factory=time.time)
    transcript: list[TranscriptEntry] = field(default_factory=list)
    audio_bridge: object = field(default=None, repr=False)  # AudioBridge instance
    _prerendered_audio: bytes | None = field(default=None, repr=False)
