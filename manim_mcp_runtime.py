from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server import Server

from config.logger_config import get_mcp_logger
from config.settings import settings
from interface.plugins.manim import (
    ManimStreamEventStage,
    ManimStreamEventType,
    make_manim_stream_event,
)
from plugins.manim.agent_pipeline.main import run_pipeline
from plugins.manim.mcp_server import create_server
from plugins.manim.runtime_config import get_manim_runs_dir

_logger = get_mcp_logger("manim_http")

_job_semaphore: threading.BoundedSemaphore | None = None
_job_semaphore_cap: int = -1
_shared_renderer_runtime: "ManimRenderRuntime | None" = None


def _job_slot_semaphore() -> threading.BoundedSemaphore:
    """Process-wide limit for concurrent Manim pipeline runs (shared across runtime instances)."""
    global _job_semaphore, _job_semaphore_cap
    cap = max(1, settings.manim.max_concurrent_jobs)
    if _job_semaphore is None or cap != _job_semaphore_cap:
        _job_semaphore = threading.BoundedSemaphore(cap)
        _job_semaphore_cap = cap
    return _job_semaphore


SERVER_NAME = "manim-edu-agent"
SERVER_VERSION = "1.0.0"
_QUALITY_PRESETS = {
    "default": "",
    "draft": "-ql --fps 30",
    "medium": "",
    "high": "-qh --fps 60",
}
_PUBLIC_REDACTED_TEXT = "[internal path redacted]"
_PUBLIC_PATH_KEYS = frozenset(
    {
        "run_dir",
        "scene_file",
        "video_path",
        "output_dir",
        "preview_path",
        "final_video",
        "final_video_with_audio",
        "delivery_video",
        "analysis_stream",
        "code_stream_partial",
        "stream_timing",
        "stream_debug_files",
        "selected_assets_file",
        "selected_theme_file",
        "teaching_plan_file",
        "manim_teaching_plan_file",
        "path",
        "file_path",
        "log_file",
    }
)
_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![\w/])/(?:Users|home|private|tmp|var|Volumes|mnt|opt|workspace)/[^\s,;:'\")\]}]+"),
    re.compile(r"\b[A-Za-z]:[\\/][^\s,;:'\")\]}]+"),
)


def _new_run_key() -> str:
    return uuid.uuid4().hex[:8]


def _managed_video_name_for_run(run_key: str) -> str:
    normalized = str(run_key or "").strip()
    if not normalized:
        raise ValueError("run_key is required")
    return f"lesson_{normalized}.mp4"


def _publish_managed_video_atomically(source_path: Path, managed_path: Path) -> None:
    src = Path(source_path).resolve()
    dst = Path(managed_path).resolve()
    if not src.exists() or not src.is_file():
        raise FileNotFoundError(f"Source video is missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dst.with_name(f"{dst.name}.tmp")
    if tmp_path.exists():
        tmp_path.unlink()
    shutil.copy2(str(src), str(tmp_path))
    tmp_path.replace(dst)


def _managed_video_metadata_path(managed_path: Path) -> Path:
    path = Path(managed_path).resolve()
    return path.with_name(f"{path.name}.json")


def _write_managed_video_metadata(
    managed_path: Path,
    *,
    conversation_id: str,
    run_key: str,
    preview_version: int | None = None,
    final_version: int | None = None,
    is_final: bool,
) -> None:
    metadata_path = _managed_video_metadata_path(managed_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "conversation_id": conversation_id or "",
        "run_key": run_key,
        "preview_version": preview_version,
        "final_version": final_version,
        "is_final": is_final,
        "updated_at": time.time(),
    }
    tmp_path = metadata_path.with_name(f"{metadata_path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(metadata_path)


def _publish_managed_artifacts(
    source_path: Path,
    managed_path: Path,
    *,
    conversation_id: str,
    run_key: str,
    preview_version: int | None = None,
    final_version: int | None = None,
    is_final: bool,
) -> None:
    _publish_managed_video_atomically(source_path, managed_path)
    _write_managed_video_metadata(
        managed_path,
        conversation_id=conversation_id,
        run_key=run_key,
        preview_version=preview_version,
        final_version=final_version,
        is_final=is_final,
    )


def _preview_version_from_path(path: Path) -> int:
    stem = path.stem
    if not stem.startswith("preview_v"):
        return 0
    suffix = stem[len("preview_v") :]
    return int(suffix) if suffix.isdigit() else 0


def _versioned_video_url(file_name: str, preview_version: int | None = None) -> str:
    normalized = str(file_name or "").strip()
    if not normalized:
        return ""
    base_url = f"/api/manim/videos/{normalized}"
    if preview_version is None or preview_version <= 0:
        return base_url
    return f"{base_url}?v={preview_version}"


def _latest_incremental_preview_path(run_dir: Path) -> tuple[Path | None, int]:
    preview_dir = Path(run_dir) / "round1" / "preview"
    if not preview_dir.exists():
        return None, 0
    candidates = sorted(
        (item for item in preview_dir.glob("preview_v*.mp4") if item.is_file()),
        key=lambda item: (_preview_version_from_path(item), item.name),
    )
    if not candidates:
        return None, 0
    latest = candidates[-1]
    return latest, _preview_version_from_path(latest)


def _managed_data_root() -> Path:
    return (Path(settings.paths.data_path) / "manim").resolve()


def get_managed_runs_dir() -> Path:
    return _managed_data_root() / "runs"


def get_managed_videos_dir() -> Path:
    return _managed_data_root() / "videos"


def _probe_duration_seconds(video_path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        return round(float(raw), 3)
    except ValueError:
        return None


def _normalize_quality_flags(quality: str) -> str | None:
    normalized = (quality or "default").strip().lower()
    if normalized not in _QUALITY_PRESETS:
        raise ValueError("quality must be one of: default, draft, medium, high")
    preset = _QUALITY_PRESETS[normalized].strip()
    return preset or None


def _redact_public_text(value: str) -> str:
    redacted = value
    for pattern in _ABSOLUTE_PATH_PATTERNS:
        redacted = pattern.sub(_PUBLIC_REDACTED_TEXT, redacted)
    return redacted


def _is_public_path_key(key: object) -> bool:
    normalized = str(key or "").strip()
    return normalized in _PUBLIC_PATH_KEYS or normalized.endswith("_path")


def _sanitize_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if _is_public_path_key(key):
                continue
            sanitized[str(key)] = _sanitize_public_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_public_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_public_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_public_text(value)
    return value


def _sanitize_public_error(message: object) -> str:
    return _redact_public_text(str(message or "").strip() or "Manim render failed.")


@dataclass(slots=True)
class RenderJobState:
    job_id: str
    status: str
    message: str
    conversation_id: str = ""
    render_backend: str = "manim"
    quality: str = "default"
    run_key: str = ""
    preview_file_name: str = ""
    preview_version: int | None = None
    preview_ready: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "job_id": self.job_id,
            "message": self.message,
            "conversation_id": self.conversation_id or None,
            "render_backend": self.render_backend,
            "quality": self.quality,
            "run_key": self.run_key or None,
            "preview_file_name": self.preview_file_name or None,
            "preview_version": self.preview_version,
            "preview_ready": self.preview_ready,
            "preview_url": (
                _versioned_video_url(self.preview_file_name, self.preview_version)
                if self.preview_file_name and self.preview_ready
                else None
            ),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.error:
            payload["error"] = self.error
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.result is not None:
            payload["result"] = _sanitize_public_payload(self.result)
        return payload


class ManimRenderRuntime:
    def __init__(self) -> None:
        configured_runs_dir = str(settings.manim.runs_output_dir or "").strip()
        self._runs_dir = Path(configured_runs_dir).resolve() if configured_runs_dir else get_manim_runs_dir().resolve()
        self._videos_dir = get_managed_videos_dir()
        self._jobs: dict[str, RenderJobState] = {}
        self._jobs_lock = threading.Lock()

    def _append_job_event(self, job_id: str, payload: dict[str, Any]) -> None:
        with self._jobs_lock:
            state = self._jobs.get(job_id)
            if state is None:
                return
            event = _sanitize_public_payload(dict(payload))
            event.setdefault("job_id", job_id)
            event.setdefault("ts", time.time())
            extra = event.get("extra")
            if isinstance(extra, dict) and isinstance(extra.get("preview_version"), int):
                state.preview_version = int(extra["preview_version"])
                if state.preview_version > 0:
                    state.preview_ready = True
            result = event.get("result")
            if isinstance(result, dict) and isinstance(result.get("preview_version"), int):
                state.preview_version = int(result["preview_version"])
                if state.preview_version > 0:
                    state.preview_ready = True
            state.events.append(event)
            state.updated_at = time.time()

    def _acquire_job_slot(self) -> bool:
        """Acquire a concurrent render slot. Returns False only when wait policy is non-blocking."""
        sem = _job_slot_semaphore()
        wait = settings.manim.job_slot_wait_seconds
        if wait is None:
            sem.acquire()
            return True
        if wait == 0:
            return sem.acquire(blocking=False)
        return sem.acquire(timeout=float(wait))

    def _set_job_state(
        self,
        job_id: str,
        *,
        status: str,
        message: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> RenderJobState | None:
        with self._jobs_lock:
            state = self._jobs.get(job_id)
            if state is None:
                return None
            state.status = status
            if message is not None:
                state.message = message
            if result is not None:
                state.result = _sanitize_public_payload(result)
            if error is not None:
                state.error = _sanitize_public_error(error)
            if error_code is not None:
                state.error_code = error_code
            state.updated_at = time.time()
            return state

    def _set_job_state_with_event(
        self,
        job_id: str,
        *,
        status: str,
        message: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> None:
        state = self._set_job_state(
            job_id,
            status=status,
            message=message,
            result=result,
            error=error,
            error_code=error_code,
        )
        if state is None:
            return
        self._append_job_event(
            job_id,
            {
                "type": "job_status",
                "status": status,
                "message": message,
                "error": error,
                "error_code": error_code,
                "result": result,
            },
        )

    def _create_job(
        self,
        *,
        conversation_id: str,
        render_backend: str,
        quality: str,
    ) -> RenderJobState:
        run_key = _new_run_key()
        preview_file_name = _managed_video_name_for_run(run_key)
        state = RenderJobState(
            job_id=f"manim_job_{uuid.uuid4().hex[:12]}",
            status="queued",
            message="Manim render job queued. Video generation is still in progress.",
            conversation_id=conversation_id,
            render_backend=render_backend,
            quality=quality,
            run_key=run_key,
            preview_file_name=preview_file_name,
        )
        with self._jobs_lock:
            self._jobs[state.job_id] = state
        self._append_job_event(
            state.job_id,
            {
                "type": "job_status",
                "status": "queued",
                "message": state.message,
                "preview_file_name": state.preview_file_name,
                "preview_ready": False,
                "preview_url": None,
            },
        )
        return state

    def _execute_render_video(
        self,
        *,
        request: str,
        language: str = "",
        render_backend: str = "manim",
        quality: str = "default",
        conversation_id: str = "",
        run_key: str = "",
        managed_name: str = "",
        event_callback: Any = None,
    ) -> dict[str, Any]:
        request_text = str(request or "").strip()
        if not request_text:
            raise ValueError("request is required")

        backend = str(render_backend or "manim").strip().lower() or "manim"
        if backend not in {"manim", "hybrid"}:
            raise ValueError("render_backend must be 'manim' or 'hybrid'")

        quality_normalized = (quality or "default").strip().lower() or "default"
        quality_flags = _normalize_quality_flags(quality_normalized)
        resolved_run_key = str(run_key or "").strip() or _new_run_key()
        conversation_tag = (conversation_id or "").strip() or "adhoc"
        safe_conversation_tag = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in conversation_tag)
        run_dir = self._runs_dir / f"{safe_conversation_tag}_{resolved_run_key}"

        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._videos_dir.mkdir(parents=True, exist_ok=True)
        resolved_managed_name = str(managed_name or "").strip() or _managed_video_name_for_run(resolved_run_key)
        managed_path = self._videos_dir / resolved_managed_name
        latest_preview_version = 0
        run_id_hint = run_dir.name

        def _maybe_publish_latest_preview() -> int:
            nonlocal latest_preview_version
            preview_path, preview_version = _latest_incremental_preview_path(run_dir)
            if preview_path is None or preview_version <= latest_preview_version:
                return latest_preview_version
            _publish_managed_artifacts(
                preview_path,
                managed_path,
                conversation_id=conversation_id,
                run_key=resolved_run_key,
                preview_version=preview_version,
                final_version=None,
                is_final=False,
            )
            latest_preview_version = preview_version
            if event_callback is not None:
                event_callback(
                    make_manim_stream_event(
                        event_type=ManimStreamEventType.PREVIEW_VIDEO_UPDATED,
                        stage=ManimStreamEventStage.DELIVERY,
                        message=f"Preview video updated: v{preview_version}",
                        run_id=run_id_hint,
                        extra={
                            "preview_url": _versioned_video_url(resolved_managed_name, preview_version),
                            "preview_file_name": resolved_managed_name,
                            "preview_version": preview_version,
                            "preview_ready": True,
                            "preview_sections": preview_version,
                            "is_final": False,
                        },
                    )
                )
            return latest_preview_version

        _logger.info(
            "[Manim] 开始生成教学视频: backend=%s quality=%s run=%s",
            backend,
            quality_normalized,
            run_dir.name,
        )

        wait_policy = settings.manim.job_slot_wait_seconds
        queue_start = time.monotonic()
        if not self._acquire_job_slot():
            _logger.warning(
                "[Manim] 并发已满，拒绝渲染: run=%s policy=%s",
                run_dir.name,
                wait_policy,
            )
            raise RuntimeError(
                "Manim render capacity reached (no free job slot). "
                "Retry later or increase [manim].max_concurrent_jobs / adjust job_slot_wait_seconds."
            )
        queue_ms = (time.monotonic() - queue_start) * 1000.0
        if queue_ms > 1.0:
            _logger.info("[Manim] 排队获得槽位: run=%s queue_ms=%.1f", run_dir.name, queue_ms)

        try:
            def _runtime_event_callback(event: Any) -> None:
                nonlocal run_id_hint
                if hasattr(event, "run_id"):
                    run_id_hint = str(getattr(event, "run_id") or run_id_hint)
                elif isinstance(event, dict) and event.get("run_id"):
                    run_id_hint = str(event.get("run_id") or run_id_hint)
                if event_callback is not None:
                    event_callback(event)
                try:
                    _maybe_publish_latest_preview()
                except Exception:
                    return

            summary = run_pipeline(
                request_text=request_text,
                run_dir=run_dir,
                language=language or None,
                render_backend=backend,
                quality_flags=quality_flags,
                event_callback=_runtime_event_callback,
            )
        finally:
            _job_slot_semaphore().release()

        _maybe_publish_latest_preview()

        source_video = summary.get("delivery_video") or summary.get("final_video_with_audio") or summary.get("final_video")
        if not source_video:
            raise RuntimeError("Pipeline finished without producing a final video.")

        source_path = Path(str(source_video)).resolve()
        if not source_path.exists():
            raise RuntimeError(f"Final video is missing: {source_path}")

        final_preview_version = (latest_preview_version or 0) + 1
        _publish_managed_artifacts(
            source_path,
            managed_path,
            conversation_id=conversation_id,
            run_key=resolved_run_key,
            preview_version=latest_preview_version or None,
            final_version=final_preview_version,
            is_final=True,
        )
        final_video_url = _versioned_video_url(resolved_managed_name, final_preview_version)
        if event_callback is not None:
            event_callback(
                make_manim_stream_event(
                    event_type=ManimStreamEventType.PREVIEW_VIDEO_FINALIZED,
                    stage=ManimStreamEventStage.DELIVERY,
                    message="Preview video finalized",
                    run_id=run_id_hint,
                    progress=100,
                    extra={
                        "preview_url": final_video_url,
                        "preview_file_name": resolved_managed_name,
                        "video_url": final_video_url,
                        "file_name": resolved_managed_name,
                        "preview_version": final_preview_version,
                        "preview_ready": True,
                        "is_final": True,
                    },
                )
            )

        duration_seconds = _probe_duration_seconds(managed_path)
        _logger.info(
            "[Manim] 生成完成: file=%s duration=%s",
            resolved_managed_name,
            duration_seconds,
        )
        return {
            "status": "ok",
            "preview_type": "manim_video",
            "message": "Teaching video generated successfully.",
            "video_url": final_video_url,
            "file_name": resolved_managed_name,
            "preview_url": final_video_url,
            "preview_file_name": resolved_managed_name,
            "delivery_url": final_video_url,
            "delivery_file_name": resolved_managed_name,
            "duration_seconds": duration_seconds,
            "preview_version": final_preview_version,
            "preview_ready": True,
            "render_backend": backend,
            "quality": quality_normalized,
            "output_language": summary.get("output_language"),
            "conversation_id": conversation_id or None,
        }

    def _run_render_job(
        self,
        job_id: str,
        *,
        request: str,
        language: str,
        render_backend: str,
        quality: str,
        conversation_id: str,
        run_key: str,
        managed_name: str,
    ) -> None:
        self._set_job_state_with_event(
            job_id,
            status="running",
            message="Manim render job is running. Final video is not ready yet.",
        )
        try:
            def _job_event_callback(event: Any) -> None:
                payload = event.model_dump() if hasattr(event, "model_dump") else dict(event)
                self._append_job_event(job_id, payload)

            result = self._execute_render_video(
                request=request,
                language=language,
                render_backend=render_backend,
                quality=quality,
                conversation_id=conversation_id,
                run_key=run_key,
                managed_name=managed_name,
                event_callback=_job_event_callback,
            )
            self._set_job_state_with_event(
                job_id,
                status="ok",
                message="Manim render job completed.",
                result=result,
            )
        except Exception as exc:
            error_code = type(exc).__name__
            error_text = _sanitize_public_error(exc)
            if isinstance(exc, RuntimeError):
                prefix, separator, _ = error_text.partition(": ")
                if separator and prefix.startswith("codegen_"):
                    error_code = prefix
            _logger.exception("[Manim] async render job failed: job_id=%s error=%s", job_id, exc)
            self._set_job_state_with_event(
                job_id,
                status="error",
                message=f"Failed to generate Manim video: {error_text}",
                error=error_text,
                error_code=error_code,
            )

    def submit_render_video(
        self,
        *,
        request: str,
        language: str = "",
        render_backend: str = "manim",
        quality: str = "default",
        conversation_id: str = "",
    ) -> dict[str, Any]:
        request_text = str(request or "").strip()
        if not request_text:
            raise ValueError("request is required")

        backend = str(render_backend or "manim").strip().lower() or "manim"
        if backend not in {"manim", "hybrid"}:
            raise ValueError("render_backend must be 'manim' or 'hybrid'")

        quality_normalized = (quality or "default").strip().lower() or "default"
        _normalize_quality_flags(quality_normalized)

        state = self._create_job(
            conversation_id=str(conversation_id or "").strip(),
            render_backend=backend,
            quality=quality_normalized,
        )

        worker = threading.Thread(
            target=self._run_render_job,
            kwargs={
                "job_id": state.job_id,
                "request": request_text,
                "language": language,
                "render_backend": backend,
                "quality": quality_normalized,
                "conversation_id": str(conversation_id or "").strip(),
                "run_key": state.run_key,
                "managed_name": state.preview_file_name,
            },
            daemon=True,
        )
        worker.start()

        return {
            "status": "queued",
            "preview_type": None,
            "message": "Manim render job accepted. Video generation is still in progress.",
            "job_id": state.job_id,
            "conversation_id": state.conversation_id or None,
            "render_backend": state.render_backend,
            "quality": state.quality,
            "run_key": state.run_key,
            "preview_file_name": state.preview_file_name,
            "preview_ready": False,
            "preview_url": None,
        }

    def get_job_status(self, *, job_id: str) -> dict[str, Any]:
        normalized = str(job_id or "").strip()
        if not normalized:
            raise ValueError("job_id is required")
        with self._jobs_lock:
            state = self._jobs.get(normalized)
            if state is None:
                raise KeyError(f"Unknown job_id: {normalized}")
            return state.to_payload()

    def get_job_events(self, *, job_id: str, after_index: int = 0) -> dict[str, Any]:
        normalized = str(job_id or "").strip()
        if not normalized:
            raise ValueError("job_id is required")
        if after_index < 0:
            raise ValueError("after_index must be >= 0")
        with self._jobs_lock:
            state = self._jobs.get(normalized)
            if state is None:
                raise KeyError(f"Unknown job_id: {normalized}")
            events = [dict(event) for event in state.events[after_index:]]
            return {
                "job_id": normalized,
                "events": events,
                "next_index": len(state.events),
                "terminal": state.status in {"ok", "error"},
                "status": state.status,
            }

    def render_video(
        self,
        *,
        request: str,
        language: str = "",
        render_backend: str = "manim",
        quality: str = "default",
        conversation_id: str = "",
    ) -> dict[str, Any]:
        return self._execute_render_video(
            request=request,
            language=language,
            render_backend=render_backend,
            quality=quality,
            conversation_id=conversation_id,
            run_key=_new_run_key(),
        )


@dataclass(slots=True)
class ManimMCPRuntime:
    server: Server
    renderer: ManimRenderRuntime


def get_shared_manim_runtime() -> ManimRenderRuntime:
    global _shared_renderer_runtime
    if _shared_renderer_runtime is None:
        _shared_renderer_runtime = ManimRenderRuntime()
    return _shared_renderer_runtime


def create_manim_runtime() -> ManimMCPRuntime:
    renderer = get_shared_manim_runtime()
    server = create_server(
        server_name=SERVER_NAME,
        render_teaching_video=renderer.render_video,
        submit_teaching_video_job=renderer.submit_render_video,
        get_teaching_video_job_status=renderer.get_job_status,
    )
    server.version = SERVER_VERSION
    return ManimMCPRuntime(server=server, renderer=renderer)
