# PromobotJazBackend Render Deployment

This package is prepared for GitHub + Render. Runtime JSON files are intentionally ignored.

## Render settings

Build Command:
```bash
pip install -r requirements.txt
```

Start Command:
```bash
gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --worker-class gthread --timeout 120 app:app
```

Health Check Path:
```text
/api/health
```

Recommended persistent disk:
```text
Mount path: /var/data
Size: 1 GB
```

Environment variables:
```text
DATA_DIR=/var/data
OPENAI_API_KEY=your key
OPENAI_MODEL=gpt-4.1-mini
ELEVENLABS_API_KEY=your key
ELEVENLABS_VOICE_ID_AR=9FHjCdVXgA4tYxIYHTcZ
ELEVENLABS_MODEL_AR=eleven_flash_v2_5
FLASK_SECRET_KEY=random secret
ADMIN_USERNAME=admin
ADMIN_PASSWORD=strong password
ENABLE_ELEVENLABS_STREAMING=true
ROBOT_AI_STREAMING_ENABLED=true
ROBOT_STRICT_LANGUAGE_LOCK=true
```
