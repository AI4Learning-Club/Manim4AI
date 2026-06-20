"""
火山引擎豆包大模型 TTS（OpenSpeech v3 WebSocket 双向流式）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import websockets
from volcengine_audio.protocol import EventReceive
from volcengine_audio.tts import VolcengineTTSFunctions

logger = logging.getLogger(__name__)

DOUBAO_TTS_WSS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
_RECV_TIMEOUT_S = 20.0
_TOTAL_TIMEOUT_S = 60.0


def _safe_extract(data: bytes) -> tuple[Any, str, Any] | None:
    try:
        return VolcengineTTSFunctions.extract_response_payload(data)
    except Exception as exc:
        logger.debug("doubao_tts: parse frame failed: %s", exc)
        return None


def _event_code(event: Any) -> int:
    if hasattr(event, "value"):
        return int(event.value)
    return int(event)


def _format_server_error_maybe(code: int, sid: Any, payload: Any) -> str:
    if code < 45_000_000:
        return ""
    sid_msg = sid if isinstance(sid, str) else ""
    payload_msg = ""
    if isinstance(payload, dict):
        payload_msg = str(payload)
    elif isinstance(payload, (bytes, bytearray)) and payload:
        try:
            payload_msg = bytes(payload).decode("utf-8", errors="replace")
        except Exception:
            payload_msg = repr(payload[:80])
    detail = sid_msg.strip() or payload_msg.strip()
    return detail


async def _synthesize_async(
    *,
    text: str,
    output_path: Path,
    app_id: str,
    access_token: str,
    resource_id: str,
    speaker: str,
    audio_format: str,
    sample_rate: int,
    speech_rate: int,
) -> None:
    if not text.strip():
        raise ValueError("empty text for Doubao TTS")

    connect_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    headers = [
        ("X-Api-App-Key", app_id),
        ("X-Api-Access-Key", access_token),
        ("X-Api-Resource-Id", resource_id),
        ("X-Api-Connect-Id", connect_id),
    ]

    audio_params: dict[str, Any] = {
        "format": audio_format,
        "sample_rate": sample_rate,
        "speech_rate": speech_rate,
    }

    start_req_params: dict[str, Any] = {
        "speaker": speaker,
        "audio_params": audio_params,
    }

    async with websockets.connect(
        DOUBAO_TTS_WSS_URL,
        additional_headers=headers,
        max_size=None,
        open_timeout=12.0,
        close_timeout=5.0,
        ping_timeout=10.0,
    ) as ws:
        await ws.send(VolcengineTTSFunctions.start_connection_payload())

        connection_ok = False
        session_started = False
        audio_chunks: list[bytes] = []

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
            except TimeoutError as exc:
                raise RuntimeError(
                    f"Doubao TTS recv timeout ({_RECV_TIMEOUT_S:.0f}s): no server frame received"
                ) from exc
            if isinstance(raw, str):
                logger.warning("doubao_tts: unexpected text frame: %s", raw[:200])
                continue

            parsed = _safe_extract(raw)
            if parsed is None:
                continue

            event, sid, payload = parsed
            code = _event_code(event)
            err_detail = _format_server_error_maybe(code, sid, payload)
            if err_detail:
                raise RuntimeError(f"Doubao TTS server error {code}: {err_detail}")

            if code == EventReceive.ConnectionStarted.value:
                connection_ok = True
                await ws.send(
                    VolcengineTTSFunctions.start_session_payload(
                        session_id,
                        start_req_params,
                        user_info={"uid": "ai4learning"},
                    )
                )
                continue

            if code == EventReceive.ConnectionFailed.value:
                raise RuntimeError(f"Doubao TTS ConnectionFailed: {payload}")

            if code == EventReceive.SessionStarted.value:
                session_started = True
                await ws.send(
                    VolcengineTTSFunctions.task_request_payload(
                        session_id,
                        text,
                        speaker,
                        audio_params,
                    )
                )
                await ws.send(VolcengineTTSFunctions.finish_session_payload(session_id))
                continue

            if code == EventReceive.TTSResponse.value or code == 352:
                if isinstance(payload, (bytes, bytearray)) and payload:
                    audio_chunks.append(bytes(payload))
                continue

            if code in (
                EventReceive.TTSSentenceStart.value,
                EventReceive.TTSSentenceEnd.value,
            ):
                continue

            if code == EventReceive.SessionFinished.value:
                if isinstance(payload, dict):
                    sc = payload.get("status_code")
                    if sc is not None and sc != 20000000:
                        msg = payload.get("message", "")
                        raise RuntimeError(f"Doubao TTS SessionFinished error: {sc} {msg}")
                await ws.send(VolcengineTTSFunctions.finish_connection_payload())
                break

            if code == EventReceive.SessionFailed.value:
                raise RuntimeError(f"Doubao TTS SessionFailed: {payload}")

            if code == EventReceive.SessionCanceled.value:
                raise RuntimeError(f"Doubao TTS SessionCanceled: {payload}")

            if code == EventReceive.ConnectionFinished.value:
                break

            logger.debug("doubao_tts: unhandled event %s sid=%s", code, sid)

        if not connection_ok:
            raise RuntimeError("Doubao TTS: no ConnectionStarted")
        if not session_started:
            raise RuntimeError("Doubao TTS: no SessionStarted")
        if not audio_chunks:
            raise RuntimeError("Doubao TTS: no audio received (TTSResponse empty)")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"".join(audio_chunks))


def synthesize_doubao_mp3(
    text: str,
    output_path: Path,
    *,
    app_id: str,
    access_token: str,
    resource_id: str,
    speaker: str,
    audio_format: str = "mp3",
    sample_rate: int = 24000,
    speech_rate: int = 0,
) -> None:
    async def _run() -> None:
        await asyncio.wait_for(
            _synthesize_async(
                text=text,
                output_path=output_path,
                app_id=app_id,
                access_token=access_token,
                resource_id=resource_id,
                speaker=speaker,
                audio_format=audio_format,
                sample_rate=sample_rate,
                speech_rate=speech_rate,
            ),
            timeout=_TOTAL_TIMEOUT_S,
        )

    asyncio.run(_run())
