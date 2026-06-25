import json
import os
import uuid
import time
import threading
import re
import asyncio
import base64
import queue
from functools import wraps
from hmac import compare_digest
from pathlib import Path

try:
    import websockets
except Exception:
    websockets = None

from flask import Flask, request, jsonify, render_template, redirect, url_for, Response
from openai import OpenAI
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
COMMANDS_PATH = DATA_DIR / "commands.json"
FACE_GREETINGS_PATH = DATA_DIR / "face_greetings.json"
SERVER_LOG_PATH = DATA_DIR / "server_runtime.log"
ACTIVE_CONFIG_PATH = DATA_DIR / "active_config.json"
ACTIVE_COMMANDS_PATH = DATA_DIR / "active_commands.json"
ACTIVE_FACE_GREETINGS_PATH = DATA_DIR / "active_face_greetings.json"

# Optional GitHub-to-Render seed import.
# Put JSON files in repo folder: seed_data/
# On Render, set:
#   IMPORT_SEED_JSON=true
#   SEED_JSON_OVERWRITE=true
# The files will be copied into DATA_DIR, usually /var/data.
SEED_DATA_DIR = BASE_DIR / "seed_data"


def import_seed_json_if_requested():
    """Import command/face JSON files from seed_data into DATA_DIR.

    This is useful when Render Web Shell paste is not working.
    It intentionally ignores config.json and active_config.json so API keys
    stay in Render Environment Variables, not GitHub.
    """
    import_enabled = os.getenv("IMPORT_SEED_JSON", "false").strip().lower() in {"1", "true", "yes", "on"}
    overwrite = os.getenv("SEED_JSON_OVERWRITE", "false").strip().lower() in {"1", "true", "yes", "on"}

    if not import_enabled:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    allowed_files = [
        "commands.json",
        "face_greetings.json",
        "active_commands.json",
        "active_face_greetings.json",
    ]

    for filename in allowed_files:
        src = SEED_DATA_DIR / filename
        dst = DATA_DIR / filename

        if not src.exists():
            print(f"[seed] skipped missing file: {src}", flush=True)
            continue

        if dst.exists() and not overwrite:
            print(f"[seed] skipped existing file: {dst}", flush=True)
            continue

        try:
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)

            with open(dst, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"[seed] imported {filename} to {dst}", flush=True)
        except Exception as e:
            print(f"[seed] failed to import {filename}: {e}", flush=True)


import_seed_json_if_requested()

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.secret_key = os.getenv("FLASK_SECRET_KEY", "promobot-local-dev-key-change-me")

DETAIL_JOBS = {}
DETAIL_JOBS_LOCK = threading.Lock()
REMOTE_COMMAND_QUEUE = []
REMOTE_COMMAND_QUEUE_LOCK = threading.Lock()
AUDIO_STORE = {}
AUDIO_STORE_LOCK = threading.Lock()
LIVE_AUDIO_JOBS = {}
LIVE_AUDIO_JOBS_LOCK = threading.Lock()
LOG_EVENTS = []
LOG_LOCK = threading.Lock()

DEFAULT_COMMANDS = [
    {
        "id": 1,
        "enabled": True,
        "name": "Handshake Arabic",
        "trigger_mode": "voice_remote",
        "language": "ar",
        "phrases": ["صافحني", "مصافحة", "سلم علي", "سلملي"],
        "match_type": "contains",
        "priority": 100,
        "action_type": "ros_script",
        "action_value": "get_hand_boy",
        "reply_text": "أكيد، تفضل.",
        "notes": ""
    },
    {
        "id": 2,
        "enabled": True,
        "name": "Handshake English",
        "trigger_mode": "voice_remote",
        "language": "en",
        "phrases": ["shake hand", "handshake", "shake my hand"],
        "match_type": "contains",
        "priority": 90,
        "action_type": "ros_script",
        "action_value": "get_hand_boy",
        "reply_text": "Sure.",
        "notes": ""
    },
    {
        "id": 3,
        "enabled": True,
        "name": "Take Photo Arabic",
        "trigger_mode": "voice_remote",
        "language": "ar",
        "phrases": ["صورني", "صوره", "صورة", "خذ صورة", "خذ صوره", "افتح الكاميرا"],
        "match_type": "contains",
        "priority": 95,
        "action_type": "start_app",
        "action_value": "promobot_example_app_camerae999999",
        "reply_text": "تمام، راح أفتح الكاميرا.",
        "notes": ""
    },
    {
        "id": 4,
        "enabled": True,
        "name": "Dance Arabic",
        "trigger_mode": "voice_remote",
        "language": "ar",
        "phrases": ["ارقص", "رقصة", "رقصه"],
        "match_type": "contains",
        "priority": 80,
        "action_type": "ros_script",
        "action_value": "dance1",
        "reply_text": "أكيد.",
        "notes": ""
    }
]


DEFAULT_FACE_GREETINGS = [
    {
        "id": 1,
        "enabled": True,
        "recognition_type": "known",
        "person_name": "KARIM",
        "match_type": "exact",
        "language": "all",
        "response_text": "أهلاً كريم، نورتنا.",
        "action_type": "ros_script",
        "action_value": "hello",
        "cooldown_sec": 60,
        "priority": 100,
        "notes": ""
    },
    {
        "id": 2,
        "enabled": True,
        "recognition_type": "unknown",
        "person_name": "",
        "match_type": "exact",
        "language": "all",
        "response_text": "أهلاً وسهلاً، نورتنا.",
        "action_type": "ros_script",
        "action_value": "hello",
        "cooldown_sec": 60,
        "priority": 50,
        "notes": ""
    }
]

DEFAULT_CONFIG = {
    "openai_api_key": "",
    "model": "gpt-4.1-mini",
    "system_prompt": (
        "You are a helpful robot assistant for Al Jazari Robotics and AI. "
        "Answer in the same language as the user question. "
        "Use natural spoken language suitable for a robot. "
        "Do not use markdown, bullet points, emojis, URLs, or symbols. "
        "When writing Arabic, write numbers as words, not digits. "
        "Keep the style clear, friendly, and professional."
    ),
    "quick_answer_prompt": (
        "Create PART ONE only. This is the first spoken response from a robot. "
        "Answer quickly in one or two short sentences. "
        "Do not include all details. Do not say you are preparing more details. "
        "If Arabic, use natural Iraqi Arabic when suitable. "
        "Do not use markdown, bullets, emojis, URLs, or symbols. "
        "Write numbers as words, not digits."
    ),
    "detail_answer_prompt": (
        "Create the detailed continuation after the first quick answer. "
        "Do not repeat the first answer. Add useful details only. "
        "Make the continuation suitable for robot speech. "
        "Use two short paragraphs maximum. "
        "Do not use markdown, bullets, emojis, URLs, or symbols. "
        "Write numbers as words, not digits."
    ),
    "elevenlabs_api_key": "",
    "elevenlabs_voice_id_ar": "9FHjCdVXgA4tYxIYHTcZ",
    "elevenlabs_model_ar": "eleven_flash_v2_5",
    "use_elevenlabs_for_arabic": True,
    "quick_max_output_tokens": 90,
    "detail_max_output_tokens": 260,
    "tts_chunk_max_chars": 230,
    "max_detail_parts": 2,
    "audio_wait_timeout_sec": 45,
    "enable_elevenlabs_streaming": True,
    "elevenlabs_stream_output_format": "mp3_22050_32",
    "elevenlabs_stream_latency": 3,
    "live_audio_prebuffer_bytes": 1024,
    "live_audio_prebuffer_timeout_ms": 300,
    "robot_ai_streaming_enabled": True,
    "robot_strict_language_lock": True
}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        return int(value)
    except Exception:
        return int(default)


def apply_env_overrides(config: dict) -> dict:
    """Environment variables override JSON values on Render.
    This keeps secrets out of Git and lets the dashboard JSON stay ignored.
    """
    config = DEFAULT_CONFIG.copy() | dict(config or {})
    string_envs = {
        "OPENAI_API_KEY": "openai_api_key",
        "OPENAI_MODEL": "model",
        "ELEVENLABS_API_KEY": "elevenlabs_api_key",
        "ELEVENLABS_VOICE_ID_AR": "elevenlabs_voice_id_ar",
        "ELEVENLABS_MODEL_AR": "elevenlabs_model_ar",
        "ELEVENLABS_STREAM_OUTPUT_FORMAT": "elevenlabs_stream_output_format",
    }
    for env_name, field in string_envs.items():
        value = os.getenv(env_name)
        if value is not None and str(value).strip() != "":
            config[field] = str(value).strip()

    int_envs = {
        "QUICK_MAX_OUTPUT_TOKENS": "quick_max_output_tokens",
        "DETAIL_MAX_OUTPUT_TOKENS": "detail_max_output_tokens",
        "TTS_CHUNK_MAX_CHARS": "tts_chunk_max_chars",
        "MAX_DETAIL_PARTS": "max_detail_parts",
        "AUDIO_WAIT_TIMEOUT_SEC": "audio_wait_timeout_sec",
        "ELEVENLABS_STREAM_LATENCY": "elevenlabs_stream_latency",
        "LIVE_AUDIO_PREBUFFER_BYTES": "live_audio_prebuffer_bytes",
        "LIVE_AUDIO_PREBUFFER_TIMEOUT_MS": "live_audio_prebuffer_timeout_ms",
    }
    for env_name, field in int_envs.items():
        if os.getenv(env_name) is not None:
            config[field] = env_int(env_name, int(config.get(field, DEFAULT_CONFIG[field])))

    bool_envs = {
        "USE_ELEVENLABS_FOR_ARABIC": "use_elevenlabs_for_arabic",
        "ENABLE_ELEVENLABS_STREAMING": "enable_elevenlabs_streaming",
        "ROBOT_AI_STREAMING_ENABLED": "robot_ai_streaming_enabled",
        "ROBOT_STRICT_LANGUAGE_LOCK": "robot_strict_language_lock",
    }
    for env_name, field in bool_envs.items():
        if os.getenv(env_name) is not None:
            config[field] = env_bool(env_name, bool(config.get(field, DEFAULT_CONFIG[field])))
    return config


def strip_env_managed_secrets_for_save(config: dict) -> dict:
    """Do not write API keys into JSON if Render env vars manage them."""
    data = DEFAULT_CONFIG.copy()
    data.update(config or {})
    if os.getenv("OPENAI_API_KEY"):
        data["openai_api_key"] = ""
    if os.getenv("ELEVENLABS_API_KEY"):
        data["elevenlabs_api_key"] = ""
    return data


PUBLIC_PATH_PREFIXES = (
    "/api/health",
    "/api/chat",
    "/api/next_audio",
    "/api/commands/match",
    "/api/remote/next",
    "/api/face-greetings/match",
    "/api/robot/runtime-config",
    "/api/robot/sync/apply",
    "/api/audio/live/",
    "/audio/",
)


def admin_auth_response():
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Promobot Dashboard"'},
    )


@app.before_request
def protect_dashboard_when_configured():
    """Optional Basic Auth for the public Render dashboard.
    Set ADMIN_USERNAME and ADMIN_PASSWORD on Render to enable it.
    Robot-facing endpoints remain public so the robot keeps working without code changes.
    """
    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        return None
    path = request.path or "/"
    if any(path == p or path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
        return None
    auth = request.authorization
    if not auth:
        return admin_auth_response()
    if not (compare_digest(auth.username or "", username) and compare_digest(auth.password or "", password)):
        return admin_auth_response()
    return None



def log_event(kind, message, extra=None):
    event = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "kind": kind,
        "message": message,
        "extra": extra or {}
    }
    with LOG_LOCK:
        LOG_EVENTS.append(event)
        if len(LOG_EVENTS) > 500:
            del LOG_EVENTS[:100]
    line = f"[{event['ts']}] {kind}: {message} {json.dumps(event.get('extra', {}), ensure_ascii=False)}"
    try:
        with open(SERVER_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 3)


def load_config():
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return apply_env_overrides(DEFAULT_CONFIG.copy())
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        config = DEFAULT_CONFIG.copy()
        config.update(data)
        return apply_env_overrides(config)
    except Exception:
        save_config(DEFAULT_CONFIG)
        return apply_env_overrides(DEFAULT_CONFIG.copy())


def save_config(config):
    safe_config = strip_env_managed_secrets_for_save(config)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(safe_config, f, ensure_ascii=False, indent=2)


def load_active_config():
    if not ACTIVE_CONFIG_PATH.exists():
        config = load_config()
        save_active_config(config)
        return config
    try:
        with open(ACTIVE_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        config = DEFAULT_CONFIG.copy()
        config.update(data)
        return apply_env_overrides(config)
    except Exception:
        config = load_config()
        save_active_config(config)
        return apply_env_overrides(config)


def save_active_config(config):
    safe_config = strip_env_managed_secrets_for_save(config)
    ACTIVE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(safe_config, f, ensure_ascii=False, indent=2)


def load_active_commands():
    if not ACTIVE_COMMANDS_PATH.exists():
        commands = load_commands()
        save_active_commands(commands)
        return commands
    try:
        with open(ACTIVE_COMMANDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("active_commands.json must contain a list")
        return data
    except Exception:
        commands = load_commands()
        save_active_commands(commands)
        return commands


def save_active_commands(commands):
    ACTIVE_COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_COMMANDS_PATH, "w", encoding="utf-8") as f:
        json.dump(commands, f, ensure_ascii=False, indent=2)


def load_active_face_greetings():
    if not ACTIVE_FACE_GREETINGS_PATH.exists():
        items = load_face_greetings()
        save_active_face_greetings(items)
        return items
    try:
        with open(ACTIVE_FACE_GREETINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("active_face_greetings.json must contain a list")
        return data
    except Exception:
        items = load_face_greetings()
        save_active_face_greetings(items)
        return items


def save_active_face_greetings(items):
    ACTIVE_FACE_GREETINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_FACE_GREETINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def apply_robot_sync_snapshot():
    config = load_config()
    commands = load_commands()
    face_greetings = load_face_greetings()
    save_active_config(config)
    save_active_commands(commands)
    save_active_face_greetings(face_greetings)
    return config, commands, face_greetings


def public_robot_runtime(config=None):
    config = config or load_active_config()
    return {
        "ai_streaming_enabled": bool(config.get("robot_ai_streaming_enabled", config.get("enable_elevenlabs_streaming", True))),
        "strict_language_lock": bool(config.get("robot_strict_language_lock", True)),
        "enable_elevenlabs_streaming": bool(config.get("enable_elevenlabs_streaming", True)),
        "model": config.get("model", ""),
        "elevenlabs_model_ar": config.get("elevenlabs_model_ar", ""),
        "elevenlabs_voice_id_ar": config.get("elevenlabs_voice_id_ar", "")
    }


def normalize_command_text(text: str) -> str:
    text = (text or "").strip().lower()
    replacements = {"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"[^\u0600-\u06FFa-zA-Z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_language(language: str) -> str:
    lang = (language or "").strip().lower()
    if lang.startswith("ar"):
        return "ar"
    if lang.startswith("en"):
        return "en"
    if lang.startswith("ku") or lang.startswith("ckb"):
        return "ku"
    return lang or "all"


def load_commands():
    if not COMMANDS_PATH.exists():
        save_commands(DEFAULT_COMMANDS)
        return [dict(x) for x in DEFAULT_COMMANDS]
    try:
        with open(COMMANDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("commands.json must contain a list")
        return data
    except Exception:
        save_commands(DEFAULT_COMMANDS)
        return [dict(x) for x in DEFAULT_COMMANDS]


def save_commands(commands):
    COMMANDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COMMANDS_PATH, "w", encoding="utf-8") as f:
        json.dump(commands, f, ensure_ascii=False, indent=2)


def load_face_greetings():
    if not FACE_GREETINGS_PATH.exists():
        save_face_greetings(DEFAULT_FACE_GREETINGS)
        return [dict(x) for x in DEFAULT_FACE_GREETINGS]
    try:
        with open(FACE_GREETINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("face_greetings.json must contain a list")
        return data
    except Exception:
        save_face_greetings(DEFAULT_FACE_GREETINGS)
        return [dict(x) for x in DEFAULT_FACE_GREETINGS]


def save_face_greetings(items):
    FACE_GREETINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FACE_GREETINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def next_face_greeting_id(items):
    max_id = 0
    for item in items:
        try:
            max_id = max(max_id, int(item.get("id", 0)))
        except Exception:
            pass
    return max_id + 1


def public_face_greeting(item: dict):
    recognition_type = (item.get("recognition_type") or "known").strip().lower()
    if recognition_type not in {"known", "unknown"}:
        recognition_type = "known"
    person_name = "" if recognition_type == "unknown" else item.get("person_name", "")
    return {
        "id": item.get("id"),
        "enabled": bool(item.get("enabled", True)),
        "recognition_type": recognition_type,
        "person_name": person_name,
        "match_type": item.get("match_type", "exact"),
        "language": item.get("language", "all"),
        "response_text": item.get("response_text", ""),
        "action_type": item.get("action_type", "ros_script"),
        "action_value": item.get("action_value", ""),
        "cooldown_sec": int(item.get("cooldown_sec", 60) or 60),
        "priority": int(item.get("priority", 0) or 0),
        "notes": item.get("notes", "")
    }


def face_greeting_matches(item: dict, username: str, language: str, recognition_type: str = "known") -> bool:
    if not item.get("enabled", True):
        return False

    req_lang = normalize_language(language)
    item_lang = (item.get("language") or "all").strip().lower()
    if item_lang not in {"", "all", req_lang}:
        return False

    requested_type = (recognition_type or "known").strip().lower()
    if requested_type not in {"known", "unknown"}:
        requested_type = "known"

    item_type = (item.get("recognition_type") or "known").strip().lower()
    if item_type not in {"known", "unknown"}:
        item_type = "known"

    if item_type != requested_type:
        return False

    if item_type == "unknown":
        return True

    username_norm = normalize_command_text(username)
    person_norm = normalize_command_text(item.get("person_name", ""))
    if not username_norm or not person_norm:
        return False

    match_type = (item.get("match_type") or "exact").strip().lower()
    if match_type == "exact":
        return username_norm == person_norm
    if match_type == "contains":
        return person_norm in username_norm or username_norm in person_norm
    if match_type == "regex":
        try:
            return bool(re.search(str(item.get("person_name", "")), username or "", flags=re.IGNORECASE))
        except Exception:
            return False
    return username_norm == person_norm


def find_matching_face_greeting(username: str, language: str, recognition_type: str = "known"):
    items = [public_face_greeting(x) for x in load_active_face_greetings()]
    matches = [x for x in items if face_greeting_matches(x, username, language, recognition_type)]
    if not matches:
        return None
    matches.sort(key=lambda x: int(x.get("priority", 0)), reverse=True)
    return matches[0]

def next_command_id(commands):
    max_id = 0
    for cmd in commands:
        try:
            max_id = max(max_id, int(cmd.get("id", 0)))
        except Exception:
            pass
    return max_id + 1


def public_command(cmd: dict):
    phrases = cmd.get("phrases", []) or []
    if isinstance(phrases, str):
        phrases = [x.strip() for x in phrases.splitlines() if x.strip()]
    return {
        "id": cmd.get("id"),
        "enabled": bool(cmd.get("enabled", True)),
        "name": cmd.get("name", ""),
        "trigger_mode": cmd.get("trigger_mode", "voice_remote"),
        "language": cmd.get("language", "all"),
        "phrases": phrases,
        "match_type": cmd.get("match_type", "contains"),
        "priority": int(cmd.get("priority", 0) or 0),
        "action_type": cmd.get("action_type", "none"),
        "action_value": cmd.get("action_value", ""),
        "reply_text": cmd.get("reply_text", ""),
        "extra_action_type": cmd.get("extra_action_type", ""),
        "extra_action_value": cmd.get("extra_action_value", ""),
        "required_language": cmd.get("required_language", cmd.get("action_value", "")),
        "mute_state": cmd.get("mute_state", cmd.get("action_value", "")),
        "media_duration_sec": int(cmd.get("media_duration_sec", 5) or 5),
        "notes": cmd.get("notes", "")
    }


def command_matches(cmd: dict, text: str, language: str) -> bool:
    if not cmd.get("enabled", True):
        return False
    trigger_mode = (cmd.get("trigger_mode") or "voice_remote").strip().lower()
    if trigger_mode == "remote_only":
        return False
    cmd_lang = (cmd.get("language") or "all").strip().lower()
    req_lang = normalize_language(language)
    if cmd_lang not in {"", "all", req_lang}:
        return False
    phrases = cmd.get("phrases") or []
    if not phrases:
        return False
    text_norm = normalize_command_text(text)
    match_type = (cmd.get("match_type") or "contains").strip().lower()
    for phrase in phrases:
        phrase_raw = str(phrase)
        phrase_norm = normalize_command_text(phrase_raw)
        if not phrase_norm:
            continue
        if match_type == "exact" and text_norm == phrase_norm:
            return True
        if match_type == "contains" and phrase_norm in text_norm:
            return True
        if match_type == "regex":
            try:
                if re.search(phrase_raw, text or "", flags=re.IGNORECASE):
                    return True
            except Exception:
                continue
    return False


def find_matching_command(text, language):
    commands = [public_command(c) for c in load_active_commands()]
    matches = [c for c in commands if command_matches(c, text, language)]
    if not matches:
        return None
    matches.sort(key=lambda c: int(c.get("priority", 0)), reverse=True)
    return matches[0]


def queue_remote_command(robot_id: str, command: dict):
    item = {
        "id": str(uuid.uuid4()),
        "robot_id": robot_id or "promobot_v4_0445",
        "created_at": time.time(),
        "command": public_command(command)
    }
    with REMOTE_COMMAND_QUEUE_LOCK:
        REMOTE_COMMAND_QUEUE.append(item)
    log_event("remote", "Command queued", {"robot_id": item["robot_id"], "command": item["command"].get("name")})
    return item


def pop_remote_command(robot_id: str):
    robot_id = robot_id or "promobot_v4_0445"
    with REMOTE_COMMAND_QUEUE_LOCK:
        for i, item in enumerate(REMOTE_COMMAND_QUEUE):
            if item.get("robot_id") in {robot_id, "all", "*"}:
                return REMOTE_COMMAND_QUEUE.pop(i)
    return None


def is_arabic_text(text: str) -> bool:
    return bool(text and any("\u0600" <= ch <= "\u06FF" for ch in text))


def should_use_elevenlabs_arabic(language_code: str, text: str) -> bool:
    language_code = (language_code or "").strip().lower()
    if language_code.startswith("ar"):
        return True
    return is_arabic_text(text)


def likely_needs_details(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    greetings = {"مرحبا", "هلا", "هلو", "السلام عليكم", "سلام", "hello", "hi", "hey"}
    if q in greetings:
        return False
    detail_keywords = [
        "شنو", "ما هي", "ما هو", "اشرح", "شرح", "تفاصيل", "خدمات", "باقات",
        "اسعار", "أسعار", "سعر", "متوفر", "متوفرة", "شلون", "كيف", "ليش",
        "مقارنة", "قارن", "انواع", "أنواع", "مميزات", "خطوات", "الشركة", "الروبوت", "الروبوتات"
    ]
    if any(k in q for k in detail_keywords):
        return True
    return len(q) > 22


def arabic_number_under_1000(n: int) -> str:
    ones = {0: "صفر", 1: "واحد", 2: "اثنان", 3: "ثلاثة", 4: "أربعة", 5: "خمسة", 6: "ستة", 7: "سبعة", 8: "ثمانية", 9: "تسعة", 10: "عشرة", 11: "أحد عشر", 12: "اثنا عشر", 13: "ثلاثة عشر", 14: "أربعة عشر", 15: "خمسة عشر", 16: "ستة عشر", 17: "سبعة عشر", 18: "ثمانية عشر", 19: "تسعة عشر"}
    tens = {20: "عشرون", 30: "ثلاثون", 40: "أربعون", 50: "خمسون", 60: "ستون", 70: "سبعون", 80: "ثمانون", 90: "تسعون"}
    hundreds = {100: "مئة", 200: "مئتان", 300: "ثلاثمئة", 400: "أربعمئة", 500: "خمسمئة", 600: "ستمئة", 700: "سبعمئة", 800: "ثمانمئة", 900: "تسعمئة"}
    if n < 20:
        return ones[n]
    if n < 100:
        if n in tens:
            return tens[n]
        return ones[n % 10] + " و" + tens[n - (n % 10)]
    if n in hundreds:
        return hundreds[n]
    return hundreds[(n // 100) * 100] + " و" + arabic_number_under_1000(n % 100)


def arabic_number_to_words(n: int) -> str:
    if n == 0:
        return "صفر"
    if n < 0:
        return "ناقص " + arabic_number_to_words(abs(n))
    if n < 1000:
        return arabic_number_under_1000(n)
    if n < 1000000:
        thousands = n // 1000
        rest = n % 1000
        if thousands == 1:
            result = "ألف"
        elif thousands == 2:
            result = "ألفان"
        elif 3 <= thousands <= 10:
            result = arabic_number_under_1000(thousands) + " آلاف"
        else:
            result = arabic_number_to_words(thousands) + " ألف"
        if rest:
            result += " و" + arabic_number_under_1000(rest)
        return result
    return str(n)


def replace_numbers_with_arabic_words(text: str) -> str:
    def repl(match):
        try:
            return arabic_number_to_words(int(match.group(0)))
        except Exception:
            return match.group(0)
    return re.sub(r"\d+", repl, text)


def clean_text_for_elevenlabs(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"www\.\S+", " ", cleaned)
    cleaned = cleaned.replace("%", " بالمئة ").replace("$", " دولار ").replace("€", " يورو ").replace("£", " باوند ")
    cleaned = cleaned.replace("IQD", " دينار عراقي ").replace("USD", " دولار ")
    cleaned = replace_numbers_with_arabic_words(cleaned)
    cleaned = re.sub(r"[*_#`~>\[\]{}|\\]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def split_text_for_tts(text: str, max_chars: int = 230, max_parts: int = 2):
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return []
    parts = re.split(r"(?<=[.!؟?،])\s+", clean)
    chunks, current = [], ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not current:
            current = part
        elif len(current) + 1 + len(part) <= max_chars:
            current += " " + part
        else:
            chunks.append(current)
            current = part
    if current:
        chunks.append(current)
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
            continue
        words, current = chunk.split(), ""
        for word in words:
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chars:
                current += " " + word
            else:
                final_chunks.append(current)
                current = word
        if current:
            final_chunks.append(current)
    if max_parts and len(final_chunks) > max_parts:
        merged = final_chunks[:max_parts - 1]
        remaining = " ".join(final_chunks[max_parts - 1:])
        merged.append(remaining[:max_chars].strip())
        return [x for x in merged if x]
    return final_chunks


def store_audio(audio_bytes: bytes, audio_id=None):
    audio_id = audio_id or f"{uuid.uuid4().hex}.mp3"
    with AUDIO_STORE_LOCK:
        AUDIO_STORE[audio_id] = {"data": audio_bytes, "created_at": time.time()}
        if len(AUDIO_STORE) > 200:
            old_ids = sorted(AUDIO_STORE, key=lambda k: AUDIO_STORE[k]["created_at"])[:50]
            for old_id in old_ids:
                AUDIO_STORE.pop(old_id, None)
    return audio_id


def generate_elevenlabs_arabic_audio(config, text: str, audio_id: str = None) -> str:
    api_key = config.get("elevenlabs_api_key", "").strip()
    voice_id = config.get("elevenlabs_voice_id_ar", "").strip()
    model_id = config.get("elevenlabs_model_ar", "eleven_flash_v2_5").strip()
    if not api_key:
        raise RuntimeError("ElevenLabs API key is not configured")
    if not voice_id:
        raise RuntimeError("ElevenLabs Arabic voice_id is not configured")
    tts_text = clean_text_for_elevenlabs(text)
    if not tts_text:
        raise RuntimeError("TTS text is empty after cleaning")
    client = ElevenLabs(api_key=api_key)
    response = client.text_to_speech.convert(
        voice_id=voice_id,
        output_format="mp3_22050_32",
        text=tts_text,
        model_id=model_id,
        voice_settings=VoiceSettings(stability=0.45, similarity_boost=0.85, style=0.0, use_speaker_boost=True, speed=0.95),
    )
    audio_bytes = b"".join(chunk for chunk in response if chunk)
    return store_audio(audio_bytes, audio_id or f"{uuid.uuid4().hex}.mp3")



def create_live_audio_job(config, question: str, language: str, host_url: str):
    request_id = str(uuid.uuid4())[:8]
    job = {
        "request_id": request_id,
        "config": config.copy(),
        "question": question,
        "language": language,
        "host_url": host_url,
        "created_at": time.time(),
        "started": False,
        "status": "waiting",
        "error": "",
    }
    with LIVE_AUDIO_JOBS_LOCK:
        LIVE_AUDIO_JOBS[request_id] = job
        if len(LIVE_AUDIO_JOBS) > 100:
            old_ids = sorted(LIVE_AUDIO_JOBS, key=lambda k: LIVE_AUDIO_JOBS[k].get("created_at", 0))[:30]
            for old_id in old_ids:
                LIVE_AUDIO_JOBS.pop(old_id, None)
    return job


def get_live_audio_job(request_id: str):
    with LIVE_AUDIO_JOBS_LOCK:
        return LIVE_AUDIO_JOBS.get(request_id)


def set_live_audio_job_status(request_id: str, status: str, error: str = ""):
    with LIVE_AUDIO_JOBS_LOCK:
        job = LIVE_AUDIO_JOBS.get(request_id)
        if job:
            job["status"] = status
            job["error"] = error


def extract_openai_text_delta(event) -> str:
    event_type = getattr(event, "type", "") or ""
    if event_type in {"response.output_text.delta", "response.refusal.delta"}:
        return getattr(event, "delta", "") or ""
    if isinstance(event, dict):
        event_type = event.get("type", "")
        if event_type in {"response.output_text.delta", "response.refusal.delta"}:
            return event.get("delta", "") or ""
    return ""


def stream_openai_text_to_queue(config, question: str, text_queue: queue.Queue, request_id: str):
    try:
        api_key = config.get("openai_api_key", "").strip()
        model = config.get("model", "gpt-4.1-mini").strip()
        client = OpenAI(api_key=api_key)
        instructions = config.get("system_prompt", "") + "\n\n" + config.get("quick_answer_prompt", DEFAULT_CONFIG["quick_answer_prompt"])
        buffer = ""
        sent_chars = 0
        stream = client.responses.create(
            model=model,
            instructions=instructions,
            input=question,
            max_output_tokens=int(config.get("quick_max_output_tokens", 90)),
            stream=True,
        )
        for event in stream:
            delta = extract_openai_text_delta(event)
            if not delta:
                continue
            buffer += delta
            if len(buffer) >= 55 or re.search(r"[.!؟?،]\s*$", buffer):
                chunk = buffer.strip()
                if chunk:
                    text_queue.put(chunk)
                    sent_chars += len(chunk)
                buffer = ""
        if buffer.strip():
            text_queue.put(buffer.strip())
        log_event("stream", "OpenAI streaming text completed", {"request_id": request_id, "chars": sent_chars})
    except Exception as e:
        log_event("error", f"OpenAI streaming error {request_id}: {e}")
        text_queue.put({"error": str(e)})
    finally:
        text_queue.put(None)


async def elevenlabs_ws_to_audio_queue(config, text_queue: queue.Queue, audio_queue: queue.Queue, request_id: str):
    api_key = config.get("elevenlabs_api_key", "").strip()
    voice_id = config.get("elevenlabs_voice_id_ar", "").strip()
    model_id = config.get("elevenlabs_model_ar", "eleven_flash_v2_5").strip()
    output_format = config.get("elevenlabs_stream_output_format", "mp3_22050_32").strip() or "mp3_22050_32"
    latency = int(config.get("elevenlabs_stream_latency", 3) or 3)
    if not api_key:
        raise RuntimeError("ElevenLabs API key is not configured")
    if not voice_id:
        raise RuntimeError("ElevenLabs Arabic voice_id is not configured")

    if websockets is None:
        raise RuntimeError("Python package websockets is not installed. Run: pip install websockets")

    uri = (
        f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input"
        f"?model_id={model_id}&output_format={output_format}&optimize_streaming_latency={latency}"
    )

    async with websockets.connect(uri, max_size=None) as ws:
        await ws.send(json.dumps({
            "text": " ",
            "xi_api_key": api_key,
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.85,
                "style": 0.0,
                "use_speaker_boost": True,
                "speed": 0.95,
            },
            "generation_config": {
                "chunk_length_schedule": [50, 80, 120, 160]
            }
        }))

        async def receiver():
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                audio_b64 = data.get("audio")
                if audio_b64:
                    try:
                        audio_queue.put(base64.b64decode(audio_b64))
                    except Exception:
                        pass
                if data.get("isFinal"):
                    break

        receive_task = asyncio.create_task(receiver())

        while True:
            item = await asyncio.to_thread(text_queue.get)
            if item is None:
                break
            if isinstance(item, dict) and item.get("error"):
                raise RuntimeError(item.get("error"))
            tts_text = clean_text_for_elevenlabs(str(item))
            if tts_text:
                await ws.send(json.dumps({"text": tts_text + " ", "try_trigger_generation": True}))

        await ws.send(json.dumps({"text": ""}))
        await receive_task


def live_audio_worker(job: dict, audio_queue: queue.Queue):
    request_id = job.get("request_id")
    config = job.get("config") or {}
    question = job.get("question") or ""
    text_queue = queue.Queue()
    try:
        set_live_audio_job_status(request_id, "running")
        threading.Thread(
            target=stream_openai_text_to_queue,
            args=(config, question, text_queue, request_id),
            daemon=True,
        ).start()
        asyncio.run(elevenlabs_ws_to_audio_queue(config, text_queue, audio_queue, request_id))
        set_live_audio_job_status(request_id, "done")
        log_event("stream", "Live audio stream completed", {"request_id": request_id})
    except Exception as e:
        set_live_audio_job_status(request_id, "error", str(e))
        log_event("error", f"Live audio stream error {request_id}: {e}")
    finally:
        audio_queue.put(None)

def openai_answer(client, model, instructions, question, max_tokens):
    response = client.responses.create(model=model, instructions=instructions, input=question, max_output_tokens=max_tokens)
    return response.output_text.strip()


def get_job(request_id):
    with DETAIL_JOBS_LOCK:
        return DETAIL_JOBS.get(request_id)


def set_job(request_id, job):
    with DETAIL_JOBS_LOCK:
        DETAIL_JOBS[request_id] = job


def update_job(request_id, **kwargs):
    with DETAIL_JOBS_LOCK:
        job = DETAIL_JOBS.get(request_id)
        if not job:
            return
        job.update(kwargs)


def detail_worker(request_id, config, question, quick_answer, language, host_url):
    _ = language
    t0 = time.perf_counter()
    try:
        api_key = config.get("openai_api_key", "").strip()
        model = config.get("model", "gpt-4.1-mini").strip()
        client = OpenAI(api_key=api_key)
        detail_prompt = config.get("system_prompt", "") + "\n\n" + config.get("detail_answer_prompt", DEFAULT_CONFIG["detail_answer_prompt"]) + "\n\nThe first quick answer already said:\n" + quick_answer + "\n\nNow continue with useful details only."
        gpt_t0 = time.perf_counter()
        detail_answer = openai_answer(client, model, detail_prompt, question, int(config.get("detail_max_output_tokens", 260)))
        detail_gpt_sec = elapsed(gpt_t0)
        chunk_max = int(config.get("tts_chunk_max_chars", 230))
        max_parts = int(config.get("max_detail_parts", 2))
        chunks = split_text_for_tts(detail_answer, max_chars=chunk_max, max_parts=max_parts)
        parts = []
        for idx, chunk in enumerate(chunks, start=2):
            audio_id = f"{request_id}_part_{idx}_{uuid.uuid4().hex[:8]}.mp3"
            generate_elevenlabs_arabic_audio(config, chunk, audio_id=audio_id)
            parts.append({"part": idx, "text": chunk, "audio_id": audio_id, "audio_url": host_url.rstrip("/") + "/audio/" + audio_id})
        update_job(request_id, status="ready", detail_answer=detail_answer, parts=parts, error="", detail_gpt_sec=detail_gpt_sec, detail_total_sec=elapsed(t0))
        log_event("ai", f"Detail job ready {request_id}", {"parts": len(parts)})
    except Exception as e:
        update_job(request_id, status="error", error=str(e), parts=[])
        log_event("error", f"Detail job error {request_id}: {e}")


@app.context_processor
def inject_common():
    return {"config": load_config(), "command_count": len(load_commands()), "face_greeting_count": len(load_face_greetings())}


@app.route("/")
def root():
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
def dashboard():
    commands = [public_command(c) for c in load_commands()]
    by_type = {}
    for cmd in commands:
        by_type[cmd["action_type"]] = by_type.get(cmd["action_type"], 0) + 1
    return render_template("overview.html", commands=commands, by_type=by_type, audio_count=len(AUDIO_STORE), queue_count=len(REMOTE_COMMAND_QUEUE), face_greeting_count=len(load_face_greetings()))


@app.route("/api-settings")
def api_settings():
    return render_template("api_settings.html", config=load_config())


@app.route("/system-prompt")
def system_prompt_page():
    return render_template("system_prompt.html", config=load_config())


@app.route("/commands")
def commands_page():
    return render_template("commands.html")


@app.route("/usage")
def usage_page():
    return render_template("usage.html")


@app.route("/logs")
def logs_page():
    return render_template("logs.html")


@app.route("/settings", methods=["POST"])
def save_settings():
    config = load_config()
    fields = [
        "openai_api_key", "model", "elevenlabs_api_key", "elevenlabs_voice_id_ar",
        "elevenlabs_model_ar", "quick_max_output_tokens", "detail_max_output_tokens",
        "tts_chunk_max_chars", "max_detail_parts", "audio_wait_timeout_sec",
        "elevenlabs_stream_output_format", "elevenlabs_stream_latency",
        "robot_ai_streaming_enabled", "robot_strict_language_lock"
    ]
    data = request.get_json(silent=True)
    if data:
        for field in fields:
            if field in data:
                value = data.get(field)
                if field in {"quick_max_output_tokens", "detail_max_output_tokens", "tts_chunk_max_chars", "max_detail_parts", "audio_wait_timeout_sec", "elevenlabs_stream_latency"}:
                    config[field] = int(value or DEFAULT_CONFIG[field])
                else:
                    config[field] = str(value or "")
        if "use_elevenlabs_for_arabic" in data:
            config["use_elevenlabs_for_arabic"] = bool(data.get("use_elevenlabs_for_arabic"))
        if "enable_elevenlabs_streaming" in data:
            config["enable_elevenlabs_streaming"] = bool(data.get("enable_elevenlabs_streaming"))
        if "robot_ai_streaming_enabled" in data:
            config["robot_ai_streaming_enabled"] = bool(data.get("robot_ai_streaming_enabled"))
        if "robot_strict_language_lock" in data:
            config["robot_strict_language_lock"] = bool(data.get("robot_strict_language_lock"))
        save_config(config)
        return jsonify({"ok": True, "message": "Settings saved"})
    for field in fields:
        value = request.form.get(field, "").strip()
        if value:
            config[field] = int(value) if field in {"quick_max_output_tokens", "detail_max_output_tokens", "tts_chunk_max_chars", "max_detail_parts", "audio_wait_timeout_sec", "elevenlabs_stream_latency"} else value
    config["use_elevenlabs_for_arabic"] = request.form.get("use_elevenlabs_for_arabic") == "on"
    config["enable_elevenlabs_streaming"] = request.form.get("enable_elevenlabs_streaming") == "on"
    config["robot_ai_streaming_enabled"] = request.form.get("robot_ai_streaming_enabled") == "on"
    config["robot_strict_language_lock"] = request.form.get("robot_strict_language_lock") == "on"
    save_config(config)
    return redirect(url_for("api_settings"))


@app.route("/prompt", methods=["POST"])
def save_prompt():
    config = load_config()
    data = request.get_json(silent=True)
    if data:
        for field in ["system_prompt", "quick_answer_prompt", "detail_answer_prompt"]:
            if field in data:
                config[field] = str(data.get(field) or "")
        save_config(config)
        return jsonify({"ok": True, "message": "Prompt saved"})
    for field in ["system_prompt", "quick_answer_prompt", "detail_answer_prompt"]:
        value = request.form.get(field, "").strip()
        if value:
            config[field] = value
    save_config(config)
    return redirect(url_for("system_prompt_page"))


@app.route("/api/commands", methods=["GET"])
def api_commands_list():
    return jsonify({"ok": True, "commands": [public_command(c) for c in load_commands()]})


@app.route("/api/commands", methods=["POST"])
def api_commands_create():
    commands = load_commands()
    data = request.get_json(silent=True) or {}
    data["id"] = next_command_id(commands)
    command = public_command(data)
    commands.append(command)
    save_commands(commands)
    log_event("commands", "Command created", {"id": command["id"], "name": command["name"]})
    return jsonify({"ok": True, "message": "Command created", "command": command})


@app.route("/api/commands/<int:command_id>", methods=["PUT"])
def api_commands_update(command_id):
    commands = load_commands()
    data = request.get_json(silent=True) or {}
    found = False
    for i, cmd in enumerate(commands):
        if int(cmd.get("id", 0)) == command_id:
            data["id"] = command_id
            commands[i] = public_command(data)
            found = True
            break
    if not found:
        return jsonify({"ok": False, "message": "Command not found"}), 404
    save_commands(commands)
    log_event("commands", "Command updated", {"id": command_id})
    return jsonify({"ok": True, "message": "Command updated", "command": public_command(data)})


@app.route("/api/commands/<int:command_id>", methods=["DELETE"])
def api_commands_delete(command_id):
    commands = load_commands()
    new_commands = [c for c in commands if int(c.get("id", 0)) != command_id]
    save_commands(new_commands)
    log_event("commands", "Command deleted", {"id": command_id})
    return jsonify({"ok": True, "message": "Command deleted"})


@app.route("/api/commands/seed", methods=["POST"])
def api_commands_seed():
    save_commands(DEFAULT_COMMANDS)
    return jsonify({"ok": True, "message": "Default commands restored", "commands": DEFAULT_COMMANDS})


@app.route("/api/commands/match", methods=["POST"])
def api_commands_match():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    language = data.get("language", "")
    robot_id = data.get("robot_id", "promobot_v4_0445")
    match = find_matching_command(text, language)
    if not match:
        return jsonify({"ok": True, "matched": False, "robot_id": robot_id})
    log_event("match", "Command matched", {"robot_id": robot_id, "command": match.get("name"), "text": text})
    return jsonify({"ok": True, "matched": True, "robot_id": robot_id, "command": public_command(match)})


@app.route("/api/remote/execute", methods=["POST"])
def api_remote_execute():
    data = request.get_json(silent=True) or {}
    robot_id = data.get("robot_id", "promobot_v4_0445")
    command_id = data.get("command_id")
    command = data.get("command")
    if command_id is not None:
        command = None
        for cmd in load_active_commands():
            if int(cmd.get("id", 0)) == int(command_id):
                command = cmd
                break
        if not command:
            return jsonify({"ok": False, "message": "Command not found"}), 404
    if not command:
        return jsonify({"ok": False, "message": "command or command_id is required"}), 400
    item = queue_remote_command(robot_id, command)
    return jsonify({"ok": True, "message": "Command queued", "queue_item": item})


@app.route("/api/remote/next", methods=["GET"])
def api_remote_next():
    robot_id = request.args.get("robot_id", "promobot_v4_0445")
    item = pop_remote_command(robot_id)
    if not item:
        return jsonify({"ok": True, "has_command": False})
    return jsonify({"ok": True, "has_command": True, "queue_id": item.get("id"), "command": item.get("command")})


@app.route("/api/face-greetings", methods=["GET"])
def api_face_greetings_list():
    return jsonify({"ok": True, "face_greetings": [public_face_greeting(x) for x in load_face_greetings()]})


@app.route("/api/face-greetings", methods=["POST"])
def api_face_greetings_create():
    items = load_face_greetings()
    data = request.get_json(silent=True) or {}
    data["id"] = next_face_greeting_id(items)
    item = public_face_greeting(data)
    items.append(item)
    save_face_greetings(items)
    log_event("face", "Face greeting created", {"id": item["id"], "person_name": item["person_name"]})
    return jsonify({"ok": True, "message": "Face greeting created", "face_greeting": item})


@app.route("/api/face-greetings/<int:greeting_id>", methods=["PUT"])
def api_face_greetings_update(greeting_id):
    items = load_face_greetings()
    data = request.get_json(silent=True) or {}
    found = False
    for i, item in enumerate(items):
        if int(item.get("id", 0)) == greeting_id:
            data["id"] = greeting_id
            items[i] = public_face_greeting(data)
            found = True
            break
    if not found:
        return jsonify({"ok": False, "message": "Face greeting not found"}), 404
    save_face_greetings(items)
    log_event("face", "Face greeting updated", {"id": greeting_id})
    return jsonify({"ok": True, "message": "Face greeting updated", "face_greeting": public_face_greeting(data)})


@app.route("/api/face-greetings/<int:greeting_id>", methods=["DELETE"])
def api_face_greetings_delete(greeting_id):
    items = load_face_greetings()
    new_items = [x for x in items if int(x.get("id", 0)) != greeting_id]
    save_face_greetings(new_items)
    log_event("face", "Face greeting deleted", {"id": greeting_id})
    return jsonify({"ok": True, "message": "Face greeting deleted"})


@app.route("/api/face-greetings/match", methods=["POST"])
def api_face_greetings_match():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    language = data.get("language", "")
    robot_id = data.get("robot_id", "promobot_v4_0445")
    track_id = data.get("track_id", 0)
    recognition_type = (data.get("recognition_type") or data.get("face_type") or ("known" if str(username).strip() else "unknown")).strip().lower()
    if recognition_type not in {"known", "unknown"}:
        recognition_type = "known"
    if recognition_type == "unknown":
        username = ""
    match = find_matching_face_greeting(username, language, recognition_type)
    if not match:
        draft_items = [public_face_greeting(x) for x in load_face_greetings()]
        draft_matches = [x for x in draft_items if face_greeting_matches(x, username, language, recognition_type)]
        draft_matches.sort(key=lambda x: int(x.get("priority", 0)), reverse=True)
        draft_match = draft_matches[0] if draft_matches else None
        if draft_match:
            log_event("face", "Face greeting exists in draft but not active snapshot", {
                "robot_id": robot_id,
                "username": username,
                "recognition_type": recognition_type,
                "draft_greeting": draft_match.get("person_name") or "unknown",
            })
        return jsonify({
            "ok": True,
            "matched": False,
            "robot_id": robot_id,
            "username": username,
            "track_id": track_id,
            "recognition_type": recognition_type,
            "draft_match_exists": bool(draft_match),
            "message": "Face greeting exists in draft but is not active. Press Sync server changes to robot." if draft_match else "No active face greeting matched"
        })
    log_event("face", "Face greeting matched", {"robot_id": robot_id, "username": username, "recognition_type": recognition_type, "greeting": match.get("person_name")})
    return jsonify({"ok": True, "matched": True, "robot_id": robot_id, "username": username, "track_id": track_id, "recognition_type": recognition_type, "greeting": public_face_greeting(match)})


@app.route("/api/robot/runtime-config", methods=["GET"])
def api_robot_runtime_config():
    config = load_active_config()
    return jsonify({"ok": True, "runtime": public_robot_runtime(config)})


@app.route("/api/robot/sync/apply", methods=["POST"])
def api_robot_sync_apply():
    config, commands, face_greetings = apply_robot_sync_snapshot()
    robot_id = (request.get_json(silent=True) or {}).get("robot_id", "promobot_v4_0445")
    log_event("sync", "Robot sync applied", {
        "robot_id": robot_id,
        "commands": len(commands),
        "face_greetings": len(face_greetings)
    })
    return jsonify({
        "ok": True,
        "message": "Server changes synced to active robot snapshot",
        "runtime": public_robot_runtime(config),
        "counts": {"commands": len(commands), "face_greetings": len(face_greetings)}
    })


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "server": "online", "robot": "unknown", "message": "Promobot backend is running"})


@app.route("/api/overview", methods=["GET"])
def api_overview():
    commands = [public_command(c) for c in load_commands()]
    return jsonify({
        "ok": True,
        "commands": len(commands),
        "voice_commands": len([c for c in commands if c.get("phrases")]),
        "remote_only": len([c for c in commands if c.get("trigger_mode") == "remote_only"]),
        "audio_cached": len(AUDIO_STORE),
        "queue": len(REMOTE_COMMAND_QUEUE),
        "face_greetings": len(load_face_greetings())
    })


@app.route("/api/logs", methods=["GET"])
def api_logs():
    with LOG_LOCK:
        return jsonify({"ok": True, "logs": list(reversed(LOG_EVENTS[-200:]))})



@app.route("/api/audio/live/<request_id>", methods=["GET"])
def live_audio_stream(request_id):
    job = get_live_audio_job(request_id)
    if not job:
        return jsonify({"ok": False, "error": "Live audio job not found", "request_id": request_id}), 404

    with LIVE_AUDIO_JOBS_LOCK:
        if job.get("started"):
            return jsonify({"ok": False, "error": "Live audio stream already consumed", "request_id": request_id}), 409
        job["started"] = True

    audio_queue = queue.Queue(maxsize=120)
    threading.Thread(target=live_audio_worker, args=(job, audio_queue), daemon=True).start()

    def generate():
        first_chunk_time = None
        start_time = time.perf_counter()
        config = job.get("config") or load_config()
        prebuffer_bytes = int(config.get("live_audio_prebuffer_bytes", 1024) or 1024)
        prebuffer_timeout_ms = int(config.get("live_audio_prebuffer_timeout_ms", 300) or 300)
        prebuffer_deadline = time.perf_counter() + max(0.1, prebuffer_timeout_ms / 1000.0)
        prebuffer = []
        prebuffer_size = 0

        while True:
            timeout = max(0.05, prebuffer_deadline - time.perf_counter())
            try:
                chunk = audio_queue.get(timeout=timeout)
            except queue.Empty:
                chunk = b""

            if chunk is None:
                if prebuffer:
                    first_chunk_time = elapsed(start_time)
                    log_event("stream", "First live audio prebuffer sent before final", {
                        "request_id": request_id,
                        "sec": first_chunk_time,
                        "bytes": prebuffer_size
                    })
                    yield b"".join(prebuffer)
                break

            if chunk:
                prebuffer.append(chunk)
                prebuffer_size += len(chunk)

            if prebuffer and (prebuffer_size >= prebuffer_bytes or time.perf_counter() >= prebuffer_deadline):
                first_chunk_time = elapsed(start_time)
                log_event("stream", "First live audio prebuffer sent", {
                    "request_id": request_id,
                    "sec": first_chunk_time,
                    "bytes": prebuffer_size
                })
                yield b"".join(prebuffer)
                break

        while True:
            chunk = audio_queue.get()
            if chunk is None:
                break
            if chunk:
                yield chunk

    response = Response(generate(), mimetype="audio/mpeg", direct_passthrough=True)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response

@app.route("/audio/<path:audio_id>", methods=["GET"])
def serve_audio(audio_id):
    config = load_active_config()
    timeout_sec = int(config.get("audio_wait_timeout_sec", 45))
    start = time.perf_counter()
    audio = None
    while elapsed(start) < timeout_sec:
        with AUDIO_STORE_LOCK:
            audio = AUDIO_STORE.get(audio_id)
        if audio:
            break
        time.sleep(0.1)
    if not audio:
        return jsonify({"ok": False, "error": "Audio file is not ready or does not exist", "audio_id": audio_id}), 404
    return Response(audio["data"], mimetype="audio/mpeg")


@app.route("/api/chat", methods=["POST"])
def chat():
    request_id = str(uuid.uuid4())[:8]
    total_t0 = time.perf_counter()
    config = load_active_config()
    data = request.get_json(silent=True) or {}
    question = data.get("question", "").strip()
    language = data.get("language", "").strip()
    log_event("chat", "Chat request", {"request_id": request_id, "language": language, "question": question})
    if not question:
        return jsonify({"ok": False, "answer": "", "error": "Missing question", "tts_provider": "promobot", "audio_url": "", "request_id": request_id, "has_more_audio": False}), 400
    api_key = config.get("openai_api_key", "").strip()
    model = config.get("model", "").strip()
    if not api_key:
        return jsonify({"ok": False, "answer": "", "error": "OpenAI API key is not configured", "tts_provider": "promobot", "audio_url": "", "request_id": request_id, "has_more_audio": False}), 400

    force_robot_local_tts = bool(data.get("force_robot_local_tts", False))
    if "use_elevenlabs_streaming" in data:
        streaming_enabled = bool(data.get("use_elevenlabs_streaming"))
    else:
        streaming_enabled = bool(config.get("robot_ai_streaming_enabled", config.get("enable_elevenlabs_streaming", True)))
    streaming_enabled = streaming_enabled and bool(config.get("enable_elevenlabs_streaming", True)) and not force_robot_local_tts

    if streaming_enabled and should_use_elevenlabs_arabic(language, question):
        if not config.get("elevenlabs_api_key", "").strip():
            return jsonify({"ok": False, "answer": "", "error": "ElevenLabs API key is not configured", "tts_provider": "promobot", "audio_url": "", "request_id": request_id, "has_more_audio": False}), 400
        if not config.get("elevenlabs_voice_id_ar", "").strip():
            return jsonify({"ok": False, "answer": "", "error": "ElevenLabs Arabic voice_id is not configured", "tts_provider": "promobot", "audio_url": "", "request_id": request_id, "has_more_audio": False}), 400
        live_job = create_live_audio_job(config, question, language, request.host_url)
        live_id = live_job.get("request_id")
        audio_url = request.host_url.rstrip("/") + "/api/audio/live/" + live_id
        log_event("stream", "Live ElevenLabs stream job created", {"request_id": live_id, "language": language})
        return jsonify({
            "ok": True,
            "answer": "...",
            "model": model,
            "tts_provider": "elevenlabs_stream",
            "audio_url": audio_url,
            "request_id": live_id,
            "has_more_audio": False,
            "real_tts_provider": "elevenlabs_stream",
            "tts_voice": config.get("elevenlabs_voice_id_ar", ""),
            "tts_model": config.get("elevenlabs_model_ar", ""),
            "timing": {"request_id": live_id, "stream_job_created_sec": elapsed(total_t0)}
        })

    try:
        client = OpenAI(api_key=api_key)
        quick_prompt = config.get("system_prompt", "") + "\n\n" + config.get("quick_answer_prompt", DEFAULT_CONFIG["quick_answer_prompt"])
        gpt_t0 = time.perf_counter()
        quick_answer = openai_answer(client, model, quick_prompt, question, int(config.get("quick_max_output_tokens", 90)))
        gpt_sec = elapsed(gpt_t0)
        audio_url = ""
        tts_provider = "promobot"
        first_tts_sec = 0
        has_more_audio = False if force_robot_local_tts else likely_needs_details(question)
        if (not force_robot_local_tts) and bool(config.get("use_elevenlabs_for_arabic", True)) and should_use_elevenlabs_arabic(language, quick_answer):
            audio_id = f"{request_id}_part_1_{uuid.uuid4().hex[:8]}.mp3"
            first_tts_t0 = time.perf_counter()
            generate_elevenlabs_arabic_audio(config, quick_answer, audio_id=audio_id)
            first_tts_sec = elapsed(first_tts_t0)
            audio_url = request.host_url.rstrip("/") + "/audio/" + audio_id
            tts_provider = "elevenlabs"
            log_event("tts", "ElevenLabs Arabic audio generated", {"request_id": request_id, "voice_id": config.get("elevenlabs_voice_id_ar", ""), "model": config.get("elevenlabs_model_ar", "")})
        if has_more_audio:
            set_job(request_id, {"status": "running", "question": question, "quick_answer": quick_answer, "parts": [], "error": "", "created_at": time.time()})
            threading.Thread(target=detail_worker, args=(request_id, config.copy(), question, quick_answer, language, request.host_url), daemon=True).start()
        return jsonify({
            "ok": True,
            "answer": quick_answer,
            "model": model,
            "tts_provider": tts_provider,
            "audio_url": audio_url,
            "request_id": request_id,
            "has_more_audio": has_more_audio,
            "real_tts_provider": "elevenlabs" if audio_url else "promobot",
            "tts_voice": config.get("elevenlabs_voice_id_ar", "") if audio_url else "",
            "tts_model": config.get("elevenlabs_model_ar", "") if audio_url else "",
            "timing": {"request_id": request_id, "quick_gpt_sec": gpt_sec, "first_tts_sec": first_tts_sec, "total_until_first_audio_sec": elapsed(total_t0)}
        })
    except Exception as e:
        log_event("error", f"Chat error {request_id}: {e}")
        return jsonify({"ok": False, "answer": "", "error": str(e), "tts_provider": "promobot", "audio_url": "", "request_id": request_id, "has_more_audio": False}), 500


@app.route("/api/next_audio", methods=["GET"])
def next_audio():
    request_id = request.args.get("request_id", "").strip()
    part = int(request.args.get("part", "2") or "2")
    config = load_active_config()
    timeout_sec = int(config.get("audio_wait_timeout_sec", 45))
    if not request_id:
        return jsonify({"ok": False, "has_audio": False, "error": "Missing request_id"}), 400
    start = time.perf_counter()
    job = get_job(request_id)
    while job and job.get("status") == "running" and elapsed(start) < timeout_sec:
        time.sleep(0.1)
        job = get_job(request_id)
    if not job:
        return jsonify({"ok": True, "has_audio": False, "has_more_audio": False, "part": part, "message": "No detail job found"})
    if job.get("status") == "error":
        return jsonify({"ok": False, "has_audio": False, "has_more_audio": False, "part": part, "error": job.get("error", "Detail job error")}), 500
    if job.get("status") != "ready":
        return jsonify({"ok": True, "has_audio": False, "has_more_audio": False, "part": part, "message": "Detail audio is not ready"})
    parts = job.get("parts", []) or []
    match = next((p for p in parts if int(p.get("part", 0)) == part), None)
    if not match:
        return jsonify({"ok": True, "has_audio": False, "has_more_audio": False, "part": part, "message": "No more audio parts"})
    has_more = any(int(p.get("part", 0)) > part for p in parts)
    return jsonify({"ok": True, "has_audio": True, "has_more_audio": has_more, "part": part, "text": match.get("text", ""), "audio_url": match.get("audio_url", "")})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = env_bool("FLASK_DEBUG", False)
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
