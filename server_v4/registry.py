from __future__ import annotations

import asyncio
from typing import Dict, Optional

from .session import RobotSessionState


class RobotRegistry:
    def __init__(self) -> None:
        self._items: Dict[str, RobotSessionState] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: RobotSessionState) -> Optional[RobotSessionState]:
        async with self._lock:
            previous = self._items.get(session.robot_id)
            self._items[session.robot_id] = session
            return previous

    async def remove(self, robot_id: str, session: RobotSessionState) -> None:
        async with self._lock:
            if self._items.get(robot_id) is session:
                self._items.pop(robot_id, None)

    async def get(self, robot_id: str) -> Optional[RobotSessionState]:
        async with self._lock:
            return self._items.get(robot_id)


registry = RobotRegistry()
