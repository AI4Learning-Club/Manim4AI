from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from mcp.types import TextContent, Tool

from config.logger_config import get_mcp_logger

_logger = get_mcp_logger("manim_http")

_PUBLIC_REDACTED_TEXT = "[internal path redacted]"
_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![\w/])/(?:Users|private|tmp|var|Volumes|home|mnt|opt)/[^\s,;:'\")\]}]+"),
    re.compile(r"\b[A-Za-z]:[\\/][^\s,;:'\")\]}]+"),
)

RenderTeachingVideo = Callable[..., dict[str, Any]]
SubmitTeachingVideoJob = Callable[..., dict[str, Any]]
GetTeachingVideoJobStatus = Callable[..., dict[str, Any]]


def _build_render_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "request": {
                "type": "string",
                "description": (
                    "Teaching request describing what animation to generate and explain. "
                    "For problem-solving videos, include that the video should first restate/analyze the problem and visually mark key givens, target, variables, or diagram relations."
                ),
            },
            "language": {
                "type": "string",
                "enum": ["en", "zh"],
                "description": "Output language for narration and on-screen explanation.",
            },
            "quality": {
                "type": "string",
                "enum": ["default", "draft", "medium", "high"],
                "description": "Render quality preset. default/medium use settings.toml defaults.",
            },
            "render_backend": {
                "type": "string",
                "enum": ["manim", "hybrid"],
                "description": "Delivery backend. hybrid wraps the final Manim video with Remotion.",
            },
            "conversation_id": {
                "type": "string",
                "description": "Conversation id for backend-side run isolation and authenticated preview access. Returned media URLs may still require this conversation's auth context.",
            },
            "stream_mode": {
                "type": "boolean",
                "description": "When true, only create an async render job and return job/status metadata immediately. This does not mean the final video is ready.",
            },
            "flash": {
                "type": "boolean",
                "description": "When true, use the fast generation model; when false, use the deeper generation model.",
            },
            "idempotency_key": {
                "type": "string",
                "description": "Optional backend idempotency key for replay-safe async job submission.",
            },
        },
        "required": ["request", "conversation_id"],
    }


def _build_job_status_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "job_id": {
                "type": "string",
                "description": "The async Manim render job id returned by render_teaching_video when stream_mode=true. Use it only to poll job progress and completion.",
            }
        },
        "required": ["job_id"],
    }


def _normalize_render_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        "request",
        "language",
        "quality",
        "render_backend",
        "conversation_id",
        "stream_mode",
        "flash",
        "idempotency_key",
    }
    unexpected_keys = sorted(key for key in arguments if key not in allowed_keys)
    if unexpected_keys:
        raise ValueError(f"Unsupported render_teaching_video fields: {', '.join(unexpected_keys)}")

    request = str(arguments.get("request") or "").strip()
    if not request:
        raise ValueError("request is required")

    language = str(arguments.get("language") or "").strip().lower()
    if language and language not in {"en", "zh"}:
        raise ValueError("language must be 'en' or 'zh'")

    quality = str(arguments.get("quality") or "default").strip().lower() or "default"
    if quality not in {"default", "draft", "medium", "high"}:
        raise ValueError("quality must be one of: default, draft, medium, high")

    render_backend = str(arguments.get("render_backend") or "manim").strip().lower() or "manim"
    if render_backend not in {"manim", "hybrid"}:
        raise ValueError("render_backend must be 'manim' or 'hybrid'")

    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    stream_mode = arguments.get("stream_mode") is True
    flash = arguments.get("flash")
    if flash is None:
        flash = True
    elif not isinstance(flash, bool):
        raise ValueError("flash must be a boolean")
    idempotency_key = str(arguments.get("idempotency_key") or "").strip()
    return {
        "request": request,
        "language": language,
        "quality": quality,
        "render_backend": render_backend,
        "conversation_id": conversation_id,
        "stream_mode": stream_mode,
        "flash": flash,
        "idempotency_key": idempotency_key,
    }


def _normalize_job_status_payload(arguments: dict[str, Any]) -> dict[str, str]:
    allowed_keys = {"job_id"}
    unexpected_keys = sorted(key for key in arguments if key not in allowed_keys)
    if unexpected_keys:
        raise ValueError(f"Unsupported get_teaching_video_job_status fields: {', '.join(unexpected_keys)}")
    job_id = str(arguments.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("job_id is required")
    return {"job_id": job_id}


def _build_failure_payload(*, message: str) -> str:
    return json.dumps(
        {
            "status": "error",
            "preview_type": None,
            "message": _sanitize_public_error(message),
            "video_url": None,
            "file_name": None,
            "duration_seconds": None,
        },
        ensure_ascii=False,
    )


def _sanitize_public_error(message: object) -> str:
    redacted = str(message or "").strip() or "Manim render failed."
    for pattern in _ABSOLUTE_PATH_PATTERNS:
        redacted = pattern.sub(_PUBLIC_REDACTED_TEXT, redacted)
    return redacted


def build_tool_definitions() -> list[Tool]:
    return [
        Tool(
            name="render_teaching_video",
            description=(
                "Generate a narrated teaching animation video with Manim. "
                "For concrete problem-solving, the video should begin by restating/analyzing the problem and marking key information before solving. "
                "When stream_mode=true, it only creates an async job and returns job_id plus authenticated preview metadata for later polling. "
                "Do not treat preview_url/video_url/delivery_url as a finished public link in the assistant's natural-language reply."
            ),
            inputSchema=_build_render_schema(),
        ),
        Tool(
            name="get_teaching_video_job_status",
            description=(
                "Get the latest status for an async Manim teaching video render job. "
                "Use the returned status to distinguish queued/running tasks from a truly completed final video."
            ),
            inputSchema=_build_job_status_schema(),
        ),
    ]


async def handle_tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    render_teaching_video: RenderTeachingVideo,
    submit_teaching_video_job: SubmitTeachingVideoJob,
    get_teaching_video_job_status: GetTeachingVideoJobStatus,
) -> Sequence[TextContent]:
    if name == "render_teaching_video":
        try:
            payload = _normalize_render_payload(arguments)
            _logger.info(
                "[Manim][MCP] 收到 render_teaching_video 请求，开始执行 pipeline … backend=%s quality=%s stream_mode=%s",
                payload["render_backend"],
                payload["quality"],
                payload["stream_mode"],
            )
            if payload["stream_mode"]:
                result = await asyncio.to_thread(
                    submit_teaching_video_job,
                    request=payload["request"],
                    language=payload["language"],
                    quality=payload["quality"],
                    render_backend=payload["render_backend"],
                    conversation_id=payload["conversation_id"],
                    idempotency_key=payload["idempotency_key"],
                    flash=payload["flash"],
                )
            else:
                result = await asyncio.to_thread(
                    render_teaching_video,
                    request=payload["request"],
                    language=payload["language"],
                    quality=payload["quality"],
                    render_backend=payload["render_backend"],
                    conversation_id=payload["conversation_id"],
                    idempotency_key=payload["idempotency_key"],
                    flash=payload["flash"],
                )
            _logger.info("[Manim][MCP] render_teaching_video 完成: status=%s", result.get("status"))
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except Exception as exc:
            _logger.exception("[Manim][MCP] render_teaching_video 失败: %s", exc)
            return [TextContent(type="text", text=_build_failure_payload(message=f"Failed to generate Manim video: {exc}"))]

    if name == "get_teaching_video_job_status":
        try:
            payload = _normalize_job_status_payload(arguments)
            result = await asyncio.to_thread(
                get_teaching_video_job_status,
                job_id=payload["job_id"],
            )
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
        except KeyError as exc:
            return [TextContent(type="text", text=_build_failure_payload(message=str(exc)))]
        except Exception as exc:
            _logger.exception("[Manim][MCP] get_teaching_video_job_status 失败: %s", exc)
            return [TextContent(type="text", text=_build_failure_payload(message=f"Failed to get Manim job status: {exc}"))]

    return [TextContent(type="text", text=_build_failure_payload(message=f"Unknown tool: {name}"))]
