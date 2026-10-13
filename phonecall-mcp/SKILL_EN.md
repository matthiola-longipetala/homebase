---
name: phonecall-mcp
description: "Use this skill whenever the user asks you to make a phone call, call someone, ring someone, or communicate with someone by phone. Also use when the user wants to check a phone number, leave a voice message, or talk to someone via telephone. This skill teaches you how to use the phonecall-mcp tools (phone_call_start, phone_call_listen, phone_call_respond, phone_call_control, phone_call_end) to conduct real phone conversations through Twilio. The phone is just an I/O channel — you remain the brain and can use all your other tools (web search, calendar, Drive, etc.) during the call."
---

# Phone Call Skill

You can make real phone calls using Twilio. The callee hears your words as natural speech (ElevenLabs TTS), and their speech is transcribed back to you in real time (ElevenLabs Scribe STT). You think, research, and respond — the phone is just a channel.

## Prerequisites

Before ANY call, the ngrok tunnel must be running. If the user hasn't confirmed it's running, remind them to start it first.

## The Conversation Loop

Every outbound phone call follows this pattern:

```
1. phone_call_start  → call connects, GDPR notice plays
2. phone_call_listen → BLOCKS until GDPR consent (DTMF "5") or 30s timeout
   → "consent_given"   → continue to step 3
   → "consent_timeout"  → polite goodbye (3b), then end call (9)
3. phone_call_respond → confirmation message + DTMF "1" instructions
4. phone_call_listen → BLOCKS until callee presses DTMF "1"
5. (you think, search, use tools — take your time, callee hears beeping)
6. phone_call_respond → your reply plays as speech
7. phone_call_listen → wait again
8. ... repeat 4-7 ...
9. phone_call_end    → hang up, get transcript
```

This is the core loop. Never skip steps. Always listen before responding.

## GDPR Consent — MANDATORY for all outbound calls

**No transcription occurs until the callee explicitly consents.** The system enforces this at the server level — the STT engine does not start until DTMF "5" is received. This is not optional.

### What the first_message MUST include:

1. **Who you are** — Claude, calling on behalf of the user
2. **Why you're calling** — brief purpose
3. **Transcription notice** — the conversation will be transcribed for the user's records
4. **Who sees the transcript** — only the user, not shared with third parties
5. **Consent mechanism** — press "5" to agree, or hang up to decline
6. **Beeping notice** — if they hear beeping, you're working on your response

### What you say AFTER consent (via phone_call_respond):

After `phone_call_listen` returns `"consent_given"`, send a confirmation:
1. **Thank them** for consenting
2. **Explain the "1" button** — from now on, press "1" when done speaking
3. **Barge-in option** — they can also press "1" to interrupt you
4. **Transition to the actual topic** — "So, the reason I'm calling..."

### Handling consent_timeout:

If `phone_call_listen` returns `"consent_timeout"`:
1. Send a polite goodbye via `phone_call_respond`
2. Call `phone_call_end`
3. Inform the user in chat that the callee didn't consent

## Starting a Call

Before calling, ALWAYS:
1. Tell the user (in chat) who you're calling and why
2. Wait for their explicit approval
3. Only then call `phone_call_start`

### Adapting language, tone, and register

The `language` and `context` parameters determine how you communicate. Your language, register (formal/informal), and tone must match the callee and the situation — exactly as you would in a text conversation.

- **Language:** Use the language the callee speaks. Set `language` accordingly ("hu", "en", "es", etc.). Stay consistent throughout the call.
- **Register:** Friend → informal. Business contact → formal. Doctor's office → polite formal. This applies to both `first_message` and all subsequent `phone_call_respond` messages.
- **Tone:** Match the purpose. A quick check-in differs from a formal appointment booking.

### Example first_message (informal — friend):

"Hey [NAME], this is Claude, [USER]'s assistant! I'd like to quickly check in with you about something. Just so you know: this conversation will be transcribed for [USER]'s records only — it won't be shared with anyone else. If that's okay, press 5 on your phone. If not, feel free to hang up, no worries!"

### Example first_message (formal — office/appointment):

"Good day, this is Claude, calling on behalf of [USER FULL NAME]. I'd like to discuss a matter with you. Please be advised that this conversation will be transcribed solely for [USER]'s records and will not be shared with any third party. If you consent, please press 5 on your phone keypad. If you do not wish to consent, please disconnect the call."

### Example confirmation after consent (informal):

"Thanks! From now on, when you're done talking, press 1 so I know it's my turn. If you want to interrupt me, that's also the 1 button. And if you hear beeping, it means I'm working on my answer. So anyway..."

### Example confirmation after consent (formal):

"Thank you. Going forward, when you've finished speaking, please press 1 on your keypad so I know I may respond. If you'd like to interrupt me at any point, you can also press 1. And if you hear a beeping tone, that simply means I'm processing your request — please bear with me. Now, the reason for my call..."

### Parameters

- `phone_number`: E.164 format (e.g. "+14155551234")
- `language`: "en", "hu", "es", etc. — determines TTS and STT language
- `context`: Who we're calling, why, the relationship, what register to use. Use this to calibrate all your messages.
- `first_message`: The GDPR-compliant greeting (see examples above — MUST include the transcription notice + "press 5" instruction)

## Listening (phone_call_listen)

This tool BLOCKS until the callee presses a DTMF key or timeout occurs.

### First call after phone_call_start — GDPR consent phase:

The first `phone_call_listen` waits for DTMF "5" (consent) with a 30-second timeout.

Possible return values:
- `event: "consent_given"` — callee pressed "5", STT is now active. Send a confirmation via `phone_call_respond`, then call `phone_call_listen` again for normal conversation.
- `event: "consent_timeout"` — 30 seconds passed, no consent. Say goodbye via `phone_call_respond`, then `phone_call_end`.

### Normal conversation (after consent):

Waits for DTMF "1" (callee finished speaking).

Possible return values:
- `event: "dtmf_1"` — callee pressed "1", transcript contains what they said
- `event: "timeout"` — callee didn't respond within the timeout period

If the callee is silent for 20+ seconds, a DTMF reminder plays automatically.

## Responding (phone_call_respond)

Send your spoken reply. Tips:
- Keep it concise (1-3 sentences when possible)
- Match the language and register to the context
- If you need time to research, the callee hears beeping automatically — no need to announce it
- If the callee interrupts (barge-in via DTMF "1"), you'll get `status: "barged_in"` — call `phone_call_listen` again immediately

## Using Your Tools During Calls

This is your superpower. Between listen and respond, you can use ANY of your available tools:
- Web search
- Calendar queries
- Drive document lookups
- Email search
- Calculations
- Anything else you have access to

The callee hears a beeping tone while you work, so they know you're still there. Take your time.

## Ending the Call

When the conversation naturally concludes:
1. Say goodbye via `phone_call_respond`
2. Call `phone_call_end`

`phone_call_end` returns the full transcript. After hanging up:
- Summarize the conversation for the user in chat
- List any action items or follow-ups
- Show the transcript

## Call Control (phone_call_control)

Use sparingly:
- `action: "status"` — check call state and duration

## Important Rules

1. **One call at a time** — never start a new call without ending the current one
2. **Max 15 minutes** per call
3. **Always get user approval** before dialing
4. **GDPR consent is mandatory** — never skip the consent flow. The `first_message` MUST include the transcription notice and "press 5" instruction. The server enforces this at the technical level (no STT before consent), but you must provide the appropriate verbal notice.
5. **Language consistency** — use the same language throughout the call as specified in the `language` parameter
6. **Graceful endings** — always say goodbye before hanging up, don't just disconnect
7. **Error handling** — if a tool returns an error, tell the user in chat. Don't try to recover silently.
8. **Consent timeout** — if the callee doesn't press "5" within 30 seconds, say goodbye politely and end the call. Inform the user in chat.
