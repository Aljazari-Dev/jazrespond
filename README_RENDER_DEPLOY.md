# Promobot Server V4 - Render Deployment

Server V4 keeps the existing Flask dashboard/API and mounts it inside an ASGI app so the robot can use a persistent WebSocket safely.

## Render

Build command:

```bash
pip install -r requirements.txt
```

Start command:

```bash
uvicorn asgi_app:app --host 0.0.0.0 --port $PORT --workers 1 --proxy-headers --forwarded-allow-ips='*'
```

Health check:

```text
/api/health
```

Persistent disk:

```text
/var/data
```

## Required new environment variables

```text
GEMINI_API_KEY=<secret>
GEMINI_MODEL=gemini-3.1-flash-live-preview
ROBOT_WS_TOKEN=<long random secret>
DATA_DIR=/var/data
```

Optional:

```text
GEMINI_VOICE_NAME=
GEMINI_VAD_SILENCE_MS=180
GEMINI_VAD_PREFIX_MS=80
FACE_GREETING_IDLE_GUARD_SEC=5.0
```

Keep the existing OpenAI/ElevenLabs variables during the migration because the old AI-OFF HTTP path remains available as rollback/compatibility logic in this first server-centralization build.

## Robot WebSocket

```text
wss://<render-host>/ws/robot/<robot_id>
```

Required request header:

```text
X-Robot-Token: <ROBOT_WS_TOKEN>
```

GEMINI_MANUAL_VAD=true
