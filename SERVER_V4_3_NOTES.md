# Server V4.3

Fixes fixed-event Gemini TTS generation.

- Keeps normal live conversation on `gemini-3.1-flash-live-preview`.
- Moves event TTS to the current Gemini Interactions API.
- Uses `gemini-3.1-flash-tts-preview` with the configured voice (default Orus).
- Adds an explicit speech-synthesis preamble and verbatim transcript markers.
- Requests inline L16 PCM audio.
- Adds 3-attempt retry handling for transient/no-audio responses.
- Surfaces the real Gemini response when TTS returns no audio.

No robot/ROS bridge change is required for this test.
