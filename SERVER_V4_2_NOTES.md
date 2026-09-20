# Promobot Server V4.2

## Purpose
Fix repeated Gemini Live WebSocket 1008 errors while keeping `gemini-3.1-flash-live-preview` for normal conversation.

## Changes
- Fixed robot/app event speech no longer enters the Live conversation. It uses `gemini-3.1-flash-tts-preview` and returns 24 kHz PCM to the robot.
- Added a strict synchronous tool-call gate: while Gemini is waiting for a tool response, realtime audio/activity input is dropped instead of being sent concurrently.
- Automatic Gemini VAD is the default again (`GEMINI_MANUAL_VAD=false`).
- Existing Live model remains `gemini-3.1-flash-live-preview`.
- Added explicit `requests` dependency for Gemini TTS REST calls.

## Render environment
Set:
- `GEMINI_MODEL=gemini-3.1-flash-live-preview`
- `GEMINI_MANUAL_VAD=false`
- `GEMINI_TTS_MODEL=gemini-3.1-flash-tts-preview`
- `GEMINI_TTS_VOICE_NAME=Orus` (or another supported voice)

No robot C++ changes are required for the first V4.2 server test.
