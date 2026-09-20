from __future__ import annotations

import os
from hmac import compare_digest

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from starlette.middleware.wsgi import WSGIMiddleware
from starlette.responses import FileResponse, JSONResponse
from pathlib import Path
import json
import re

from app import app as flask_app
from server_v4.orchestrator import RobotOrchestrator
from server_v4.registry import registry
from server_v4.session import RobotSessionState

app = FastAPI(title="Al Jazari Promobot Server V4")


@app.websocket("/ws/robot/{robot_id}")
async def robot_websocket(websocket: WebSocket, robot_id: str):
    expected = os.getenv("ROBOT_WS_TOKEN", "").strip()
    provided = websocket.headers.get("x-robot-token", "").strip()
    if not expected or not provided or not compare_digest(expected, provided):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    session = RobotSessionState(robot_id=robot_id, websocket=websocket)
    previous = await registry.add(session)
    if previous is not None and previous is not session:
        try:
            await previous.websocket.close(code=4001, reason="Replaced by newer robot connection")
        except Exception:
            pass

    orchestrator = RobotOrchestrator(session)
    await orchestrator.start()
    try:
        while True:
            message = await websocket.receive()
            kind = message.get("type")
            if kind == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                await orchestrator.handle_binary(message["bytes"])
            elif message.get("text") is not None:
                import json
                try:
                    payload = json.loads(message["text"])
                except Exception:
                    await session.send_json({"type": "error", "code": "bad_json", "message": "Invalid JSON"})
                    continue
                if isinstance(payload, dict):
                    await orchestrator.handle_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        await orchestrator.close()
        await registry.remove(robot_id, session)


def _require_robot_token(token: str) -> None:
    expected = os.getenv("ROBOT_WS_TOKEN", "").strip()
    if not expected or not token or not compare_digest(expected, token.strip()):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _diag_paths(robot_id: str):
    safe_robot = re.sub(r"[^A-Za-z0-9_.-]+", "_", robot_id or "robot")
    data_dir = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent / "data"))).expanduser()
    diag_dir = data_dir / "diagnostics"
    return diag_dir / (safe_robot + "_latest.wav"), diag_dir / (safe_robot + "_latest.json")


@app.get("/api/v4/debug/audio/{robot_id}/latest.wav")
async def debug_latest_audio(robot_id: str, x_robot_token: str = Header(default="")):
    _require_robot_token(x_robot_token)
    wav_path, _ = _diag_paths(robot_id)
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="No captured audio for robot")
    return FileResponse(str(wav_path), media_type="audio/wav", filename=wav_path.name)


@app.get("/api/v4/debug/audio/{robot_id}/latest.json")
async def debug_latest_audio_meta(robot_id: str, x_robot_token: str = Header(default="")):
    _require_robot_token(x_robot_token)
    _, meta_path = _diag_paths(robot_id)
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="No captured audio metadata for robot")
    try:
        return JSONResponse(json.loads(meta_path.read_text(encoding="utf-8")))
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Invalid diagnostic metadata: %s" % exc)


# Keep the existing Flask dashboard and HTTP APIs unchanged while the new
# real-time robot channel moves to ASGI/WebSocket.
app.mount("/", WSGIMiddleware(flask_app))
