from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from fastapi import WebSocket


@dataclass
class RobotSessionState:
    robot_id: str
    websocket: WebSocket
    app_active: bool = False
    ai_enabled: bool = False
    language: str = "ar"
    muted: bool = False
    paused: bool = False
    user_speaking: bool = False
    assistant_speaking: bool = False
    action_running: bool = False
    last_interaction_at: float = 0.0
    connected_at: float = field(default_factory=time.monotonic)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    gemini: Optional[object] = None
    remote_task: Optional[asyncio.Task] = None
    face_last: Dict[str, float] = field(default_factory=dict)

    def touch(self) -> None:
        self.last_interaction_at = time.monotonic()

    def recent_interaction(self, seconds: float = 5.0) -> bool:
        if self.last_interaction_at <= 0:
            return False
        return (time.monotonic() - self.last_interaction_at) < seconds

    def face_greeting_blocked(self, idle_guard_sec: float = 5.0) -> bool:
        return bool(
            self.muted
            or self.paused
            or self.user_speaking
            or self.assistant_speaking
            or self.action_running
            or self.recent_interaction(idle_guard_sec)
        )

    async def send_json(self, payload: dict) -> None:
        async with self.send_lock:
            await self.websocket.send_json(payload)

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm or self.muted or self.paused:
            return
        async with self.send_lock:
            await self.websocket.send_bytes(pcm)
