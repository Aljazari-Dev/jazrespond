from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

SUPPORTED_LANGUAGES = {"ar", "en", "ku"}


def normalize_language(value: str) -> str:
    value = (value or "ar").strip().lower()
    if value.startswith("ku") or value.startswith("ckb"):
        return "ku"
    if value.startswith("en"):
        return "en"
    return "ar"


def normalize_ai_mode(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "on", "enabled", "ai_on"}


def public_error(code: str, message: str) -> Dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


@dataclass
class Priority:
    PAUSE_MUTE: int = 100
    USER_TURN: int = 90
    VOICE_COMMAND: int = 80
    REMOTE_COMMAND: int = 70
    AUTOMATIC_EVENT: int = 60
    FACE_GREETING: int = 20
