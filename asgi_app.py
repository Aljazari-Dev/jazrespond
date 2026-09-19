from __future__ import annotations

import os
from hmac import compare_digest

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.middleware.wsgi import WSGIMiddleware

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


# Keep the existing Flask dashboard and HTTP APIs unchanged while the new
# real-time robot channel moves to ASGI/WebSocket.
app.mount("/", WSGIMiddleware(flask_app))
