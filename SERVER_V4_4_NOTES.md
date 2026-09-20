# Server V4.4

Fixes Gemini 3.1 Flash TTS Interactions API request/response handling.

- `response_format` now uses the current schema exactly: `{ "type": "audio" }`.
- Removed unsupported `mime_type` and `delivery` fields that caused HTTP 400 `Audio delivery mode is not supported.`
- Reads the documented top-level `output_audio.data` field first.
- Keeps recursive audio extraction only as a compatibility fallback.
- Live model remains `gemini-3.1-flash-live-preview`.
- Event TTS model remains `gemini-3.1-flash-tts-preview`.
