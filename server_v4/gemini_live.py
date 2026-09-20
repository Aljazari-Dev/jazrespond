from __future__ import annotations

import asyncio
import base64
import json
import os
import requests
from typing import Awaitable, Callable, Dict, Optional

import websockets

from .context import build_system_instruction

GEMINI_WS = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


class GeminiLiveSession:
    """One Gemini Live session owned by one connected robot.

    This class never touches ROS or robot hardware. It only talks to Gemini and
    calls the supplied robot callbacks. That separation is intentional: the
    server owns AI state, while the robot application remains the sole physical
    executor.
    """

    def __init__(
        self,
        robot_id: str,
        get_config: Callable[[], Dict],
        route_command: Callable[[str], Awaitable[Dict]],
        on_audio: Callable[[bytes], Awaitable[None]],
        on_json: Callable[[dict], Awaitable[None]],
        on_speaking: Callable[[bool], Awaitable[None]],
        on_activity: Callable[[], None],
    ) -> None:
        self.robot_id = robot_id
        self.get_config = get_config
        self.route_command = route_command
        self.on_audio = on_audio
        self.on_json = on_json
        self.on_speaking = on_speaking
        self.on_activity = on_activity

        self.model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview").strip()
        self.voice = os.getenv("GEMINI_VOICE_NAME", "").strip()
        self.silence_ms = int(os.getenv("GEMINI_VAD_SILENCE_MS", "180"))
        self.prefix_ms = int(os.getenv("GEMINI_VAD_PREFIX_MS", "80"))
        self.manual_vad = os.getenv("GEMINI_MANUAL_VAD", "true").strip().lower() in {"1", "true", "yes", "on"}
        self.tts_model = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview").strip()
        self.tts_voice = os.getenv("GEMINI_TTS_VOICE_NAME", self.voice or "Orus").strip()

        self.language = "ar"
        self.wanted = False
        self.manual_muted = False
        self.paused = False
        self._task: Optional[asyncio.Task] = None
        self._ws = None
        self._send_lock = asyncio.Lock()
        self._suppress_audio_until_turn_end = False
        self._pending_say = []
        self._ready = asyncio.Event()
        self._manual_activity_active = False
        self._tool_call_pending = False

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    async def start(self, language: str) -> None:
        changed = language != self.language
        self.language = language
        self.wanted = True
        if self._task and not self._task.done():
            if changed:
                await self.restart("language change")
            return
        self._task = asyncio.create_task(self._supervisor(), name="gemini-%s" % self.robot_id)

    async def stop(self) -> None:
        self.wanted = False
        self._ready.clear()
        self._manual_activity_active = False
        task = self._task
        self._task = None
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await self.on_speaking(False)

    async def restart(self, reason: str = "restart") -> None:
        if not self.wanted:
            return
        await self.on_json({"type": "gemini_state", "state": "restarting", "reason": reason})
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def set_muted(self, muted: bool) -> None:
        self.manual_muted = bool(muted)
        if muted:
            await self.interrupt()

    async def set_paused(self, paused: bool) -> None:
        self.paused = bool(paused)
        if paused:
            await self.interrupt()

    async def interrupt(self) -> None:
        self._suppress_audio_until_turn_end = True
        await self.on_json({"type": "audio_interrupt"})
        await self.on_speaking(False)

    async def send_audio(self, pcm: bytes) -> None:
        if not pcm or not self.wanted or self.manual_muted or self.paused or self._tool_call_pending:
            return
        ws = self._ws
        if ws is None or not self._ready.is_set():
            return
        payload = {
            "realtimeInput": {
                "audio": {
                    "data": base64.b64encode(pcm).decode("ascii"),
                    "mimeType": "audio/pcm;rate=16000",
                }
            }
        }
        try:
            await self._send(payload)
        except Exception:
            # The supervisor owns reconnection. Dropping one mic frame is safer
            # than blocking the robot audio thread while the socket rotates.
            return


    async def set_user_activity(self, active: bool) -> None:
        """Forward bridge-side VAD boundaries to Gemini when manual VAD is enabled."""
        active = bool(active)
        if not self.manual_vad or self._tool_call_pending:
            return
        if self._ws is None or not self._ready.is_set() or self.manual_muted or self.paused:
            self._manual_activity_active = False if not active else self._manual_activity_active
            return
        if active == self._manual_activity_active:
            return
        self._manual_activity_active = active
        payload = {"realtimeInput": {"activityStart": {}}} if active else {"realtimeInput": {"activityEnd": {}}}
        try:
            await self._send(payload)
        except Exception:
            if active:
                self._manual_activity_active = False

    async def audio_stream_end(self) -> None:
        if self._ws is None or not self._ready.is_set() or self._tool_call_pending:
            return
        try:
            if self.manual_vad:
                if self._manual_activity_active:
                    self._manual_activity_active = False
                    await self._send({"realtimeInput": {"activityEnd": {}}})
            else:
                await self._send({"realtimeInput": {"audioStreamEnd": True}})
        except Exception:
            pass

    async def speak_event(self, text: str) -> None:
        """Speak fixed robot/app text with Gemini TTS, not the Live turn.

        Fixed events (face greeting, remote reply, configured reply_text) must not
        enter the Live conversation or its function-calling state.  Keeping them
        on a separate TTS request prevents event speech from colliding with a
        pending synchronous Live tool call and preserves the exact configured
        text.
        """
        text = (text or "").strip()
        if not text or self.manual_muted or self.paused:
            return
        try:
            pcm = await asyncio.to_thread(self._generate_event_tts_pcm, text)
        except Exception as exc:
            await self.on_json({
                "type": "event_tts_error",
                "message": str(exc),
            })
            return
        if not pcm or self.manual_muted or self.paused:
            return
        self.on_activity()
        await self.on_speaking(True)
        # 100 ms chunks at 24 kHz mono signed 16-bit PCM.
        chunk_bytes = 4800
        for pos in range(0, len(pcm), chunk_bytes):
            if self.manual_muted or self.paused:
                break
            await self.on_audio(pcm[pos:pos + chunk_bytes])
        await self.on_json({"type": "output_transcript", "text": text})
        await self.on_json({"type": "audio_turn_end"})
        await self.on_speaking(False)

    def _generate_event_tts_pcm(self, text: str) -> bytes:
        """Generate fixed robot-event speech through the current Interactions TTS API.

        Gemini 3.1 TTS can occasionally reject vague/raw transcript prompts.  The
        request therefore uses an explicit synthesis preamble and labels the exact
        transcript.  The current Interactions API is used instead of the legacy
        GenerateContent path, and transient/no-audio responses are retried.
        """
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        if not self.tts_model:
            raise RuntimeError("GEMINI_TTS_MODEL is empty")

        # Explicit wording is intentional. Gemini 3.1 TTS documents that vague
        # prompts may fail its speech-synthesis classifier. Keep the transcript
        # clearly separated so configured robot text stays verbatim.
        tts_input = (
            "Synthesize speech for the transcript below. "
            "Speak ONLY the transcript, exactly as written. "
            "Do not add, remove, translate, explain, or repeat anything.\n"
            "TRANSCRIPT START\n"
            + text
            + "\nTRANSCRIPT END"
        )

        url = "https://generativelanguage.googleapis.com/v1beta/interactions"
        headers = {
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
            "Api-Revision": "2026-05-20",
        }
        speech_entry = {"voice": self.tts_voice or "Orus"}
        payload = {
            "model": self.tts_model,
            "input": tts_input,
            "response_format": {
                "type": "audio"
            },
            "generation_config": {
                "speech_config": [speech_entry]
            },
        }

        last_error = "unknown TTS error"
        for attempt in range(1, 4):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(5, 35),
                )
            except Exception as exc:
                last_error = "Gemini TTS request failed: %s" % exc
                if attempt < 3:
                    continue
                raise RuntimeError(last_error)

            body_preview = (response.text or "")[:1200]
            if response.status_code >= 400:
                last_error = "Gemini TTS HTTP %s: %s" % (
                    response.status_code,
                    body_preview,
                )
                # Retry transient service/rate-limit responses only.
                if attempt < 3 and response.status_code in {429, 500, 502, 503, 504}:
                    continue
                raise RuntimeError(last_error)

            try:
                data = response.json()
            except Exception:
                last_error = "Gemini TTS returned invalid JSON: %s" % body_preview
                if attempt < 3:
                    continue
                raise RuntimeError(last_error)

            # Current Interactions API exposes the convenience audio block at
            # top-level `output_audio`. Prefer that exact documented shape.
            output_audio = data.get("output_audio") or data.get("outputAudio") or {}
            encoded = output_audio.get("data") if isinstance(output_audio, dict) else None
            if encoded:
                try:
                    pcm = base64.b64decode(encoded)
                    if pcm:
                        return pcm
                except Exception:
                    pass

            audio_blocks = []

            # Compatibility fallback: walk the response recursively in case
            # Google wraps the audio block differently in a future revision.
            # model-output step content. Walk recursively as a compatibility
            # guard in case Google adds another wrapper around audio content.
            def collect_audio(value):
                if isinstance(value, dict):
                    if value.get("type") == "audio" and value.get("data"):
                        audio_blocks.append(value)
                    for child in value.values():
                        collect_audio(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_audio(child)

            collect_audio(data)

            pcm_parts = []
            for block in audio_blocks:
                encoded = block.get("data")
                if not encoded:
                    continue
                try:
                    pcm_parts.append(base64.b64decode(encoded))
                except Exception:
                    continue

            if pcm_parts:
                return b"".join(pcm_parts)

            status = str(data.get("status") or "")
            last_error = (
                "Gemini TTS returned no audio | status=%s | response=%s"
                % (status or "unknown", json.dumps(data, ensure_ascii=False)[:1200])
            )
            # Gemini 3.1 TTS documentation notes rare no-audio/text-token
            # responses. Retry a clean request before surfacing the failure.
            if attempt < 3:
                continue

        raise RuntimeError(last_error)

    async def _send(self, payload: dict) -> None:
        ws = self._ws
        if ws is None:
            raise RuntimeError("Gemini socket is not connected")
        async with self._send_lock:
            await ws.send(json.dumps(payload, ensure_ascii=False))

    async def _supervisor(self) -> None:
        delay = 0.6
        try:
            while self.wanted:
                try:
                    await self._run_once()
                    delay = 0.6
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self.on_json({"type": "gemini_state", "state": "error", "message": str(exc)})
                finally:
                    self._ready.clear()
                    self._ws = None
                    await self.on_speaking(False)
                if self.wanted:
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.7, 8.0)
        finally:
            self._ready.clear()
            self._ws = None

    async def _run_once(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        config = self.get_config()
        instruction = build_system_instruction(config, self.language)
        url = GEMINI_WS + "?key=" + api_key
        await self.on_json({"type": "gemini_state", "state": "connecting", "language": self.language})

        async with websockets.connect(
            url,
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            generation_config = {"responseModalities": ["AUDIO"]}
            if self.voice:
                generation_config["speechConfig"] = {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self.voice}}
                }
            realtime_input_config = {
                "automaticActivityDetection": ({"disabled": True} if self.manual_vad else {
                    "disabled": False,
                    "prefixPaddingMs": self.prefix_ms,
                    "silenceDurationMs": self.silence_ms,
                }),
                # Gemini 3.1 defaults differ from 2.5.  Explicitly keep only
                # marked speech activity in the user's turn so room silence/noise
                # cannot contaminate turn formation.
                "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",
                "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
            }
            setup = {
                "setup": {
                    "model": "models/" + self.model,
                    "generationConfig": generation_config,
                    "systemInstruction": {"parts": [{"text": instruction}]},
                    "realtimeInputConfig": realtime_input_config,
                    "inputAudioTranscription": {},
                    "outputAudioTranscription": {},
                    "tools": [{
                        "functionDeclarations": [{
                            "name": "route_promobot_utterance",
                            "description": (
                                "Mandatory first routing step for every normal spoken user turn. "
                                "Send the user's exact utterance to the Promobot command matcher "
                                "before deciding whether to answer normally."
                            ),
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string", "description": "Exact user utterance"}
                                },
                                "required": ["text"],
                            },
                        }]
                    }],
                }
            }
            await ws.send(json.dumps(setup, ensure_ascii=False))
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            reply = json.loads(raw)
            if "error" in reply:
                raise RuntimeError("Gemini setup error: %s" % reply["error"])
            if "setupComplete" not in reply:
                raise RuntimeError("Gemini setup did not complete: %s" % reply)

            self._manual_activity_active = False
            self._ready.set()
            await self.on_json({
                "type": "gemini_state",
                "state": "connected",
                "language": self.language,
                "model": self.model,
            })

            pending = list(self._pending_say)
            self._pending_say = []
            for text in pending:
                await self.speak_event(text)

            heard_text = ""
            said_text = ""

            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                data = json.loads(raw)
                if "error" in data:
                    raise RuntimeError("Gemini error: %s" % data["error"])
                if "goAway" in data:
                    await self.on_json({"type": "gemini_state", "state": "rotating"})
                    return
                if "toolCall" in data:
                    await self._handle_tool_call(data.get("toolCall") or {})
                    continue
                if "toolCallCancellation" in data:
                    await self.on_json({"type": "tool_call_cancelled", "detail": data.get("toolCallCancellation")})
                    continue

                content = data.get("serverContent") or {}
                tx = content.get("inputTranscription") or {}
                if tx.get("text"):
                    heard_text += tx["text"]
                    await self.on_json({
                        "type": "input_transcript_partial",
                        "text": heard_text.strip(),
                    })
                tx = content.get("outputTranscription") or {}
                if tx.get("text"):
                    said_text += tx["text"]
                    await self.on_json({
                        "type": "output_transcript_partial",
                        "text": said_text.strip(),
                    })

                if not (self.manual_muted or self.paused or self._suppress_audio_until_turn_end):
                    for part in (content.get("modelTurn") or {}).get("parts", []):
                        inline = part.get("inlineData") or {}
                        encoded = inline.get("data")
                        if encoded:
                            pcm = base64.b64decode(encoded)
                            if pcm:
                                await self.on_speaking(True)
                                await self.on_audio(pcm)

                if content.get("interrupted"):
                    self._suppress_audio_until_turn_end = True
                    await self.on_json({"type": "audio_interrupt"})
                    await self.on_speaking(False)

                if content.get("turnComplete"):
                    await self.on_json({"type": "audio_turn_end"})
                    await self.on_speaking(False)
                    heard_clean = heard_text.strip()
                    said_clean = said_text.strip()
                    if heard_clean:
                        self.on_activity()
                        await self.on_json({"type": "input_transcript", "text": heard_clean})
                    if said_clean:
                        self.on_activity()
                        await self.on_json({"type": "output_transcript", "text": said_clean})
                    heard_text = ""
                    said_text = ""
                    self._suppress_audio_until_turn_end = bool(self.manual_muted or self.paused)

    async def _handle_tool_call(self, tool_call: dict) -> None:
        # Gemini 3.1 Live function calling is synchronous.  While a tool call is
        # pending, do not send realtime audio/activity frames to the same Live
        # session.  Sending realtimeInput concurrently with a pending tool call
        # can close the Gemini socket with WebSocket 1008.
        self._tool_call_pending = True
        await self.on_json({"type": "tool_state", "state": "pending"})
        try:
            calls = (tool_call or {}).get("functionCalls") or []
            responses = []
            for call in calls:
                call_id = call.get("id") or ""
                name = call.get("name") or ""
                args = call.get("args") or {}
                if name == "route_promobot_utterance":
                    result = await self.route_command(args.get("text") or "")
                else:
                    result = {"ok": False, "matched": False, "error": "Unknown tool: " + name}
                item = {"name": name, "response": result}
                if call_id:
                    item["id"] = call_id
                responses.append(item)
            await self._send({"toolResponse": {"functionResponses": responses}})
        finally:
            self._tool_call_pending = False
            await self.on_json({"type": "tool_state", "state": "idle"})

