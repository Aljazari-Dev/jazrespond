# Promobot Server V4.5

- Gemini 3.1 Live now defaults to manual VAD.
- `activityStart` / `activityEnd` from the robot bridge are authoritative turn boundaries.
- Explicit `TURN_INCLUDES_ONLY_ACTIVITY` prevents silence/noise from contaminating turns.
- Explicit `START_OF_ACTIVITY_INTERRUPTS` preserves barge-in semantics.
- Sends partial input/output transcripts to the robot for diagnostics.
- Event TTS path from V4.4 is unchanged.
- Gemini Live model remains `gemini-3.1-flash-live-preview`.
