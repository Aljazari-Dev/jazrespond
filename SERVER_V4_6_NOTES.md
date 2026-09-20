# Server V4.6 - PCM diagnostics

No change to Gemini routing or robot control behavior.

Adds optional server-side capture of exactly the PCM bytes received from the robot during each manual VAD speech turn.

Render env:

```text
AUDIO_DIAGNOSTICS=true
```

Latest turn is overwritten per robot under `DATA_DIR/diagnostics`, so disk usage stays bounded.

Protected download endpoints:

- `/api/v4/debug/audio/<robot_id>/latest.wav`
- `/api/v4/debug/audio/<robot_id>/latest.json`

Both require `X-Robot-Token`.
