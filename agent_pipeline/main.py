"""
Single-round Manim generation pipeline with optional Remotion hybrid delivery.

Flow:
  1. CodeGen agent produces Round 1 Manim code from a student request.
  2. Round 1 renders with TTS enabled at final delivery quality.
  3. (hybrid mode) A StoryboardAgent designs a Remotion assembly plan,
     and the Manim video is wrapped with chapter cards / transitions.
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import os
import queue
import subprocess
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, TextIO

# Sentinel to stop the pipeline background IO worker (same-process only).
_PIPELINE_IO_SHUTDOWN = object()

from interface.plugins.manim import (
    ManimStreamEvent,
    ManimStreamEventStage,
    ManimStreamEventType,
    make_manim_stream_event,
)

from .asset_resolver import resolve_local_assets
from .code_eval import CodeEvalAgent
from .code_gen import CodeGenAgent
from .fast_paths import (
    build_fast_path_plan_and_code_for_category,
    maybe_build_fast_path_plan_and_code,
    should_skip_fast_path_for_request,
)
from .llm import resolve_pipeline_llm_configs, validate_pipeline_llm_configs
from .output_language import normalize_output_language, output_language_name
from .remotion_renderer import build_remotion_hybrid
from .renderer import (
    RenderResult,
    SegmentRenderResult,
    _concat_segment_videos,
    _write_round_render_log,
    build_incremental_hls_preview,
    build_incremental_preview_video,
    prepare_segment_tts_assets,
    render_streaming_scene_pack_segment_with_repair,
    write_streaming_scene_pack_segment_file,
)
from .scene_pack import parse_scene_pack
from .streaming_scene_pack import ScenePackStreamBuffer, sanitize_streaming_code
from .storyboard_agent import StoryboardAgent
from .teaching_planner import TeachingPlannerAgent
from .style_selector import ExplanationStyleSelector
from .section_validation import validate_and_fix_streaming_scene_file
from .theme_resolver import resolve_theme
from .tts import has_audio_stream, voice_for_language
from .concurrency_runtime import SectionPipelineCoordinator
from plugins.manim.runtime_config import get_manim_runs_dir, get_manim_settings

# =====================================================================
# Configuration constants
# =====================================================================

ROOT_DIR = Path(__file__).resolve().parent.parent
MANIM_SETTINGS = get_manim_settings()
MANIM_QUALITY = MANIM_SETTINGS.quality_flags
ROUND1_MANIM_QUALITY = MANIM_SETTINGS.round1_quality_flags or MANIM_QUALITY
RUNS_DIR = get_manim_runs_dir()
USE_LOCAL_ICONS = MANIM_SETTINGS.use_local_icons
DEFAULT_OUTPUT_LANGUAGE = normalize_output_language(
    MANIM_SETTINGS.default_output_language
)
CODE_EVAL_FIX_MAX_ATTEMPTS = max(1, MANIM_SETTINGS.code_eval_fix_max_attempts)
RENDER_FIX_MAX_ATTEMPTS = max(0, MANIM_SETTINGS.render_fix_max_attempts)

# =====================================================================
# Helpers
# =====================================================================


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _emit_debug_observation(
    callback: Callable[[dict[str, Any]], None] | None,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception as exc:
        _log(f"CLI debug callback warning: {exc}")


def _configure_pipeline_http_logging() -> None:
    """Silence per-request httpx INFO lines unless MANIM_HTTP_LOG is set."""
    raw = (os.environ.get("MANIM_HTTP_LOG") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _pipeline_io_sync_only() -> bool:
    """When set, stream/file debug IO runs on the caller thread (for tests / debugging)."""
    raw = (os.environ.get("MANIM_SYNC_PIPELINE_IO") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


_pipeline_io_lock = threading.Lock()
_pipeline_io_queue: queue.Queue | None = None
_pipeline_io_thread: threading.Thread | None = None
_pipeline_io_active_runs = 0


def _pipeline_io_queue_ready() -> bool:
    return _pipeline_io_queue is not None and _pipeline_io_thread is not None


def _pipeline_io_begin() -> None:
    """Start a background thread for stdout/file debug writes so hot-path timing stays clean."""
    global _pipeline_io_queue, _pipeline_io_thread, _pipeline_io_active_runs
    if _pipeline_io_sync_only():
        return
    with _pipeline_io_lock:
        _pipeline_io_active_runs += 1
        if _pipeline_io_thread is not None and _pipeline_io_thread.is_alive():
            return
        _pipeline_io_queue = queue.Queue()
        _pipeline_io_thread = threading.Thread(
            target=_pipeline_io_worker_loop,
            name="manim-pipeline-io",
            daemon=True,
        )
        _pipeline_io_thread.start()


def _pipeline_io_end() -> None:
    """Drain and stop the background IO worker when the outermost pipeline run finishes."""
    global _pipeline_io_queue, _pipeline_io_thread, _pipeline_io_active_runs
    if _pipeline_io_sync_only():
        return
    with _pipeline_io_lock:
        if _pipeline_io_active_runs <= 0:
            return
        _pipeline_io_active_runs -= 1
        if _pipeline_io_active_runs > 0:
            return
        q = _pipeline_io_queue
        th = _pipeline_io_thread
        _pipeline_io_queue = None
        _pipeline_io_thread = None
    if q is not None:
        q.put(_PIPELINE_IO_SHUTDOWN)
    if th is not None:
        th.join(timeout=120.0)


def _pipeline_io_worker_loop() -> None:
    q = _pipeline_io_queue
    if q is None:
        return
    while True:
        item = q.get()
        try:
            if item is _PIPELINE_IO_SHUTDOWN:
                break
            _pipeline_io_dispatch(item)
        except Exception:
            pass
        finally:
            q.task_done()


def _sync_stream_write(stream: TextIO, text: str) -> None:
    if not text:
        return
    stream.write(text)
    stream.flush()


def _pipeline_io_dispatch(item: Any) -> None:
    kind = item[0]
    if kind == "stream":
        _, stream, text = item
        _sync_stream_write(stream, text)
    elif kind == "append":
        _, path, text = item
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    elif kind == "json":
        _, path, payload = item
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    elif kind == "preview_open":
        _, path_s, platform_name, open_fn = item
        path = Path(path_s)
        if platform_name != "darwin":
            _sync_stream_write(
                sys.stdout,
                f"[debug] preview auto-open skipped on platform={platform_name}: {path}\n",
            )
            return
        ok, detail = open_fn(path)
        if ok:
            _sync_stream_write(sys.stdout, f"[debug] opened preview with macOS open: {path}\n")
        else:
            _sync_stream_write(
                sys.stdout,
                f"[debug] preview auto-open failed: {detail or path}\n",
            )


def _enqueue_pipeline_io(item: tuple[Any, ...]) -> None:
    q = _pipeline_io_queue
    if q is None:
        _pipeline_io_dispatch(item)
        return
    q.put(item)


def _append_stream_text_impl(path: Path, delta: str) -> None:
    if not delta:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(delta)
    except Exception:
        return


def _write_json_debug_impl(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _write_text_debug(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except Exception:
        return


def _call_with_optional_on_event(
    fn: Callable[..., Any],
    /,
    *args: Any,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    **kwargs: Any,
) -> Any:
    if on_event is None:
        return fn(*args, **kwargs)
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        kwargs["on_event"] = on_event
        return fn(*args, **kwargs)
    parameters = signature.parameters
    if "on_event" in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    ):
        kwargs["on_event"] = on_event
    return fn(*args, **kwargs)


class CliDebugSink:
    EVENT_PREFIX = "A4L_MANIM_EVENT\t"

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        platform_name: str | None = None,
        open_preview_fn: Callable[[Path], tuple[bool, str]] | None = None,
        machine_verbose: bool = False,
        sync_io: bool = False,
    ) -> None:
        self.stream = stream or sys.stdout
        self.platform_name = (platform_name or sys.platform).lower()
        self.open_preview_fn = open_preview_fn or self._open_preview_in_default_app
        self.machine_verbose = machine_verbose
        self.sync_io = sync_io
        self._preview_lock = threading.Lock()
        self._current_delta_type: str | None = None
        self._line_open = False
        self._opened_preview_versions: set[int] = set()
        self._last_status_by_stage: dict[str, str] = {}

    def __call__(self, payload: dict[str, Any]) -> None:
        self.emit(payload)

    def emit(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("type") or "").strip().lower()
        if not event_type:
            return
        if event_type in {"analysis_delta", "code_delta", "llm_reasoning_summary_delta"}:
            self._emit_delta(event_type, str(payload.get("delta") or ""))
            # Per-chunk A4L_MANIM_EVENT lines interleave with streamed text and ruin readability;
            # emit them only when integrators need machine-parseable deltas (--debug-machine-verbose).
            if self.machine_verbose:
                self._emit_machine_event(payload)
            return
        self._current_delta_type = None
        self._emit_human_event(event_type, payload)
        self._emit_machine_event(payload)

    def _write(self, text: str) -> None:
        if not text:
            return
        self._line_open = not text.endswith("\n")
        if self.sync_io or not _pipeline_io_queue_ready():
            _sync_stream_write(self.stream, text)
            return
        _enqueue_pipeline_io(("stream", self.stream, text))

    def _ensure_newline(self) -> None:
        if self._line_open:
            self._write("\n")

    def _emit_delta(self, event_type: str, delta: str) -> None:
        if not delta:
            return
        if self._current_delta_type != event_type:
            self._ensure_newline()
            if event_type == "analysis_delta":
                label = "AI analysis"
            elif event_type == "code_delta":
                label = "AI code"
            else:
                label = "AI reasoning summary"
            self._write(f"[debug] {label}:\n")
            self._current_delta_type = event_type
        self._write(delta)

    def _emit_human_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self._ensure_newline()
        if event_type == "llm_request_started":
            self._write(
                "[debug] AI request started: "
                f"stage={payload.get('stage')} model={payload.get('provider')}/{payload.get('model')} "
                f"reasoning={payload.get('reasoning_effort')} attempt={payload.get('attempt')} "
                f"input_chars={payload.get('input_text_chars')} images={payload.get('input_image_count')} "
                f"endpoint={payload.get('endpoint')} "
                f"service_tier={payload.get('service_tier_requested')} "
                f"reasoning_summary={payload.get('reasoning_summary_requested')}\n"
            )
            return
        if event_type == "llm_stream_status":
            stage = str(payload.get("stage") or "unknown")
            status = str(payload.get("status") or payload.get("event") or "unknown")
            if self._last_status_by_stage.get(stage) == status:
                return
            self._last_status_by_stage[stage] = status
            self._write(f"[debug] AI {stage} status: {status}\n")
            return
        if event_type == "llm_reasoning_started":
            self._write(f"[debug] AI {payload.get('stage')} reasoning started\n")
            return
        if event_type == "llm_reasoning_progress":
            self._write(
                f"[debug] AI {payload.get('stage')} reasoning in progress "
                f"(chars={payload.get('char_count')})\n"
            )
            return
        if event_type == "llm_reasoning_completed":
            self._write(
                f"[debug] AI {payload.get('stage')} reasoning completed "
                f"(chars={payload.get('char_count')})\n"
            )
            return
        if event_type == "llm_output_started":
            self._write(f"[debug] AI {payload.get('stage')} started emitting output\n")
            return
        if event_type == "llm_request_retry":
            self._write(
                "[debug] AI request retry: "
                f"stage={payload.get('stage')} attempt={payload.get('attempt')} "
                f"next={payload.get('next_attempt')} removed={payload.get('removed_keys') or []} "
                f"changed={payload.get('changed_keys') or []} error={payload.get('error')}\n"
            )
            return
        if event_type == "llm_request_failed":
            self._write(
                f"[debug] AI request failed: stage={payload.get('stage')} "
                f"attempt={payload.get('attempt')} error={payload.get('error')}\n"
            )
            return
        if event_type == "llm_request_completed":
            self._write(
                "[debug] AI request completed: "
                f"stage={payload.get('stage')} attempt={payload.get('attempt')} "
                f"output_chars={payload.get('output_chars')} "
                f"reasoning_chars={payload.get('reasoning_chars')} "
                f"reasoning_summary_chars={payload.get('reasoning_summary_chars')} "
                f"service_tier_requested={payload.get('service_tier_requested')} "
                f"service_tier_effective={payload.get('service_tier_effective')} "
                f"reasoning_summary={payload.get('reasoning_summary_requested')}\n"
            )
            return
        if event_type == "section_render_completed":
            scene_name = payload.get("scene_name") or payload.get("segment_id") or "unknown"
            self._write(
                "[debug] section rendered: "
                f"order={payload.get('segment_order')} segment={payload.get('segment_id')} "
                f"scene={scene_name} success={bool(payload.get('success', False))} "
                f"video={payload.get('video_path') or '-'}\n"
            )
            return
        if event_type == "preview_updated":
            preview_version = int(payload.get("preview_version", 0) or 0)
            self._write(
                "[debug] preview updated: "
                f"version={preview_version} sections={payload.get('preview_sections')} "
                f"path={payload.get('preview_path') or '-'}\n"
            )
            self._maybe_open_preview(payload)
            return
        self._write(f"[debug] {event_type}: {json.dumps(payload, ensure_ascii=False, sort_keys=True)}\n")

    def _payload_for_machine_line(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.machine_verbose:
            return dict(payload)
        event_type = str(payload.get("type") or "").strip().lower()
        if event_type in {"analysis_delta", "code_delta"}:
            delta = payload.get("delta")
            slim: dict[str, Any] = {
                "type": payload.get("type"),
                "run_id": payload.get("run_id"),
                "char_count": payload.get("char_count"),
            }
            if isinstance(delta, str):
                slim["delta_len"] = len(delta)
            return {k: v for k, v in slim.items() if v is not None}
        return dict(payload)

    def _emit_machine_event(self, payload: dict[str, Any]) -> None:
        self._ensure_newline()
        line_payload = self._payload_for_machine_line(payload)
        self._write(
            f"{self.EVENT_PREFIX}{json.dumps(line_payload, ensure_ascii=False, sort_keys=True)}\n"
        )

    def _maybe_open_preview(self, payload: dict[str, Any]) -> None:
        preview_path = str(payload.get("preview_path") or "").strip()
        if not preview_path:
            return
        preview_version = int(payload.get("preview_version", 0) or 0)
        with self._preview_lock:
            if preview_version in self._opened_preview_versions:
                return
            self._opened_preview_versions.add(preview_version)
        path = Path(preview_path)
        if self.sync_io or not _pipeline_io_queue_ready():
            self._open_preview_sync(path)
            return
        _enqueue_pipeline_io(("preview_open", str(path), self.platform_name, self.open_preview_fn))

    def _open_preview_sync(self, path: Path) -> None:
        if self.platform_name != "darwin":
            self._write(f"[debug] preview auto-open skipped on platform={self.platform_name}: {path}\n")
            return
        ok, detail = self.open_preview_fn(path)
        if ok:
            self._write(f"[debug] opened preview with macOS open: {path}\n")
            return
        self._write(f"[debug] preview auto-open failed: {detail or path}\n")

    @staticmethod
    def _open_preview_in_default_app(path: Path) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                ["open", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            return False, str(exc)
        if result.returncode == 0:
            return True, ""
        detail = (result.stderr or result.stdout or f"exit={result.returncode}").strip()
        return False, detail


def _emit_pipeline_event(
    callback: Callable[[ManimStreamEvent], None] | None,
    *,
    event_type: ManimStreamEventType,
    stage: ManimStreamEventStage,
    message: str,
    run_id: str,
    progress: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if callback is None:
        return
    event = make_manim_stream_event(
        event_type=event_type,
        stage=stage,
        message=message,
        run_id=run_id,
        progress=progress,
        extra=extra,
    )
    try:
        callback(event)
    except Exception as exc:
        _log(f"Streaming callback warning: {exc}")


def _make_text_delta_event_bridge(
    callback: Callable[[ManimStreamEvent], None] | None,
    *,
    run_id: str,
    event_type: ManimStreamEventType,
    stage: ManimStreamEventStage,
    message: str,
) -> Callable[[str], None] | None:
    if callback is None:
        return None

    chunk_index = 0
    total_chars = 0

    def _bridge(delta: str) -> None:
        nonlocal chunk_index, total_chars
        if not isinstance(delta, str) or not delta:
            return
        chunk_index += 1
        total_chars += len(delta)
        _emit_pipeline_event(
            callback,
            event_type=event_type,
            stage=stage,
            message=message,
            run_id=run_id,
            extra={
                "chunk_index": chunk_index,
                "char_count": total_chars,
                "delta": delta,
            },
        )

    return _bridge


def _append_stream_text(path: Path, delta: str) -> None:
    if not delta:
        return
    if not _pipeline_io_queue_ready():
        _append_stream_text_impl(path, delta)
        return
    _enqueue_pipeline_io(("append", path, delta))


def _read_streaming_scene_file_code(scene_file: Path) -> str:
    return scene_file.read_text(encoding="utf-8")


def _write_json_debug(path: Path, payload: dict[str, Any]) -> None:
    if not _pipeline_io_queue_ready():
        _write_json_debug_impl(path, payload)
        return
    _enqueue_pipeline_io(("json", path, dict(payload)))


def _record_timing_once(store: dict[str, float], key: str, pipeline_started_at: float) -> None:
    if key in store:
        return
    store[key] = round(time.time() - pipeline_started_at, 3)


def _round_timing_map(values: dict[str, float]) -> dict[str, float]:
    return {
        key: round(float(value), 3)
        for key, value in values.items()
    }


def _build_pipeline_timing_summary(
    *,
    stage_times: dict[str, float],
    stream_timing: dict[str, float],
    streaming_section_counts: dict[str, Any],
    streaming_preview: dict[str, Any],
) -> dict[str, Any]:
    rounded_stage_times = _round_timing_map(stage_times)
    rounded_stream_timing = _round_timing_map(stream_timing)
    preview_history = streaming_preview.get("history")
    preview_history_count = len(preview_history) if isinstance(preview_history, list) else 0
    preview_version = streaming_preview.get("version")
    published_prefix_len = streaming_preview.get("published_prefix_len")
    total_wall_seconds = rounded_stream_timing.get("t_final_done")
    first_preview_seconds = rounded_stream_timing.get("t_first_preview_built")
    first_section_ready_seconds = rounded_stream_timing.get("t_first_section_ready")
    return {
        "total_wall_seconds": total_wall_seconds,
        "time_to_first_analysis_delta_seconds": rounded_stream_timing.get("t_first_analysis_delta"),
        "time_to_first_code_delta_seconds": rounded_stream_timing.get("t_first_code_delta"),
        "time_to_first_section_ready_seconds": first_section_ready_seconds,
        "time_to_first_section_validation_started_seconds": rounded_stream_timing.get("t_first_section_validation_started"),
        "time_to_first_section_validation_completed_seconds": rounded_stream_timing.get("t_first_section_validation_completed"),
        "time_to_first_section_fix_completed_seconds": rounded_stream_timing.get("t_first_section_fix_completed"),
        "time_to_first_section_tts_started_seconds": rounded_stream_timing.get("t_first_section_tts_started"),
        "time_to_first_section_tts_completed_seconds": rounded_stream_timing.get("t_first_section_tts_completed"),
        "time_to_first_section_render_started_seconds": rounded_stream_timing.get("t_first_section_render_started"),
        "time_to_first_section_render_completed_seconds": rounded_stream_timing.get("t_first_section_render_completed"),
        "time_to_first_preview_seconds": first_preview_seconds,
        "time_from_first_section_ready_to_first_preview_seconds": (
            round(first_preview_seconds - first_section_ready_seconds, 3)
            if first_preview_seconds is not None and first_section_ready_seconds is not None
            else None
        ),
        "render_stage_started_seconds": rounded_stream_timing.get("t_render_stage_started"),
        "stage_times": rounded_stage_times,
        "stream_timing": rounded_stream_timing,
        "streaming_section_counts": dict(streaming_section_counts),
        "preview_history_count": preview_history_count,
        "preview_versions_published": int(preview_version or 0),
        "preview_contiguous_prefix_sections": int(published_prefix_len or 0),
        "preview_last_error": streaming_preview.get("last_error"),
    }


def _attempt_codegen_repair(
    *,
    agent: Any,
    code: str,
    codegen_failure_message: str,
    output_language: str,
) -> str | None:
    try:
        repaired = agent.fix(
            code=code,
            error_log=codegen_failure_message,
            output_language=output_language,
        )
    except Exception:
        return None
    if not isinstance(repaired, str) or not repaired.strip():
        return None
    repaired = sanitize_streaming_code(repaired)
    try:
        spec = parse_scene_pack(repaired)
    except Exception:
        return None
    return repaired if spec.manifest else None


def _streaming_section_state_summary(state: Any) -> dict[str, Any]:
    validation_result = state.validation_result if isinstance(getattr(state, "validation_result", None), dict) else {}
    validation_report = validation_result.get("validation_report")
    validation_summary = ""
    validation_passed = bool(validation_result.get("passed")) if validation_result else state.status != "failed"
    if isinstance(validation_report, dict):
        validation_summary = str(validation_report.get("summary") or "")
    return {
        "segment_id": state.task.segment_id,
        "scene_name": state.task.scene_name,
        "order": state.task.order,
        "status": state.status,
        "validated": validation_passed,
        "validation_attempts": int(validation_result.get("payload", {}).get("validation_attempts", 0))
        if isinstance(validation_result.get("payload"), dict)
        else 0,
        "validation_failed": bool(validation_result) and not validation_passed,
        "validation_summary": validation_summary or state.error,
        "render_success": bool(getattr(state.render_result, "success", False))
        if not isinstance(state.render_result, dict)
        else bool(state.render_result.get("success")),
        "render_attempts": int(getattr(state.render_result, "render_attempts", 0))
        if not isinstance(state.render_result, dict)
        else int(state.render_result.get("render_attempts", 0) or 0),
        "render_repair_rounds": int(getattr(state.render_result, "render_repair_rounds", 0))
        if not isinstance(state.render_result, dict)
        else int(state.render_result.get("render_repair_rounds", 0) or 0),
        "error": state.error,
    }


def _streaming_section_status_payload(
    *,
    segment_id: str,
    scene_name: str | None = None,
    stage: str,
    payload: dict[str, Any] | None = None,
    state: Any = None,
) -> dict[str, Any]:
    if state is not None:
        status_payload = _streaming_section_state_summary(state)
        status_payload["stage"] = stage
        return status_payload
    return {
        "segment_id": segment_id,
        "scene_name": scene_name,
        "stage": stage,
        "payload": payload or {},
    }


def _write_streaming_section_status(
    run_dir: Path,
    *,
    segment_id: str,
    scene_name: str | None = None,
    stage: str,
    payload: dict[str, Any] | None = None,
    state: Any = None,
) -> None:
    _write_json_debug(
        run_dir / f"section_status_{segment_id}.json",
        _streaming_section_status_payload(
            segment_id=segment_id,
            scene_name=scene_name,
            stage=stage,
            payload=payload,
            state=state,
        ),
    )


def _write_streaming_section_statuses(run_dir: Path, states: list[Any]) -> None:
    for state in states:
        _write_streaming_section_status(
            run_dir,
            segment_id=state.task.segment_id,
            scene_name=state.task.scene_name,
            stage=state.status,
            state=state,
        )


def _write_streaming_submit_failure(
    run_dir: Path,
    *,
    segment_id: str,
    scene_name: str,
    order: int,
    parseable_prefix: str,
    error: str,
) -> None:
    _write_json_debug(
        run_dir / f"section_submit_failure_{segment_id}.json",
        {
            "segment_id": segment_id,
            "scene_name": scene_name,
            "order": order,
            "error": error,
        },
    )
    _write_text_debug(
        run_dir / f"section_submit_failure_{segment_id}.py",
        parseable_prefix,
    )


def _emit_section_ready_event(
    *,
    event_callback: Callable[[ManimStreamEvent], None] | None,
    run_id: str,
    run_dir: Path,
    stream_timing: dict[str, float],
    stream_timing_path: Path,
    pipeline_started_at: float,
    segment_id: str,
    scene_name: str,
    method_name: str,
    order: int,
    ready_sections: int,
    resolved_scene_file: Path,
) -> None:
    _emit_pipeline_event(
        event_callback,
        event_type=ManimStreamEventType.CODE_SECTION_READY,
        stage=ManimStreamEventStage.CODEGEN,
        message=f"Section ready: {scene_name}",
        run_id=run_id,
        extra={
            "section_id": segment_id,
            "section_method": method_name,
            "scene_name": scene_name,
            "section_order": order,
            "ready_sections": ready_sections,
            "scene_file": str(resolved_scene_file),
        },
    )
    _record_timing_once(stream_timing, "t_first_section_ready", pipeline_started_at)
    _write_json_debug(
        run_dir / f"section_ready_{segment_id}.json",
        {
            "segment_id": segment_id,
            "scene_name": scene_name,
            "method_name": method_name,
            "order": order,
            "ready_sections": ready_sections,
            "scene_file": str(resolved_scene_file),
        },
    )
    _write_json_debug(stream_timing_path, stream_timing)


def _materialize_missing_streaming_sections(
    *,
    code: str,
    submitted_segment_ids: set[str],
    ensure_coordinator: Callable[[int], SectionPipelineCoordinator],
    r1_dir: Path,
    tts_voice: str | None,
    event_callback: Callable[[ManimStreamEvent], None] | None,
    run_id: str,
    run_dir: Path,
    stream_timing: dict[str, float],
    stream_timing_path: Path,
    pipeline_started_at: float,
) -> None:
    sanitized_code = sanitize_streaming_code(code)
    try:
        spec = parse_scene_pack(sanitized_code)
    except Exception:
        return
    if not spec.manifest:
        return
    coordinator = ensure_coordinator(len(spec.manifest))
    ready_count = len(submitted_segment_ids)
    for segment in spec.manifest:
        if segment.segment_id in submitted_segment_ids:
            continue
        scene_file = r1_dir / "streaming_scene_files" / f"{segment.order:02d}_{segment.segment_id}.py"
        _, resolved_scene_file, _ = write_streaming_scene_pack_segment_file(
            sanitized_code,
            r1_dir,
            segment_id=segment.segment_id,
            order=segment.order,
            tts_voice=tts_voice,
            scene_file=scene_file,
        )
        ready_count += 1
        _emit_section_ready_event(
            event_callback=event_callback,
            run_id=run_id,
            run_dir=run_dir,
            stream_timing=stream_timing,
            stream_timing_path=stream_timing_path,
            pipeline_started_at=pipeline_started_at,
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            method_name=segment.method_name,
            order=segment.order,
            ready_sections=ready_count,
            resolved_scene_file=resolved_scene_file,
        )
        coordinator.submit_ready_section(
            {
                "segment_id": segment.segment_id,
                "scene_name": segment.scene_name,
                "order": segment.order,
                "scene_file": resolved_scene_file,
            }
        )


def _state_render_result(state: Any) -> SegmentRenderResult:
    result = state.render_result
    if isinstance(result, SegmentRenderResult):
        return result
    return SegmentRenderResult(
        segment_id=state.task.segment_id,
        scene_name=state.task.scene_name,
        order=state.task.order,
        output_dir=Path(getattr(result, "output_dir", "")) if getattr(result, "output_dir", None) else Path(""),
        success=bool(getattr(result, "success", False)),
        video_path=Path(result.video_path) if getattr(result, "video_path", None) else None,
        error_log=str(getattr(result, "error_log", "") or ""),
        render_attempts=int(getattr(result, "render_attempts", 1) or 1),
        render_repair_rounds=int(getattr(result, "render_repair_rounds", 0) or 0),
    )


def _build_section_only_render_result(
    *,
    output_dir: Path,
    states: list[Any],
    expected_segment_count: int | None = None,
) -> RenderResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    segment_results = [_state_render_result(state) for state in states]
    if not segment_results:
        error_log = "No streaming sections were produced for rendering."
        _write_round_render_log(output_dir, segment_results, final_error=error_log)
        return RenderResult(success=False, error_log=error_log, scene_name="ScenePack", segments=segment_results)
    if expected_segment_count is not None and len(segment_results) < expected_segment_count:
        error_log = (
            "streaming render incomplete: "
            f"expected {expected_segment_count} segment(s) from SCENE_MANIFEST, "
            f"but only {len(segment_results)} segment(s) reached render completion."
        )
        _write_round_render_log(output_dir, segment_results, final_error=error_log)
        return RenderResult(success=False, error_log=error_log, scene_name="ScenePack", segments=segment_results)
    failed_segments = [segment for segment in segment_results if not segment.success]
    if failed_segments:
        error_log = "\n\n".join(
            f"[{segment.order:02d}:{segment.segment_id}] {segment.error_log or 'segment_render_failed'}"
            for segment in failed_segments
        )
        successful_segments = [
            segment
            for segment in sorted(segment_results, key=lambda item: item.order)
            if segment.success and segment.video_path is not None
        ]
        if successful_segments:
            final_video = output_dir / "video.mp4"
            concat_error = _concat_segment_videos(successful_segments, final_video)
            if not concat_error:
                fallback_note = (
                    "Partial Manim video rendered because one or more sections failed. "
                    "Failed sections:\n"
                    f"{error_log}"
                )
                _write_round_render_log(
                    output_dir,
                    segment_results,
                    final_video=final_video,
                    final_error=fallback_note,
                )
                return RenderResult(
                    success=True,
                    video_path=final_video,
                    error_log=fallback_note,
                    scene_name="ScenePack",
                    segments=segment_results,
                )
            error_log = f"{error_log}\n\nPartial video concat also failed:\n{concat_error}"
        _write_round_render_log(output_dir, segment_results, final_error=error_log)
        return RenderResult(success=False, error_log=error_log, scene_name="ScenePack", segments=segment_results)

    final_video = output_dir / "video.mp4"
    concat_error = _concat_segment_videos(segment_results, final_video)
    if concat_error:
        _write_round_render_log(output_dir, segment_results, final_error=concat_error)
        return RenderResult(success=False, error_log=concat_error, scene_name="ScenePack", segments=segment_results)

    _write_round_render_log(output_dir, segment_results, final_video=final_video)
    return RenderResult(success=True, video_path=final_video, scene_name="ScenePack", segments=segment_results)


def _compute_contiguous_preview_prefix_len(
    completed_results_by_order: dict[int, SegmentRenderResult],
) -> int:
    ready = 0
    while True:
        result = completed_results_by_order.get(ready)
        if result is None or not result.success or result.video_path is None:
            return ready
        ready += 1


def _maybe_build_incremental_preview(
    *,
    output_dir: Path,
    completed_results_by_order: dict[int, SegmentRenderResult],
    published_preview_prefix_len: int,
    preview_version: int,
) -> dict[str, Any]:
    contiguous_prefix_len = _compute_contiguous_preview_prefix_len(completed_results_by_order)
    if contiguous_prefix_len <= published_preview_prefix_len:
        return {
            "published_preview_prefix_len": published_preview_prefix_len,
            "preview_version": preview_version,
            "updated": False,
            "preview_path": None,
            "preview_sections": contiguous_prefix_len,
            "delivery_type": "hls" if str(MANIM_SETTINGS.delivery.mode).strip().lower() == "hls_cos_cdn" else "mp4",
            "error": "",
        }

    preview_dir = output_dir / "preview"
    next_version = preview_version + 1
    ordered_results = [
        completed_results_by_order[order]
        for order in sorted(completed_results_by_order)
    ]
    delivery_type = "mp4"
    if str(MANIM_SETTINGS.delivery.mode).strip().lower() == "hls_cos_cdn":
        delivery_type = "hls"
        preview_sections, preview_path, error = build_incremental_hls_preview(
            ordered_results,
            output_dir / "hls",
            preview_version=next_version,
            target_segment_seconds=MANIM_SETTINGS.delivery.hls_segment_seconds,
        )
    else:
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / f"preview_v{next_version:02d}.mp4"
        preview_sections, error = build_incremental_preview_video(ordered_results, preview_path)
    if error:
        return {
            "published_preview_prefix_len": published_preview_prefix_len,
            "preview_version": preview_version,
            "updated": False,
            "preview_path": None,
            "preview_sections": preview_sections,
            "delivery_type": delivery_type,
            "error": error,
        }

    return {
        "published_preview_prefix_len": preview_sections,
        "preview_version": next_version,
        "updated": True,
        "preview_path": preview_path,
        "preview_sections": preview_sections,
        "delivery_type": delivery_type,
        "error": "",
    }


def _resolve_streaming_render_workers(
    *,
    configured_cap: int,
    manifest_section_count: int,
) -> int:
    if configured_cap > 0:
        return max(1, configured_cap)
    return max(1, manifest_section_count)


def _emit_stage_started(
    callback: Callable[[ManimStreamEvent], None] | None,
    *,
    stage: ManimStreamEventStage,
    run_id: str,
    message: str,
    progress: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    _emit_pipeline_event(
        callback,
        event_type=ManimStreamEventType.STAGE_STARTED,
        stage=stage,
        message=message,
        run_id=run_id,
        progress=progress,
        extra=extra,
    )


def _emit_stage_completed(
    callback: Callable[[ManimStreamEvent], None] | None,
    *,
    stage: ManimStreamEventStage,
    run_id: str,
    message: str,
    progress: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    _emit_pipeline_event(
        callback,
        event_type=ManimStreamEventType.STAGE_COMPLETED,
        stage=stage,
        message=message,
        run_id=run_id,
        progress=progress,
        extra=extra,
    )


def _make_render_event_bridge(
    callback: Callable[[ManimStreamEvent], None] | None,
    *,
    run_id: str,
) -> Callable[[dict[str, Any]], None] | None:
    if callback is None:
        return None

    tts_progress_floor = 61
    tts_progress_ceiling = 75
    render_progress_floor = 75
    render_progress_ceiling = 90

    def _map_ratio_to_progress(done: Any, total: Any, floor: int, ceiling: int) -> int | None:
        if not (isinstance(total, int) and total > 0 and isinstance(done, int)):
            return None
        clamped_done = max(0, min(done, total))
        span = max(0, ceiling - floor)
        return floor + int(clamped_done * span / total)

    def _bridge(payload: dict[str, Any]) -> None:
        kind = str(payload.get("kind") or "").strip().lower()
        if kind in {"tts_started", "tts_progress"}:
            total = payload.get("tts_total")
            done = payload.get("tts_done")
            progress = _map_ratio_to_progress(
                done,
                total,
                tts_progress_floor,
                tts_progress_ceiling,
            )
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.STAGE_PROGRESS,
                stage=ManimStreamEventStage.TTS,
                message="TTS progress",
                run_id=run_id,
                progress=progress,
                extra=payload,
            )
            return

        if kind == "section_tts_started":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_TTS_STARTED,
                stage=ManimStreamEventStage.TTS,
                message=f"Section TTS started: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=tts_progress_floor,
                extra=payload,
            )
            return

        if kind == "section_tts_completed":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_TTS_COMPLETED,
                stage=ManimStreamEventStage.TTS,
                message=f"Section TTS completed: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=tts_progress_ceiling if bool(payload.get("success", False)) else tts_progress_floor,
                extra=payload,
            )
            return

        if kind == "render_in_progress":
            raw_progress = payload.get("progress")
            progress = raw_progress if isinstance(raw_progress, int) else render_progress_floor
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.STAGE_PROGRESS,
                stage=ManimStreamEventStage.RENDER,
                message=str(payload.get("message") or "视频渲染中"),
                run_id=run_id,
                progress=progress,
                extra=payload,
            )
            return

        if kind == "segment_started":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SEGMENT_STARTED,
                stage=ManimStreamEventStage.RENDER,
                message=f"Segment started: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=render_progress_floor,
                extra=payload,
            )
            return

        if kind == "section_render_started":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_RENDER_STARTED,
                stage=ManimStreamEventStage.RENDER,
                message=f"Section render started: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=render_progress_floor,
                extra=payload,
            )
            return

        if kind == "section_render_completed":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_RENDER_COMPLETED,
                stage=ManimStreamEventStage.RENDER,
                message=f"Section render completed: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=render_progress_floor if not bool(payload.get("success", False)) else render_progress_ceiling,
                extra=payload,
            )
            return

        if kind == "section_validation_started":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_VALIDATION_STARTED,
                stage=ManimStreamEventStage.CODEGEN,
                message=f"Section validation started: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=50,
                extra=payload,
            )
            return

        if kind == "section_validation_completed":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_VALIDATION_COMPLETED,
                stage=ManimStreamEventStage.CODEGEN,
                message=f"Section validation completed: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=55,
                extra=payload,
            )
            return

        if kind == "section_validation_failed":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_VALIDATION_FAILED,
                stage=ManimStreamEventStage.CODEGEN,
                message=f"Section validation failed: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=55,
                extra=payload,
            )
            return

        if kind == "section_fix_started":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_FIX_STARTED,
                stage=ManimStreamEventStage.CODEGEN,
                message=f"Section fix started: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=53,
                extra=payload,
            )
            return

        if kind == "section_fix_completed":
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SECTION_FIX_COMPLETED,
                stage=ManimStreamEventStage.CODEGEN,
                message=f"Section fix completed: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=55,
                extra=payload,
            )
            return

        if kind == "segment_completed":
            total = payload.get("segment_total")
            done = payload.get("segment_done")
            progress = _map_ratio_to_progress(
                done,
                total,
                render_progress_floor,
                render_progress_ceiling,
            )
            _emit_pipeline_event(
                callback,
                event_type=ManimStreamEventType.SEGMENT_COMPLETED,
                stage=ManimStreamEventStage.RENDER,
                message=f"Segment completed: {payload.get('scene_name') or payload.get('segment_id') or 'unknown'}",
                run_id=run_id,
                progress=progress,
                extra=payload,
            )

    return _bridge


def _validate_ready_streaming_section(
    *,
    payload: dict[str, Any],
    code_eval_agent: CodeEvalAgent,
    agent: CodeGenAgent,
    output_language: str,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    segment_id = str(payload.get("segment_id") or "")
    scene_name = str(payload.get("scene_name") or "")
    order = int(payload.get("order", 0))
    scene_file = Path(payload["scene_file"])
    if event_callback is not None:
        event_callback(
            {
                "kind": "section_validation_started",
                "segment_id": segment_id,
                "scene_name": scene_name,
                "segment_order": order,
            }
        )

    result = validate_and_fix_streaming_scene_file(
        code_eval_agent=code_eval_agent,
        agent=agent,
        scene_file=scene_file,
        output_language=output_language,
        max_attempts=CODE_EVAL_FIX_MAX_ATTEMPTS,
        segment_id=segment_id,
        scene_name=scene_name,
        order=order,
        event_callback=event_callback,
    )

    updated_payload = {
        "segment_id": segment_id,
        "scene_name": scene_name,
        "order": order,
        "scene_file": result["scene_file"],
        "validation_report": result["validation_report"],
        "validation_attempts": result["validation_attempts"],
    }

    validation_summary = ""
    report_raw = result.get("validation_report")
    if isinstance(report_raw, dict):
        validation_summary = str(report_raw.get("summary") or "")

    if event_callback is not None:
        event_callback(
            {
                "kind": "section_validation_completed" if result["passed"] else "section_validation_failed",
                "segment_id": segment_id,
                "scene_name": scene_name,
                "segment_order": order,
                "success": bool(result["passed"]),
                "validation_attempts": int(result.get("validation_attempts", 0)),
                "validation_summary": validation_summary or str(result.get("error") or ""),
            }
        )

    return {
        "passed": bool(result["passed"]),
        "payload": updated_payload,
        "validation_report": result["validation_report"],
        "error": str(result.get("error") or ""),
    }


def _segment_infos(render: RenderResult) -> List[Dict[str, Any]]:
    infos: List[Dict[str, Any]] = []
    for segment in sorted(render.segments, key=lambda item: item.order):
        info: Dict[str, Any] = {
            "order": segment.order,
            "segment_id": segment.segment_id,
            "scene_name": segment.scene_name,
            "success": segment.success,
            "video": str(segment.video_path) if segment.video_path else None,
            "output_dir": str(segment.output_dir),
        }
        if segment.error_log:
            info["error"] = segment.error_log[-1500:]
        infos.append(info)
    return infos


def _round_info(n: int, render: RenderResult, report: Optional[Dict]) -> Dict:
    info = {
        "round": n,
        "render_success": render.success,
        "video": str(render.video_path) if render.video_path else None,
        "eval_score": report.get("overall_score") if report else None,
        "eval_passed": report.get("overall_passed") if report else None,
        "segments": _segment_infos(render),
    }
    if render.error_log:
        info["render_warning" if render.success else "render_error"] = render.error_log[-1500:]
    return info


def _build_eval_meta(
    *,
    render_at_1: Optional[bool],
    render_at_final: Optional[bool],
    repair_rounds: int,
    time_total_sec: Optional[float],
    time_per_stage: Dict[str, float],
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "render_at_1": render_at_1,
        "render_at_final": render_at_final,
        "time_total_sec": time_total_sec,
        "time_per_stage": time_per_stage or None,
    }
    if repair_rounds > 0:
        meta["repair_rounds"] = repair_rounds
        meta["fix_rate"] = 1.0 if render_at_final else 0.0
    return meta


def _build_manim_teaching_plan(
    teaching_plan: Dict[str, Any],
    storyboard: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not storyboard:
        return teaching_plan

    scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    manim_section_ids = [
        str(scene.get("source_section_id"))
        for scene in scenes
        if isinstance(scene, dict)
        and scene.get("type") == "manim_chunk"
        and scene.get("source_section_id")
    ]
    if not manim_section_ids:
        return teaching_plan

    sections = teaching_plan.get("sections") if isinstance(teaching_plan.get("sections"), list) else []
    by_id = {
        str(section.get("id")): section
        for section in sections
        if isinstance(section, dict) and section.get("id")
    }
    filtered_sections = [by_id[section_id] for section_id in manim_section_ids if section_id in by_id]
    if not filtered_sections:
        return teaching_plan

    concept_section_ids = {
        str(scene.get("source_section_id"))
        for scene in scenes
        if isinstance(scene, dict)
        and scene.get("type") == "concept_card"
        and scene.get("source_section_id")
    }

    plan = dict(teaching_plan)
    plan["sections"] = filtered_sections
    plan["hybrid_routes"] = {
        "manim_section_ids": manim_section_ids,
        "remotion_section_ids": sorted(concept_section_ids),
    }
    plan["teaching_promise"] = (
        f"{teaching_plan.get('teaching_promise', '')} "
        "In this hybrid lesson, only the mathematically dense sections below should be rendered in Manim."
    ).strip()
    return plan


# =====================================================================
# Main pipeline
# =====================================================================


def run_pipeline(
    request_text: str,
    image_path: Optional[Path] = None,
    run_dir: Optional[Path] = None,
    language: Optional[str] = None,
    render_backend: str = "manim",
    quality_flags: Optional[str] = None,
    event_callback: Callable[[ManimStreamEvent], None] | None = None,
    debug_callback: Callable[[dict[str, Any]], None] | None = None,
) -> Dict:
    """Execute the single-round generate-render pipeline.

    Args:
        render_backend: "manim" for pure Manim delivery, or "hybrid" for
            Remotion-wrapped delivery with chapter cards and transitions.
    """
    _configure_pipeline_http_logging()
    stage_times: Dict[str, float] = {}
    output_language = normalize_output_language(language, DEFAULT_OUTPUT_LANGUAGE)

    if run_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = RUNS_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_dir.name
    pipeline_started_at = time.time()
    stream_timing: Dict[str, float] = {}
    analysis_debug_char_count = 0
    code_debug_char_count = 0
    analysis_stream_path = run_dir / "analysis_stream.txt"
    code_stream_path = run_dir / "code_stream.py.partial"
    stream_timing_path = run_dir / "stream_timing.json"

    (run_dir / "request.txt").write_text(request_text, encoding="utf-8")
    if image_path and image_path.exists():
        shutil.copy2(str(image_path), str(run_dir / f"request_image{image_path.suffix}"))

    _emit_pipeline_event(
        event_callback,
        event_type=ManimStreamEventType.TASK_STARTED,
        stage=ManimStreamEventStage.UNKNOWN,
        message="Manim pipeline started",
        run_id=run_id,
        progress=0,
        extra={"render_backend": render_backend},
    )

    try:
        _pipeline_io_begin()
        llm_configs = resolve_pipeline_llm_configs()
        required_stages = ("analysis", "code", "director") if render_backend == "hybrid" else ("analysis", "code")
        validate_pipeline_llm_configs(llm_configs, required_stages=required_stages)
        analysis_llm = llm_configs["analysis"]
        code_llm = llm_configs["code"]
        routing_msg = (
            f"analysis={analysis_llm.provider}/{analysis_llm.model}, "
            f"code={code_llm.provider}/{code_llm.model}"
        )
        if render_backend == "hybrid":
            director_llm_preview = llm_configs["director"]
            routing_msg += f", director={director_llm_preview.provider}/{director_llm_preview.model}"
        _log(f"LLM routing: {routing_msg}")
        _log(f"Output language: {output_language_name(output_language)} ({output_language})")
        effective_quality_flags = (quality_flags or ROUND1_MANIM_QUALITY).strip() or ROUND1_MANIM_QUALITY
        _log(f"Render backend: {render_backend}")
        _log(f"Render quality flags: {effective_quality_flags}")

        llm_routing_path = run_dir / "llm_routing.json"
        llm_routing_path.write_text(
            json.dumps(
                {
                    "analysis": analysis_llm.summary(),
                    "code": code_llm.summary(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        _emit_stage_started(
            event_callback,
            stage=ManimStreamEventStage.PLANNING,
            run_id=run_id,
            message="Teaching planner started",
            progress=5,
        )
        _log("教学规划: 正在调用 AI 分析课程结构 …")
        _log("Teaching planner: building lesson structure ...")
        stage_started_at = time.time()
        planner = TeachingPlannerAgent(analysis_llm)
        style_selector = ExplanationStyleSelector(analysis_llm)
        agent = CodeGenAgent(code_llm)
        code_eval_agent = CodeEvalAgent(analysis_llm)
        analysis_delta_bridge = _make_text_delta_event_bridge(
            event_callback,
            run_id=run_id,
            event_type=ManimStreamEventType.ANALYSIS_DELTA,
            stage=ManimStreamEventStage.ANALYSIS,
            message="Teaching analysis delta",
        )
        def _analysis_delta_and_persist(delta: str) -> None:
            nonlocal analysis_debug_char_count
            _record_timing_once(stream_timing, "t_first_analysis_delta", pipeline_started_at)
            _append_stream_text(analysis_stream_path, delta)
            _write_json_debug(stream_timing_path, stream_timing)
            analysis_debug_char_count += len(delta)
            _emit_debug_observation(
                debug_callback,
                {
                    "type": "analysis_delta",
                    "run_id": run_id,
                    "delta": delta,
                    "char_count": analysis_debug_char_count,
                },
            )
            if analysis_delta_bridge is not None:
                analysis_delta_bridge(delta)
        def _analysis_llm_event(payload: dict[str, Any]) -> None:
            _emit_debug_observation(debug_callback, payload)
        fast_path_used = False
        fast_path_template_id = ""
        fast_path_reference_code = ""
        teaching_plan: Dict[str, Any]
        selected_theme: Dict[str, Any] | None = None
        skip_deterministic_fast_path = should_skip_fast_path_for_request(request_text)
        if MANIM_SETTINGS.render.deterministic_fast_path_enabled and not skip_deterministic_fast_path:
            selected_theme = {"theme_id": "mist_blue_focus", "display_name": "Mist Blue Focus"}
            fast_path = None
            try:
                style_pick = style_selector.select(
                    request_text,
                    on_event=_analysis_llm_event,
                )
                picked_category_id = str(style_pick.get("category_id") or "").strip()
                if picked_category_id:
                    fast_path = build_fast_path_plan_and_code_for_category(
                        picked_category_id,
                        selected_theme=selected_theme,
                    )
            except Exception as exc:
                _emit_debug_observation(
                    debug_callback,
                    {
                        "type": "style_selector_failed",
                        "run_id": run_id,
                        "error": str(exc),
                    },
                )
            if fast_path is None:
                fast_path = maybe_build_fast_path_plan_and_code(
                    request_text,
                    output_language=output_language,
                    selected_theme=selected_theme,
                )
            if fast_path is not None:
                teaching_plan, fast_path_reference_code = fast_path
                fast_path_used = True
                fast_path_template_id = str(
                    teaching_plan.get("fast_path", {}).get("template_id") or "deterministic_fast_path"
                )
                _record_timing_once(stream_timing, "t_first_analysis_delta", pipeline_started_at)
                _append_stream_text(analysis_stream_path, json.dumps(teaching_plan, ensure_ascii=False))
                _write_json_debug(stream_timing_path, stream_timing)
                _emit_debug_observation(
                    debug_callback,
                    {
                        "type": "analysis_delta",
                        "run_id": run_id,
                        "delta": "[FAST_PATH_PLAN]",
                        "char_count": len(json.dumps(teaching_plan, ensure_ascii=False)),
                    },
                )
                _log(f"Teaching planner: deterministic fast path matched ({fast_path_template_id})")
            else:
                teaching_plan = _call_with_optional_on_event(
                    planner.plan,
                    request_text,
                    image_path,
                    on_delta=_analysis_delta_and_persist,
                        on_event=_analysis_llm_event,
                )
        else:
            teaching_plan = _call_with_optional_on_event(
                planner.plan,
                request_text,
                image_path,
                on_delta=_analysis_delta_and_persist,
                on_event=_analysis_llm_event,
            )
        stage_times["planning"] = time.time() - stage_started_at
        _log(f"Teaching planner: {len(teaching_plan.get('sections', []))} section(s) ready")
        _emit_pipeline_event(
            event_callback,
            event_type=ManimStreamEventType.ANALYSIS_COMPLETED,
            stage=ManimStreamEventStage.ANALYSIS,
            message="Teaching analysis completed",
            run_id=run_id,
            extra={
                "section_count": len(teaching_plan.get("sections", [])),
                "fast_path_used": fast_path_used,
                "fast_path_template_id": fast_path_template_id or None,
            },
        )
        _emit_stage_completed(
            event_callback,
            stage=ManimStreamEventStage.PLANNING,
            run_id=run_id,
            message="Teaching planner completed",
            progress=15,
            extra={"section_count": len(teaching_plan.get("sections", []))},
        )

        _emit_stage_started(
            event_callback,
            stage=ManimStreamEventStage.THEME_SELECTION,
            run_id=run_id,
            message="Theme selection started",
            progress=16,
        )
        _log("主题: 正在匹配课件主题 …")
        _log("Theme resolver: selecting lesson theme ...")
        stage_started_at = time.time()
        if fast_path_used and selected_theme is not None:
            stage_times["theme_selection"] = time.time() - stage_started_at
            teaching_plan["selected_theme"] = selected_theme
        else:
            selected_theme = resolve_theme(request_text, teaching_plan)
            stage_times["theme_selection"] = time.time() - stage_started_at
            teaching_plan["selected_theme"] = selected_theme
        _log(
            "Theme resolver: selected "
            f"{selected_theme['theme_id']} ({selected_theme['display_name']})"
        )
        _emit_stage_completed(
            event_callback,
            stage=ManimStreamEventStage.THEME_SELECTION,
            run_id=run_id,
            message="Theme selection completed",
            progress=25,
            extra={"theme_id": selected_theme["theme_id"]},
        )

        assets_info: Dict[str, Any] = {
            "enabled": USE_LOCAL_ICONS,
            "icon_dir": str((ROOT_DIR / "icon").resolve()),
            "available_icon_count": 0,
            "selected_assets": [],
        }
        _emit_stage_started(
            event_callback,
            stage=ManimStreamEventStage.ASSET_SELECTION,
            run_id=run_id,
            message="Asset selection started",
            progress=26,
        )
        if USE_LOCAL_ICONS:
            _log("素材解析: 正在调用 AI 选择本地图标 …")
            _log("Asset resolver: selecting local icons ...")
            stage_started_at = time.time()
            try:
                assets_info = resolve_local_assets(
                    request_text,
                    teaching_plan,
                    llm_config=analysis_llm,
                )
                selected_assets = assets_info.get("selected_assets", [])
                teaching_plan["selected_assets"] = selected_assets
                stage_times["asset_selection"] = time.time() - stage_started_at
                _log(f"Asset resolver: selected {len(selected_assets)} icon(s)")
            except Exception as exc:
                stage_times["asset_selection"] = time.time() - stage_started_at
                teaching_plan["selected_assets"] = []
                _log(f"Asset resolver: skipped due to error - {exc}")
        else:
            assets_info["disabled_reason"] = "disabled_in_settings"
            teaching_plan["selected_assets"] = []
        _emit_stage_completed(
            event_callback,
            stage=ManimStreamEventStage.ASSET_SELECTION,
            run_id=run_id,
            message="Asset selection completed",
            progress=30,
            extra={
                "enabled": bool(USE_LOCAL_ICONS),
                "selected_asset_count": len(teaching_plan.get("selected_assets", [])),
            },
        )

        selected_assets_path = run_dir / "selected_assets.json"
        selected_assets_path.write_text(
            json.dumps(assets_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        selected_theme_path = run_dir / "selected_theme.json"
        selected_theme_path.write_text(
            json.dumps(selected_theme, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        teaching_plan_path = run_dir / "teaching_plan.json"
        teaching_plan_path.write_text(
            json.dumps(teaching_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        storyboard: Optional[Dict[str, Any]] = None
        storyboard_path: Optional[Path] = None
        manim_teaching_plan = teaching_plan
        manim_plan_path: Optional[Path] = None
        if render_backend == "hybrid":
            _emit_stage_started(
                event_callback,
                stage=ManimStreamEventStage.STORYBOARD,
                run_id=run_id,
                message="Storyboard generation started",
                progress=31,
            )
            director_llm = llm_configs["director"]
            _log("分镜/导演: 正在调用 AI 规划 Remotion 混合成片 …")
            _log("Director: drafting hybrid Remotion assembly plan ...")
            stage_started_at = time.time()
            director = StoryboardAgent(director_llm)
            storyboard = director.plan(request_text, teaching_plan)
            stage_times["director"] = time.time() - stage_started_at
            storyboard_path = run_dir / "storyboard.json"
            storyboard_path.write_text(
                json.dumps(storyboard, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _log(f"Director: {len(storyboard.get('scenes', []))} scene(s) planned")
            manim_teaching_plan = _build_manim_teaching_plan(teaching_plan, storyboard)
            manim_plan_path = run_dir / "manim_teaching_plan.json"
            manim_plan_path.write_text(
                json.dumps(manim_teaching_plan, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _emit_stage_completed(
                event_callback,
                stage=ManimStreamEventStage.STORYBOARD,
                run_id=run_id,
                message="Storyboard generation completed",
                progress=40,
                extra={"scene_count": len(storyboard.get("scenes", []))},
            )

        summary: Dict[str, Any] = {
            "request": request_text,
            "output_language": output_language,
            "render_backend": render_backend,
            "quality_flags": effective_quality_flags,
            "image": str(image_path) if image_path else None,
            "teaching_plan_file": str(teaching_plan_path),
            "selected_theme_file": str(selected_theme_path),
            "selected_theme_id": selected_theme["theme_id"],
            "selected_theme_reason": selected_theme.get("reason"),
            "selected_assets_file": str(selected_assets_path),
            "selected_assets_count": len(teaching_plan.get("selected_assets", [])),
            "fast_path_used": fast_path_used,
            "fast_path_template_id": fast_path_template_id or None,
            "storyboard_file": str(storyboard_path) if storyboard_path else None,
            "manim_teaching_plan_file": str(manim_plan_path) if manim_plan_path else str(teaching_plan_path),
            "hybrid_routes": manim_teaching_plan.get("hybrid_routes"),
            "llm_routing_file": str(llm_routing_path),
            "analysis_llm": analysis_llm.summary(),
            "code_llm": code_llm.summary(),
            "rounds": [],
            "final_video": None,
            "final_video_with_audio": None,
            "delivery_video": None,
            "hybrid_delivery": None,
            "final_score": None,
            "final_passed": None,
            "stream_run_id": run_id,
            "streaming_sections": [],
            "streaming_section_counts": {
                "ready": 0,
                "validated": 0,
                "validation_failed": 0,
                "pre_rendered": 0,
            },
            "streaming_preview": {
                "version": 0,
                "published_prefix_len": 0,
                "latest_preview_path": None,
                "history": [],
                "last_error": None,
            },
            "codegen_warnings": [],
        }

        _emit_stage_started(
            event_callback,
            stage=ManimStreamEventStage.CODEGEN,
            run_id=run_id,
            message="Manim code generation started",
            progress=41,
        )
        r1_dir = run_dir / "round1"
        r1_dir.mkdir(parents=True, exist_ok=True)
        raw_render_event_bridge = _make_render_event_bridge(event_callback, run_id=run_id)
        def render_event_bridge(payload: dict[str, Any]) -> None:
            kind = str(payload.get("kind") or "").strip().lower()
            segment_id = str(payload.get("segment_id") or "").strip()
            if kind == "section_validation_started" and segment_id:
                _record_timing_once(stream_timing, "t_first_section_validation_started", pipeline_started_at)
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_validation_started",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            elif kind == "section_validation_completed" and segment_id:
                _record_timing_once(stream_timing, "t_first_section_validation_completed", pipeline_started_at)
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_validation_completed",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            elif kind == "section_validation_failed" and segment_id:
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_validation_failed",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            elif kind == "section_fix_completed" and segment_id:
                _record_timing_once(stream_timing, "t_first_section_fix_completed", pipeline_started_at)
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_fix_completed",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            elif kind == "section_tts_started" and segment_id:
                _record_timing_once(stream_timing, "t_first_section_tts_started", pipeline_started_at)
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_tts_started",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            elif kind == "section_tts_completed" and segment_id:
                _record_timing_once(stream_timing, "t_first_section_tts_completed", pipeline_started_at)
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_tts_completed",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            elif kind == "section_render_started" and segment_id:
                _record_timing_once(stream_timing, "t_first_section_render_started", pipeline_started_at)
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_render_started",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            elif kind == "section_render_completed" and segment_id:
                _record_timing_once(stream_timing, "t_first_section_render_completed", pipeline_started_at)
                _write_streaming_section_status(
                    run_dir,
                    segment_id=segment_id,
                    scene_name=payload.get("scene_name"),
                    stage="section_render_completed",
                    payload=payload,
                )
                _write_json_debug(stream_timing_path, stream_timing)
            if raw_render_event_bridge is not None:
                raw_render_event_bridge(payload)
        tts_voice = voice_for_language(output_language)
        stream_buffer = ScenePackStreamBuffer()
        seg_cap = MANIM_SETTINGS.segment_render_workers
        coordinator: SectionPipelineCoordinator | None = None
        completed_preview_results_by_order: dict[int, SegmentRenderResult] = {}
        published_preview_prefix_len = 0
        preview_version = 0
        manifest_section_count = 0

        def _on_section_render_completed(state: Any) -> None:
            nonlocal published_preview_prefix_len, preview_version
            render_result = _state_render_result(state)
            _emit_debug_observation(
                debug_callback,
                {
                    "type": "section_render_completed",
                    "run_id": run_id,
                    "segment_id": render_result.segment_id,
                    "scene_name": render_result.scene_name,
                    "segment_order": render_result.order,
                    "success": getattr(state, "status", "") == "done",
                    "video_path": str(render_result.video_path) if render_result.video_path else None,
                    "output_dir": str(render_result.output_dir) if render_result.output_dir else None,
                },
            )
            if getattr(state, "status", "") != "done":
                return
            try:
                completed_preview_results_by_order[render_result.order] = render_result
                preview_update = _maybe_build_incremental_preview(
                    output_dir=r1_dir,
                    completed_results_by_order=completed_preview_results_by_order,
                    published_preview_prefix_len=published_preview_prefix_len,
                    preview_version=preview_version,
                )
                if not preview_update["updated"]:
                    if preview_update["error"]:
                        summary["streaming_preview"]["last_error"] = str(preview_update["error"])
                    return
                published_preview_prefix_len = int(preview_update["published_preview_prefix_len"])
                preview_version = int(preview_update["preview_version"])
                latest_preview_path = preview_update["preview_path"]
                summary["streaming_preview"]["version"] = preview_version
                summary["streaming_preview"]["published_prefix_len"] = published_preview_prefix_len
                summary["streaming_preview"]["latest_preview_path"] = str(latest_preview_path) if latest_preview_path else None
                summary["streaming_preview"]["last_error"] = None
                summary["streaming_preview"]["history"].append(
                    {
                        "version": preview_version,
                        "preview_sections": int(preview_update["preview_sections"]),
                        "preview_path": str(latest_preview_path) if latest_preview_path else None,
                    }
                )
                if manifest_section_count > 0:
                    render_progress = 61 + int(
                        min(preview_update["preview_sections"], manifest_section_count) * 29 / manifest_section_count
                    )
                    _emit_pipeline_event(
                        event_callback,
                        event_type=ManimStreamEventType.STAGE_PROGRESS,
                        stage=ManimStreamEventStage.RENDER,
                        message="Incremental preview updated",
                        run_id=run_id,
                        progress=render_progress,
                        extra={
                            "preview_sections": int(preview_update["preview_sections"]),
                            "total_sections_expected": manifest_section_count,
                            "preview_version": preview_version,
                        },
                    )
                _emit_debug_observation(
                    debug_callback,
                    {
                        "type": "preview_updated",
                        "run_id": run_id,
                        "preview_path": str(latest_preview_path) if latest_preview_path else None,
                        "preview_version": preview_version,
                        "preview_sections": int(preview_update["preview_sections"]),
                    },
                )
                if published_preview_prefix_len == 1:
                    _record_timing_once(stream_timing, "t_first_preview_built", pipeline_started_at)
                    _write_json_debug(stream_timing_path, stream_timing)
            except Exception as exc:
                summary["streaming_preview"]["last_error"] = str(exc)

        def _ensure_streaming_coordinator(manifest_section_count: int) -> SectionPipelineCoordinator:
            nonlocal coordinator
            if coordinator is not None:
                return coordinator
            coordinator = SectionPipelineCoordinator(
                validate_fix_fn=lambda payload: _validate_ready_streaming_section(
                    payload=payload,
                    code_eval_agent=code_eval_agent,
                    agent=agent,
                    output_language=output_language,
                    event_callback=render_event_bridge,
                ),
                tts_prepare_fn=lambda payload: prepare_segment_tts_assets(
                    _read_streaming_scene_file_code(Path(payload["scene_file"])),
                    r1_dir,
                    segment_id=payload["segment_id"],
                    tts_voice=tts_voice,
                    event_callback=render_event_bridge,
                ),
                render_fn=lambda payload: render_streaming_scene_pack_segment_with_repair(
                    _read_streaming_scene_file_code(Path(payload["scene_file"])),
                    r1_dir,
                    segment_id=payload["segment_id"],
                    order=payload["order"],
                    quality_flags=effective_quality_flags,
                    tts_voice=tts_voice,
                    scene_file=payload.get("scene_file"),
                    agent=agent,
                    code_eval_agent=code_eval_agent,
                    output_language=output_language,
                    max_render_fix_attempts=RENDER_FIX_MAX_ATTEMPTS,
                    max_validation_fix_attempts=CODE_EVAL_FIX_MAX_ATTEMPTS,
                    event_callback=render_event_bridge,
                ),
                event_callback=render_event_bridge,
                on_section_render_completed=_on_section_render_completed,
                validate_workers=max(1, min(2, manifest_section_count)),
                tts_workers=max(1, MANIM_SETTINGS.tts_process_threads),
                render_workers=_resolve_streaming_render_workers(
                    configured_cap=seg_cap,
                    manifest_section_count=manifest_section_count,
                ),
                tts_retry_attempts=max(0, int(MANIM_SETTINGS.tts_section_retry_attempts)),
                allow_render_on_tts_failure=bool(MANIM_SETTINGS.allow_render_without_tts),
            )
            return coordinator
        _log("代码生成: 正在调用 AI 生成 Manim 场景代码 …")
        _log("Round 1: generating Manim code ...")
        stage_started_at = time.time()
        code_delta_event_bridge = _make_text_delta_event_bridge(
            event_callback,
            run_id=run_id,
            event_type=ManimStreamEventType.CODE_DELTA,
            stage=ManimStreamEventStage.CODEGEN,
            message="Manim code delta",
        )

        def _code_delta_bridge(delta: str) -> None:
            nonlocal code_debug_char_count, manifest_section_count
            _record_timing_once(stream_timing, "t_first_code_delta", pipeline_started_at)
            _append_stream_text(code_stream_path, delta)
            _write_json_debug(stream_timing_path, stream_timing)
            code_debug_char_count += len(delta)
            _emit_debug_observation(
                debug_callback,
                {
                    "type": "code_delta",
                    "run_id": run_id,
                    "delta": delta,
                    "char_count": code_debug_char_count,
                },
            )
            if code_delta_event_bridge is not None:
                code_delta_event_bridge(delta)
            snapshot = stream_buffer.append(delta)
            if snapshot.all_reports:
                manifest_section_count = max(manifest_section_count, len(snapshot.all_reports))
                _ensure_streaming_coordinator(len(snapshot.all_reports))
            for report in snapshot.newly_ready:
                try:
                    scene_file = (
                        r1_dir
                        / "streaming_scene_files"
                        / f"{report.segment.order:02d}_{report.segment.segment_id}.py"
                    )
                    _, resolved_scene_file, segment_code = write_streaming_scene_pack_segment_file(
                        snapshot.parseable_prefix,
                        r1_dir,
                        segment_id=report.segment.segment_id,
                        order=report.segment.order,
                        tts_voice=tts_voice,
                        scene_file=scene_file,
                    )
                    _emit_section_ready_event(
                        event_callback=event_callback,
                        run_id=run_id,
                        run_dir=run_dir,
                        stream_timing=stream_timing,
                        stream_timing_path=stream_timing_path,
                        pipeline_started_at=pipeline_started_at,
                        segment_id=report.segment.segment_id,
                        scene_name=report.segment.scene_name,
                        method_name=report.segment.method_name,
                        order=report.segment.order,
                        ready_sections=len(stream_buffer.ready_segment_ids),
                        resolved_scene_file=resolved_scene_file,
                    )
                    _ensure_streaming_coordinator(len(snapshot.all_reports)).submit_ready_section(
                        {
                            "segment_id": report.segment.segment_id,
                            "scene_name": report.segment.scene_name,
                            "order": report.segment.order,
                            "scene_file": resolved_scene_file,
                        }
                    )
                    stream_buffer.mark_submitted(report.segment.segment_id)
                except Exception as exc:
                    _write_streaming_submit_failure(
                        run_dir,
                        segment_id=report.segment.segment_id,
                        scene_name=report.segment.scene_name,
                        order=report.segment.order,
                        parseable_prefix=snapshot.parseable_prefix,
                        error=str(exc),
                    )
                    _log(
                        f"Code streaming: failed to submit ready section "
                        f"{report.segment.segment_id} - {exc}"
                    )

        def _code_llm_event(payload: dict[str, Any]) -> None:
            _emit_debug_observation(debug_callback, payload)

        if fast_path_used and fast_path_reference_code:
            manim_teaching_plan = dict(manim_teaching_plan)
            manim_teaching_plan["fast_path_reference"] = {
                "template_code": fast_path_reference_code,
                "fusion_required": True,
            }
        code = _call_with_optional_on_event(
            agent.generate,
            request_text,
            image_path,
            teaching_plan=manim_teaching_plan,
            output_language=output_language,
            on_delta=_code_delta_bridge,
            on_event=_code_llm_event,
        )
        expected_segment_count: int | None = None
        sanitized_code = sanitize_streaming_code(code)
        codegen_failure_reason = ""
        codegen_failure_message = ""
        try:
            expected_segment_count = len(parse_scene_pack(sanitized_code).manifest)
        except Exception as exc:
            codegen_failure_reason = "codegen_unparseable"
            codegen_failure_message = f"Code generation produced an unparseable Scene Pack: {exc}"
            repaired_codegen = _attempt_codegen_repair(
                agent=agent,
                code=sanitized_code,
                codegen_failure_message=codegen_failure_message,
                output_language=output_language,
            )
            if repaired_codegen:
                sanitized_code = repaired_codegen
                code = repaired_codegen
                codegen_failure_reason = ""
                codegen_failure_message = ""
                expected_segment_count = len(parse_scene_pack(sanitized_code).manifest)
        if not codegen_failure_reason and expected_segment_count <= 0:
            codegen_failure_reason = "codegen_truncated"
            codegen_failure_message = "Code generation finished without any renderable Scene Pack sections."
        if not codegen_failure_reason:
            _materialize_missing_streaming_sections(
                code=code,
                submitted_segment_ids=set(stream_buffer.ready_segment_ids),
                ensure_coordinator=_ensure_streaming_coordinator,
                r1_dir=r1_dir,
                tts_voice=tts_voice,
                event_callback=event_callback,
                run_id=run_id,
                run_dir=run_dir,
                stream_timing=stream_timing,
                stream_timing_path=stream_timing_path,
                pipeline_started_at=pipeline_started_at,
            )
        stage_times["round1_codegen"] = time.time() - stage_started_at
        if coordinator is not None:
            coordinator.close_submissions()
            streaming_states = coordinator.wait_until_complete()
        else:
            streaming_states = []
        _write_streaming_section_statuses(run_dir, streaming_states)
        summary["streaming_sections"] = [
            _streaming_section_state_summary(state)
            for state in streaming_states
        ]
        summary["streaming_section_counts"] = {
            "ready": len(streaming_states),
            "validated": sum(
                1
                for item in summary["streaming_sections"]
                if bool(item.get("validated"))
            ),
            "validation_failed": sum(
                1
                for item in summary["streaming_sections"]
                if bool(item.get("validation_failed"))
            ),
            "pre_rendered": sum(
                1
                for item in summary["streaming_sections"]
                if item.get("status") == "done"
            ),
        }
        if codegen_failure_reason:
            rendered_section_count = sum(
                1
                for state in streaming_states
                if getattr(state, "status", "") == "done"
                and getattr(getattr(state, "render_result", None), "success", False)
            )
            if rendered_section_count <= 0:
                _emit_pipeline_event(
                    event_callback,
                    event_type=ManimStreamEventType.TASK_FAILED,
                    stage=ManimStreamEventStage.CODEGEN,
                    message=codegen_failure_message,
                    run_id=run_id,
                    extra={
                        "reason": codegen_failure_reason,
                        "partial_chars": len(code),
                        "ready_sections": len(streaming_states),
                    },
                )
                raise RuntimeError(f"{codegen_failure_reason}: {codegen_failure_message}")
            warning = {
                "reason": codegen_failure_reason,
                "message": codegen_failure_message,
                "partial_chars": len(code),
                "ready_sections": len(streaming_states),
                "rendered_sections": rendered_section_count,
            }
            summary["codegen_warnings"].append(warning)
            expected_segment_count = len(streaming_states)
            _emit_pipeline_event(
                event_callback,
                event_type=ManimStreamEventType.STAGE_PROGRESS,
                stage=ManimStreamEventStage.CODEGEN,
                message="Manim code stream completed with a salvageable Scene Pack parse warning",
                run_id=run_id,
                progress=60,
                extra=warning,
            )
        pre_rendered_segment_ids = {
            state.task.segment_id
            for state in streaming_states
            if state.status == "done"
        }
        _emit_pipeline_event(
            event_callback,
            event_type=ManimStreamEventType.CODEGEN_STREAM_COMPLETED,
            stage=ManimStreamEventStage.CODEGEN,
            message="Manim code stream completed",
            run_id=run_id,
            extra={
                "char_count": len(code),
                "ready_sections": len(pre_rendered_segment_ids),
                "validated_sections": summary["streaming_section_counts"]["validated"],
                "validation_failed_sections": summary["streaming_section_counts"]["validation_failed"],
            },
        )
        _emit_stage_completed(
            event_callback,
            stage=ManimStreamEventStage.CODEGEN,
            run_id=run_id,
            message="Manim code generation completed",
            progress=60,
        )

        _emit_stage_started(
            event_callback,
            stage=ManimStreamEventStage.RENDER,
            run_id=run_id,
            message="Round 1 section-only finalization started",
            progress=61,
        )
        _record_timing_once(stream_timing, "t_render_stage_started", pipeline_started_at)
        _write_json_debug(stream_timing_path, stream_timing)
        stage_started_at = time.time()
        r1_render = _build_section_only_render_result(
            output_dir=r1_dir,
            states=streaming_states,
            expected_segment_count=expected_segment_count,
        )
        stage_times["round1_render"] = time.time() - stage_started_at
        total_render_repairs = sum(
            int(getattr(state.render_result, "render_repair_rounds", 0) or 0)
            for state in streaming_states
            if getattr(state, "render_result", None) is not None
        )
        _emit_stage_completed(
            event_callback,
            stage=ManimStreamEventStage.RENDER,
            run_id=run_id,
            message="Round 1 section-only finalization completed",
            progress=90,
            extra={
                "render_success": bool(r1_render.success),
                "repair_rounds": total_render_repairs,
            },
        )

        summary["rounds"].append(_round_info(1, r1_render, None))
        if r1_render.success and r1_render.video_path:
            summary["final_video"] = str(r1_render.video_path)
            if has_audio_stream(r1_render.video_path):
                summary["final_video_with_audio"] = str(r1_render.video_path)
            summary["final_passed"] = True
            _log("Final delivery: Round 1 video ready")
        else:
            summary["final_passed"] = False

        _emit_stage_started(
            event_callback,
            stage=ManimStreamEventStage.DELIVERY,
            run_id=run_id,
            message="Delivery stage started",
            progress=91,
        )
        _apply_delivery_assets(
            run_dir,
            request_text,
            teaching_plan,
            storyboard,
            summary,
            event_callback=event_callback,
            run_id=run_id,
        )
        _emit_stage_completed(
            event_callback,
            stage=ManimStreamEventStage.DELIVERY,
            run_id=run_id,
            message="Delivery stage completed",
            progress=100,
            extra={"has_delivery_video": bool(summary.get("delivery_video"))},
        )
        _record_timing_once(stream_timing, "t_final_done", pipeline_started_at)
        _write_json_debug(stream_timing_path, stream_timing)
        summary["stage_times"] = _round_timing_map(stage_times)
        summary["stream_timing"] = _round_timing_map(stream_timing)
        summary["timing"] = _build_pipeline_timing_summary(
            stage_times=stage_times,
            stream_timing=stream_timing,
            streaming_section_counts=summary["streaming_section_counts"],
            streaming_preview=summary["streaming_preview"],
        )
        summary["stream_debug_files"] = {
            "analysis_stream": str(analysis_stream_path),
            "code_stream_partial": str(code_stream_path),
            "stream_timing": str(stream_timing_path),
        }

        _log(f"Done - final score: {summary['final_score']}, passed: {summary['final_passed']}")
        _save_summary(run_dir, summary)
        _emit_pipeline_event(
            event_callback,
            event_type=ManimStreamEventType.TASK_COMPLETED,
            stage=ManimStreamEventStage.DELIVERY,
            message="Manim pipeline completed",
            run_id=run_id,
            progress=100,
            extra={"final_passed": bool(summary.get("final_passed"))},
        )
        return summary
    except Exception as exc:
        _emit_pipeline_event(
            event_callback,
            event_type=ManimStreamEventType.TASK_FAILED,
            stage=ManimStreamEventStage.UNKNOWN,
            message=f"Manim pipeline failed: {exc}",
            run_id=run_id,
            extra={"error": str(exc)},
        )
        raise
    finally:
        _pipeline_io_end()


def _apply_delivery_assets(
    run_dir: Path,
    request_text: str,
    teaching_plan: Dict[str, Any],
    storyboard: Optional[Dict[str, Any]],
    summary: Dict[str, Any],
    *,
    event_callback: Callable[[ManimStreamEvent], None] | None = None,
    run_id: str = "",
) -> None:
    """When a storyboard exists, wrap the Manim video with Remotion."""
    source_video = summary.get("final_video_with_audio") or summary.get("final_video")
    if not source_video:
        return

    summary["delivery_video"] = source_video
    if not storyboard:
        return

    _emit_pipeline_event(
        event_callback,
        event_type=ManimStreamEventType.STAGE_PROGRESS,
        stage=ManimStreamEventStage.DELIVERY,
        message="Preparing hybrid delivery assets",
        run_id=run_id,
        progress=94,
    )
    _log("混合成片: 正在构建 Remotion 成片 …")
    _log("Remotion: building hybrid delivery ...")
    try:
        hybrid = build_remotion_hybrid(
            run_dir=run_dir,
            request_text=request_text,
            teaching_plan=teaching_plan,
            storyboard=storyboard,
            source_video=Path(source_video),
        )
        summary["hybrid_delivery"] = hybrid
        if hybrid.get("video_path"):
            summary["delivery_video"] = hybrid["video_path"]
            _log(f"Remotion: hybrid delivery ready at {hybrid['video_path']}")
        else:
            status = hybrid.get("status", "unknown")
            _log(f"Remotion: hybrid scaffolded (status={status})")
            if hybrid.get("render_command"):
                _log(f"  Manual render: {hybrid['render_command']}")
    except Exception as exc:
        _log(f"Remotion: hybrid delivery failed - {exc}")
        summary["hybrid_delivery"] = {"enabled": True, "status": "error", "error": str(exc)}
    finally:
        _emit_pipeline_event(
            event_callback,
            event_type=ManimStreamEventType.STAGE_PROGRESS,
            stage=ManimStreamEventStage.DELIVERY,
            message="Hybrid delivery preparation finished",
            run_id=run_id,
            progress=97,
        )


def _save_summary(run_dir: Path, summary: Dict[str, Any]) -> None:
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Summary saved: {path}")


# =====================================================================
# CLI
# =====================================================================


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent_pipeline",
        description="Single-round Manim generation pipeline with optional Remotion hybrid delivery",
    )
    parser.add_argument("request", nargs="?", default=None, help="Student request text")
    parser.add_argument("--image", type=Path, default=None, help="Optional input image")
    parser.add_argument("--run-dir", type=Path, default=None, help="Custom output directory")
    parser.add_argument(
        "--language",
        type=str,
        default=DEFAULT_OUTPUT_LANGUAGE,
        help="Output language for the video: en or zh",
    )
    parser.add_argument(
        "--render-backend",
        choices=["manim", "hybrid"],
        default="manim",
        help="Delivery backend: 'manim' for pure Manim, 'hybrid' for Remotion-wrapped",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Stream AI deltas, section renders, and preview updates to stdout for local CLI debugging.",
    )
    parser.add_argument(
        "--debug-machine-verbose",
        action="store_true",
        help=(
            "With --debug, also emit one A4L_MANIM_EVENT JSON line per analysis/code delta "
            "(includes full delta; default --debug only prints human stream without these lines). "
            "Env MANIM_DEBUG_MACHINE_VERBOSE=1 also enables."
        ),
    )
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    machine_verbose = bool(
        args.debug_machine_verbose
        or (os.environ.get("MANIM_DEBUG_MACHINE_VERBOSE") or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    debug_sink = (
        CliDebugSink(machine_verbose=machine_verbose) if args.debug else None
    )

    try:
        configs = resolve_pipeline_llm_configs()
        required = ("analysis", "code", "director") if args.render_backend == "hybrid" else ("analysis", "code")
        validate_pipeline_llm_configs(configs, required_stages=required)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1

    if args.request is None and args.image is None:
        print('Usage: python -m plugins.manim.agent_pipeline "request text" [--image img.png]')
        return 1

    request_text = args.request or "Generate an animation lesson from the image."

    t0 = time.time()
    summary = run_pipeline(
        request_text,
        args.image,
        args.run_dir,
        language=args.language,
        render_backend=args.render_backend,
        debug_callback=debug_sink,
    )
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"  Pipeline finished in {elapsed:.0f}s")
    print(f"  Backend:  {summary.get('render_backend', 'manim')}")
    print(f"  Rounds:   {len(summary['rounds'])}")
    for round_info in summary["rounds"]:
        round_number = round_info["round"]
        score = round_info.get("eval_score")
        passed = round_info.get("eval_passed")
        rendered = "YES" if round_info.get("render_success") else "NO"
        print(f"    Round {round_number}: render={rendered}, score={score}, passed={passed}")
    print(f"  Final video:  {summary['final_video']}")
    print(f"  Language:     {summary['output_language']}")
    audio_video = summary.get("final_video_with_audio")
    if audio_video:
        print(f"  With audio:   {audio_video}")
    delivery_video = summary.get("delivery_video")
    if delivery_video and delivery_video != summary.get("final_video") and delivery_video != audio_video:
        print(f"  Delivery:     {delivery_video}")
    print(f"  Final score:  {summary['final_score']}")
    print(f"  Final passed: {summary['final_passed']}")
    print(f"{'=' * 60}")

    return 0 if summary.get("final_video") else 1
