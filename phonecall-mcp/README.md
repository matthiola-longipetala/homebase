# phonecall-mcp

An MCP server that enables Claude to make and manage real phone calls through Twilio. Claude becomes the brain behind the conversation — thinking, searching, and responding — while the phone is just an I/O channel.

**What makes this different?** Most phone AI solutions put a pre-prompted, standalone agent on the line. Here, your full Claude instance — with all its tools (web search, calendar, Drive, email, etc.) — handles the call. It's not a script-following bot, but a reflective assistant that adapts to the conversation in real time.

## How It Works

```
Claude Desktop (MCP client)
    ↕ MCP protocol (stdio)
phonecall-mcp server (Python, local)
    ↕ WebSocket (Twilio Media Streams)
Twilio (PSTN telephone network)
    ↕
Callee's phone
```

Parallel services:
```
phonecall-mcp server
    → ElevenLabs Scribe v2 Realtime (STT: speech → text)
    → ElevenLabs TTS (text → speech)
```

The entire audio pipeline runs natively in μ-law 8kHz — no format conversion needed.

## Features

- **Outbound calls** — Claude initiates calls on your behalf, with natural TTS voice
- **Real-time transcription** — ElevenLabs Scribe v2 transcribes the callee's speech live
- **GDPR consent** — Built-in consent mechanism: no transcription occurs until the callee explicitly agrees (DTMF "5"). Enforced at the server level.
- **DTMF turn-taking** — Callee presses "1" when done speaking, press "1" to interrupt (barge-in)
- **Tool use during calls** — Claude can search the web, check calendars, look up documents — all while the callee hears a hold tone
- **Inbound voicemail** — Incoming calls are handled as a voicemail recorder
- **Multi-language** — Works in any language supported by ElevenLabs (tested: Hungarian, English, German)
- **Full transcript** — Every call produces a timestamped transcript with speaker labels

## Prerequisites

- **Python 3.12+**
- **Twilio account** with a phone number ([console.twilio.com](https://console.twilio.com))
- **ElevenLabs account** with API access ([elevenlabs.io](https://elevenlabs.io))
- **ngrok account** with a static domain ([ngrok.com](https://ngrok.com)) — free tier works
- **Claude Desktop** app

## Setup

### 1. Clone the repository

This MCP is a folder of the package. Clone (or download) the whole repository, then work in this folder:
```bash
git clone <this repository's URL>
cd <repository>/phonecall-mcp
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

On Windows, the `tzdata` package (included in requirements.txt) is required for timezone support.

### 3. Download ngrok

Download the ngrok binary for your platform from [ngrok.com/download](https://ngrok.com/download) and place `ngrok.exe` (or `ngrok`) in the project directory. Authenticate it with your ngrok auth token:

```bash
ngrok config add-authtoken YOUR_AUTH_TOKEN
```

If you have a static ngrok domain (recommended), note it for the next step.

### 4. Configure environment variables

Copy the example files:

```bash
cp .env.example .env
cp config.example.json config.json
```

Edit `.env` with your credentials:

```env
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+1234567890

ELEVENLABS_API_KEY=your_elevenlabs_api_key

NGROK_URL=https://your-subdomain.ngrok-free.app
```

### 5. Configure config.json

Edit `config.json` to set your preferences:

```json
{
  "timezone": "UTC",
  "tts": {
    "voice_id": "YOUR_VOICE_ID_HERE",
    "model_id": "eleven_v3",
    "language_code": "en"
  },
  "stt": {
    "model_id": "scribe_v2_realtime",
    "language_code": "en",
    "vad_silence_threshold": 1.5
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8765
  },
  "call": {
    "max_duration_seconds": 900
  },
  "voicemail": {
    "default_language": "en",
    "local_prefixes": [],
    "greeting": {
      "en": "Hello! You've reached the voicemail. Please leave your message after the tone. When you're done, press 1 on your phone. Thank you!"
    },
    "thanks": {
      "en": "Thank you for your message. Goodbye!"
    }
  }
}
```

**Key settings to customize:**

| Setting | Description |
|---|---|
| `timezone` | Your local timezone (e.g. `"America/New_York"`, `"Europe/London"`) |
| `tts.voice_id` | Your ElevenLabs voice ID (find it in your ElevenLabs dashboard) |
| `tts.language_code` | Default TTS language code |
| `stt.language_code` | Default STT language code |
| `voicemail.default_language` | Language for voicemail greetings |
| `voicemail.local_prefixes` | Phone prefixes for your country (e.g. `["+1"]` for US, `["+44"]` for UK). Calls from these prefixes use `default_language`; others get English. |
| `voicemail.greeting` | Add greetings in any language you need |
| `voicemail.thanks` | Add thank-you messages in matching languages |

### 6. Configure Claude Desktop

Add the MCP server to your Claude Desktop configuration. Open Claude Desktop's settings and add a new MCP server, or edit the config file directly:

**Config file location:**
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the `phonecall-mcp` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "phonecall-mcp": {
      "command": "python",
      "args": [
        "/absolute/path/to/phonecall-mcp/server.py"
      ]
    }
  }
}
```

Replace `/absolute/path/to/phonecall-mcp/server.py` with the actual path on your system.

### 7. Start ngrok

Before making calls, start the ngrok tunnel. You can use the included launcher (Windows):

```
Double-click ngrok_launcher.pyw
```

Or start it manually:

```bash
ngrok http 8765 --domain=your-subdomain.ngrok-free.app
```

### 8. Restart Claude Desktop

Restart Claude Desktop so it picks up the new MCP server configuration. You should see `phonecall-mcp` in the available tools.

## Usage

### Making a call

Simply ask Claude to call someone:

> "Call +1234567890 and ask about their business hours."

Claude will:
1. Tell you who it's calling and why, and wait for your approval
2. Start the call with a GDPR-compliant greeting
3. Wait for the callee's consent (DTMF "5")
4. Conduct the conversation
5. Provide you with a transcript and summary after hanging up

### The call flow

```
1. Claude plays GDPR notice + "press 5 to consent"
2. Callee presses 5 → transcription starts, beeping confirms
3. Claude sends confirmation + explains "press 1 when done"
4. Callee speaks → presses 1 → Claude receives transcript
5. Claude thinks (callee hears beeping) → responds via TTS
6. Repeat 4-5 until conversation ends
7. Claude hangs up → provides transcript + summary
```

### GDPR consent mechanism

Every outbound call includes a mandatory consent step:

- The callee hears who is calling, why, and that the conversation will be transcribed
- The callee must press **5** on their phone to consent
- **No transcription occurs before consent** — this is enforced at the server level (STT engine does not start until DTMF "5" is received)
- If the callee doesn't press 5 within 30 seconds, Claude politely ends the call
- If the callee hangs up, nothing was recorded

### Inbound calls (voicemail)

When someone calls your Twilio number, the server automatically handles it as a voicemail:

1. Plays a greeting in the configured language
2. Records the caller's message
3. Logs the transcript

You can ask Claude: *"Did I get any calls?"* and it will check the call log.

### Using the skill file

The `SKILL_EN.md` file teaches Claude how to use phonecall-mcp effectively — including GDPR notice templates, conversation flow, and best practices. To use it:

1. Copy `SKILL_EN.md` to your Claude Desktop skills directory
2. Customize the templates with your name and preferences
3. Claude will automatically follow the skill guidelines when making calls

## MCP Tools

| Tool | Description |
|---|---|
| `phone_call_start` | Initiate an outbound call |
| `phone_call_listen` | Wait for callee to speak + press DTMF "1" |
| `phone_call_respond` | Send a spoken response via TTS |
| `phone_call_control` | Check call status |
| `phone_call_end` | Hang up and get transcript |

## Architecture

### State machine (AudioBridge)

```
IDLE → CONSENT_PENDING → LISTENING ⇄ PROCESSING → SPEAKING → LISTENING
                              ↑                                    |
                              └────────────────────────────────────┘
```

- **IDLE** — Call not yet connected
- **CONSENT_PENDING** — Waiting for GDPR consent (DTMF "5"), no STT active
- **LISTENING** — Routing callee audio to STT, waiting for DTMF "1"
- **PROCESSING** — DTMF received, Claude is thinking (hold tone plays)
- **SPEAKING** — TTS audio being sent to Twilio

### Key design decisions

- **DTMF-based turn-taking** instead of silence detection — more reliable on phone lines with background noise
- **Server-enforced GDPR consent** — STT physically cannot start before consent, regardless of what Claude sends
- **Pre-rendered audio** — First message TTS is synthesized while the phone rings, eliminating the initial silence gap
- **Scribe audio throttling** — During PROCESSING state, only keepalive packets are sent to STT to prevent `resource_exhausted` errors
- **Barge-in support** — Callee can interrupt Claude by pressing "1" during TTS playback

## Security Considerations

### Caller data extraction risk

⚠️ **Important:** During a phone call, Claude has access to all its connected tools — web search, Google Drive, email, calendar, and any other MCP connectors you have enabled. This means the **callee could potentially extract sensitive information** from Claude through social engineering (e.g., "Can you check if there's a document about..." or "What's on the calendar for...").

**Recommended mitigations:**

- **Disable sensitive connectors** during calls — temporarily turn off MCP connectors that access private data (Drive, email, calendar) if the call doesn't require them
- **Define boundaries in your skill file** — explicitly instruct Claude what it should NOT do during calls (e.g., "Never read emails or share calendar details during a call", "Only use web search, no internal tools")
- **Review transcripts** — check call transcripts for any unexpected information disclosure

The tool use capability during calls is powerful — Claude can look up information in real time to help the conversation. But that same power means you should carefully consider which tools Claude should have access to for each call.

### API keys and credentials

- All secrets are stored in `.env` (excluded from git via `.gitignore`)
- Never commit `.env` or `config.json` to version control
- The Twilio auth token is used for request validation — keep it secure
- Request validation is **enforced** (fail-closed): `/voice`, `/status` and the `/media-stream` WebSocket upgrade are rejected with 403 unless the `X-Twilio-Signature` header verifies against your auth token; without a configured auth token nothing is accepted. For local debugging only, `PHONECALL_SKIP_TWILIO_VALIDATION=1` disables the check (logged loudly).

### GDPR consent

The built-in consent mechanism ensures no transcription occurs without the callee's explicit agreement. However, this is a technical safeguard — you are responsible for ensuring your use of this tool complies with applicable privacy laws in your jurisdiction.

## Troubleshooting

### Call fails immediately
- Check that ngrok is running and the URL in `.env` matches
- Verify your Twilio credentials and phone number
- Check `logs/phonecall-mcp.log` for error details

### No audio / silence after connecting
- Verify your ElevenLabs API key and voice ID
- Check that the voice ID exists in your ElevenLabs account
- Look for TTS errors in the log file

### Callee can't hear the greeting
- Ensure ngrok tunnel is active (the TwiML webhook URL must be reachable)
- Check Twilio console for call logs and error codes

### Empty transcripts
- Check for `resource_exhausted` errors in the log — this means Scribe is being overloaded
- Verify `stt.language_code` matches the language being spoken

### Timezone shows UTC
- Install `tzdata`: `pip install tzdata`
- Set `timezone` in `config.json` to your timezone (e.g. `"America/New_York"`)

## File Structure

```
phonecall-mcp/
├── server.py              # MCP server entry point
├── audio_bridge.py        # Bidirectional audio management + state machine
├── call_manager.py        # Call lifecycle (start, listen, respond, end)
├── twilio_handler.py      # Twilio webhooks + WebSocket handler
├── stt.py                 # ElevenLabs Scribe v2 Realtime STT client
├── tts.py                 # ElevenLabs TTS synthesis
├── config.py              # Configuration loader
├── log_setup.py           # Centralized logging
├── models.py              # Data models (CallState, TranscriptEntry)
├── ngrok_launcher.pyw     # Windows ngrok launcher with notifications
├── ngrok_stop.pyw         # Windows ngrok stop utility
├── config.example.json    # Configuration template
├── .env.example           # Environment variables template
├── .gitignore
├── requirements.txt
└── SKILL_EN.md            # Claude skill file for phonecall-mcp
```

## Requirements

- Python 3.12+
- Twilio account (trial accounts work but play a short message before connecting)
- ElevenLabs account with API access
- ngrok (free tier with static domain)
- Claude Desktop

## License

MIT License — see [LICENSE](LICENSE) for details.

## Acknowledgments

Built with [Twilio Media Streams](https://www.twilio.com/docs/voice/media-streams), [ElevenLabs](https://elevenlabs.io/) (TTS + Scribe STT), and the [Model Context Protocol](https://modelcontextprotocol.io/).
