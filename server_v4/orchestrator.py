from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import wave
from array import array
from pathlib import Path
from typing import Any, Dict

import app as legacy

from .gemini_live import GeminiLiveSession
from .protocol import normalize_ai_mode, normalize_language
from .session import RobotSessionState


class RobotOrchestrator:
    def __init__(self, session: RobotSessionState) -> None:
        self.s = session
        self.face_idle_guard_sec = float(os.getenv("FACE_GREETING_IDLE_GUARD_SEC", "5.0"))
        self.audio_diag_enabled = os.getenv("AUDIO_DIAGNOSTICS", "false").strip().lower() in {"1", "true", "yes", "on"}
        self.audio_diag_max_bytes = int(os.getenv("AUDIO_DIAGNOSTICS_MAX_BYTES", str(16000 * 2 * 12)))
        data_dir = Path(os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))).expanduser()
        self.audio_diag_dir = data_dir / "diagnostics"
        self.audio_diag_buffer = bytearray()
        self.audio_diag_frames = 0
        self.audio_diag_started_at = 0.0
        self.audio_diag_sum_sq = 0
        self.audio_diag_samples = 0
        self.audio_diag_peak = 0
        self.s.gemini = GeminiLiveSession(
            robot_id=self.s.robot_id,
            get_config=legacy.load_active_config,
            route_command=self._route_voice_command,
            on_audio=self.s.send_audio,
            on_json=self.s.send_json,
            on_speaking=self._set_assistant_speaking,
            on_activity=self.s.touch,
        )

    async def start(self) -> None:
        self.s.remote_task = asyncio.create_task(self._remote_loop(), name="remote-%s" % self.s.robot_id)
        await self.s.send_json({
            "type": "status",
            "state": "connected",
            "robot_id": self.s.robot_id,
            "ai_mode": "off",
            "language": self.s.language,
        })

    async def close(self) -> None:
        task = self.s.remote_task
        self.s.remote_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        if self.s.gemini:
            await self.s.gemini.stop()

    async def handle_binary(self, pcm: bytes) -> None:
        if not self.s.app_active or not self.s.ai_enabled or self.s.muted or self.s.paused:
            return
        if self.audio_diag_enabled and self.s.user_speaking:
            self._audio_diag_add(pcm)
        await self.s.gemini.send_audio(pcm)

    async def handle_json(self, msg: Dict[str, Any]) -> None:
        kind = (msg.get("type") or "").strip().lower()
        if kind == "hello":
            self.s.app_active = bool(msg.get("app_active", self.s.app_active))
            self.s.language = normalize_language(msg.get("language") or self.s.language)
            await self._publish_status()
            return
        if kind == "app_start":
            self.s.app_active = True
            self.s.touch()
            await self._publish_status()
            return
        if kind == "app_stop":
            self.s.app_active = False
            self.s.ai_enabled = False
            await self.s.gemini.stop()
            await self._publish_status()
            return
        if kind == "set_mode":
            await self._set_mode(normalize_ai_mode(msg.get("ai_mode")), msg.get("language"))
            return
        if kind == "set_language":
            await self._set_language(msg.get("language"))
            return
        if kind == "mute":
            self.s.muted = True
            await self.s.gemini.set_muted(True)
            await self._publish_status()
            return
        if kind == "unmute":
            self.s.muted = False
            await self.s.gemini.set_muted(False)
            await self._publish_status()
            return
        if kind == "pause":
            self.s.paused = True
            self.s.touch()
            await self.s.gemini.set_paused(True)
            await self.s.send_json({"type": "audio_interrupt"})
            await self._publish_status()
            return
        if kind == "resume":
            self.s.paused = False
            await self.s.gemini.set_paused(False)
            await self._publish_status()
            return
        if kind == "interrupt":
            self.s.touch()
            await self.s.gemini.interrupt()
            return
        if kind == "sync":
            if self.s.ai_enabled:
                await self.s.gemini.restart("runtime sync")
            await self.s.send_json({"type": "sync_ack"})
            return
        if kind == "say":
            await self._handle_say(msg)
            return
        if kind == "face_detected":
            await self._handle_face(msg)
            return
        if kind == "user_activity":
            active = bool(msg.get("active"))
            if active and not self.s.user_speaking and self.audio_diag_enabled:
                self._audio_diag_start()
            self.s.user_speaking = active
            if active:
                self.s.touch()
            if self.s.ai_enabled:
                await self.s.gemini.set_user_activity(active)
            await self.s.send_json({
                "type": "vad_state",
                "active": active,
                "mode": "manual" if self.s.gemini.manual_vad else "automatic",
            })
            if not active and self.audio_diag_enabled and self.audio_diag_started_at:
                await self._audio_diag_finish()
            return
        if kind == "speaker_state":
            self.s.assistant_speaking = bool(msg.get("speaking"))
            if self.s.assistant_speaking:
                self.s.touch()
            return
        if kind == "action_state":
            self.s.action_running = bool(msg.get("running"))
            if self.s.action_running:
                self.s.touch()
            return
        if kind == "audio_stream_end":
            await self.s.gemini.audio_stream_end()
            return

    def _audio_diag_start(self) -> None:
        self.audio_diag_buffer = bytearray()
        self.audio_diag_frames = 0
        self.audio_diag_started_at = time.monotonic()
        self.audio_diag_sum_sq = 0
        self.audio_diag_samples = 0
        self.audio_diag_peak = 0
        legacy.log_event("audio_debug", "PCM capture started", {"robot_id": self.s.robot_id})

    def _audio_diag_add(self, pcm: bytes) -> None:
        if not pcm or not self.audio_diag_started_at:
            return
        remaining = max(0, self.audio_diag_max_bytes - len(self.audio_diag_buffer))
        if remaining:
            self.audio_diag_buffer.extend(pcm[:remaining])
        self.audio_diag_frames += 1
        usable = len(pcm) - (len(pcm) % 2)
        if usable <= 0:
            return
        samples = array("h")
        samples.frombytes(pcm[:usable])
        if sys.byteorder != "little":
            samples.byteswap()
        for sample in samples:
            value = int(sample)
            absolute = -value if value < 0 else value
            if absolute > self.audio_diag_peak:
                self.audio_diag_peak = absolute
            self.audio_diag_sum_sq += value * value
        self.audio_diag_samples += len(samples)

    def _audio_diag_paths(self):
        safe_robot = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.s.robot_id or "robot")
        self.audio_diag_dir.mkdir(parents=True, exist_ok=True)
        return (
            self.audio_diag_dir / (safe_robot + "_latest.wav"),
            self.audio_diag_dir / (safe_robot + "_latest.json"),
        )

    async def _audio_diag_finish(self) -> None:
        started = self.audio_diag_started_at
        self.audio_diag_started_at = 0.0
        pcm = bytes(self.audio_diag_buffer)
        self.audio_diag_buffer = bytearray()
        duration_ms = int((len(pcm) / float(16000 * 2)) * 1000.0) if pcm else 0
        rms = int((self.audio_diag_sum_sq / float(self.audio_diag_samples)) ** 0.5) if self.audio_diag_samples else 0
        peak = int(self.audio_diag_peak)
        frames = int(self.audio_diag_frames)
        wav_path, meta_path = self._audio_diag_paths()
        if pcm:
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(pcm)
        metadata = {
            "robot_id": self.s.robot_id,
            "bytes": len(pcm),
            "frames": frames,
            "duration_ms": duration_ms,
            "rms": rms,
            "peak": peak,
            "truncated": len(pcm) >= self.audio_diag_max_bytes,
            "wav_file": wav_path.name if pcm else "",
        }
        try:
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        legacy.log_event("audio_debug", "PCM capture finished", metadata)
        await self.s.send_json({"type": "audio_debug_turn", **metadata})

    async def _set_mode(self, enabled: bool, language: Any = None) -> None:
        if language:
            self.s.language = normalize_language(str(language))
        self.s.ai_enabled = bool(enabled)
        self.s.paused = False
        self.s.touch()
        if self.s.ai_enabled:
            await self.s.gemini.start(self.s.language)
            await self.s.gemini.set_muted(self.s.muted)
        else:
            await self.s.gemini.stop()
        await self._publish_status()

    async def _set_language(self, language: Any) -> None:
        new_lang = normalize_language(str(language or ""))
        if new_lang == self.s.language:
            return
        self.s.language = new_lang
        self.s.touch()
        if self.s.ai_enabled:
            await self.s.gemini.start(new_lang)
        await self._publish_status()

    async def _publish_status(self) -> None:
        state = "muted" if self.s.muted else ("paused" if self.s.paused else "connected")
        await self.s.send_json({
            "type": "status",
            "state": state,
            "robot_id": self.s.robot_id,
            "ai_mode": "on" if self.s.ai_enabled else "off",
            "language": self.s.language,
        })

    async def _set_assistant_speaking(self, speaking: bool) -> None:
        speaking = bool(speaking)
        if self.s.assistant_speaking == speaking:
            return
        self.s.assistant_speaking = speaking
        if speaking:
            self.s.touch()

    async def _route_voice_command(self, text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": True, "matched": False, "message": "Empty utterance"}
        match = legacy.find_matching_command(text, self.s.language)
        if not match:
            return {"ok": True, "matched": False}
        command = legacy.public_command(match)
        self.s.touch()
        await self.s.send_json({"type": "command", "source": "voice", "command": command})
        action_type = (command.get("action_type") or "").strip().lower()
        action_value = (command.get("action_value") or "").strip().lower()
        mute_value = (command.get("mute_state") or action_value).strip().lower()
        muting_command = action_type == "mute" and mute_value in {
            "active", "activate", "on", "true", "1", "mute", "mute_active"
        }
        native_tts_action = action_type in {"promobot_tts", "tts", "say"}
        reply_text = command.get("reply_text") or ""
        return {
            "ok": True,
            "matched": True,
            "command_name": command.get("name") or "",
            "action_type": action_type,
            "reply_text": reply_text,
            "speak_reply": bool(reply_text) and not muting_command and not native_tts_action,
        }

    async def _handle_say(self, msg: Dict[str, Any]) -> None:
        text = (msg.get("text") or "").strip()
        if not text or self.s.muted or self.s.paused:
            return
        source = (msg.get("source") or "app").strip().lower()
        if source == "face" and self.s.face_greeting_blocked(self.face_idle_guard_sec):
            legacy.log_event("face", "Face speech dropped by busy guard", {"robot_id": self.s.robot_id})
            return
        self.s.touch()
        if self.s.ai_enabled:
            await self.s.gemini.speak_event(text)
        else:
            await self.s.send_json({"type": "native_say", "text": text})

    async def _handle_face(self, msg: Dict[str, Any]) -> None:
        if not self.s.app_active or self.s.muted:
            return
        if self.s.face_greeting_blocked(self.face_idle_guard_sec):
            legacy.log_event("face", "Face greeting dropped by server busy guard", {
                "robot_id": self.s.robot_id,
                "username": msg.get("username") or "",
            })
            return
        username = (msg.get("username") or "").strip()
        recognition_type = (msg.get("recognition_type") or ("known" if username else "unknown")).strip().lower()
        match = legacy.find_matching_face_greeting(username, self.s.language, recognition_type)
        if not match:
            return
        greeting = legacy.public_face_greeting(match)
        cooldown_sec = int(greeting.get("cooldown_sec") or 60)
        face_key = "__unknown_face__" if recognition_type == "unknown" else (username.lower() or str(greeting.get("id") or "face"))
        now = __import__("time").monotonic()
        last = self.s.face_last.get(face_key, 0.0)
        if last and (now - last) < cooldown_sec:
            return
        self.s.face_last[face_key] = now

        command = {
            "name": "Face Greeting",
            "action_type": greeting.get("action_type") or "none",
            "action_value": greeting.get("action_value") or "",
            "reply_text": greeting.get("response_text") or "",
        }
        self.s.touch()
        await self.s.send_json({"type": "command", "source": "face", "command": command})
        if self.s.ai_enabled and command["reply_text"]:
            await self.s.gemini.speak_event(command["reply_text"])

    async def _remote_loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            if not self.s.app_active:
                continue
            item = legacy.pop_remote_command(self.s.robot_id)
            if not item:
                continue
            command = legacy.public_command(item.get("command") or {})
            self.s.touch()
            await self.s.send_json({"type": "command", "source": "remote", "command": command})
            if self.s.ai_enabled:
                reply_text = (command.get("reply_text") or "").strip()
                action_type = (command.get("action_type") or "").strip().lower()
                if reply_text and action_type not in {"mute", "promobot_tts", "tts", "say"}:
                    await self.s.gemini.speak_event(reply_text)
