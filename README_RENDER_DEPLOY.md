# Promobot Direct Gemini Render Deployment

This package restores the Flask backend used by the proven direct Gemini ROS node. Gemini audio does **not** pass through Render.

## Render settings

Build Command:
```bash
pip install -r requirements.txt
```

Start Command:
```bash
gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --worker-class gthread --timeout 120 --keep-alive 15 app:app
```

Health Check Path: `/api/health`

Persistent disk: `/var/data` (1 GB is sufficient for current JSON/runtime data).

Required/important environment:
```text
DATA_DIR=/var/data
IMPORT_SEED_JSON=false
SEED_JSON_OVERWRITE=false
ROBOT_CONFIG_TOKEN=<same value used by PROMOBOT_PROMPT_TOKEN on robot>
FLASK_SECRET_KEY=<secret>
ADMIN_USERNAME=<dashboard user>
ADMIN_PASSWORD=<dashboard password>
```

Keep existing OpenAI/ElevenLabs variables only if AI-OFF `/api/chat` still uses those providers.

Remove experimental bridge-only variables after rollback: `ROBOT_WS_TOKEN`, `AUDIO_DIAGNOSTICS`, `GEMINI_MANUAL_VAD`, `GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE_NAME`.
