from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
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
from plugins.manim.agent_pipeline.render_backend import normalize_render_backend
from plugins.manim.agent_pipeline.renderer import build_hls_video_file
from plugins.manim.delivery import HlsPublishInput, TencentCosCdnPublisher
from plugins.manim.mcp_server import create_server
from plugins.manim.runtime_config import get_manim_runs_dir

_logger = get_mcp_logger("manim_http")

_job_semaphore: threading.BoundedSemaphore | None = None
_job_semaphore_cap: int = -1
_shared_renderer_runtime: ManimRenderRuntime | None = None
_TERMINAL_JOB_STATUSES = {"ok", "error"}
_RESTART_INTERRUPTED_ERROR_CODE = "job_interrupted_by_restart"
_RESTART_INTERRUPTED_MESSAGE = (
    "Manim render job was interrupted by backend restart. "
    "Start a new render job to regenerate the final video."
)
_CAPACITY_QUEUE_MESSAGE = (
    "Manim render capacity is full. Your video task is queued and will start automatically "
    "when a render slot is available."
)
_RENDER_RUNNING_MESSAGE = "Manim render job is running. Final video is not ready yet."
_TEXT_STREAM_EVENT_TYPES = {"analysis_delta", "code_delta", "llm_reasoning_summary_delta"}
_PREVIEW_PUBLISH_INITIAL_RETRY_SECONDS = 30.0
_PREVIEW_PUBLISH_MAX_RETRY_SECONDS = 300.0
_PUBLIC_CLOUD_DELIVERY_ERROR_MESSAGE = (
    "Manim video delivery is temporarily unavailable. Please retry after the service refreshes."
)
_CLOUD_CREDENTIAL_ERROR_CODES = frozenset(
    {
        "InvalidAccessKeyId",
        "InvalidSecretId.NotFound",
        "AuthFailure.InvalidSecretId",
        "AuthFailure.SecretIdNotFound",
        "AuthFailure.SignatureFailure",
    }
)
_CLOUD_CREDENTIAL_ERROR_SNIPPETS = (
    "InvalidAccessKeyId",
    "Access Key Id",
    "SecretId",
    "SignatureFailure",
)


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    dst = Path(path).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding=encoding,
            dir=str(dst.parent),
            prefix=f".{dst.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_file.write(text)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(dst)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


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
    "medium": "-qm --fps 60",
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
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=str(dst.parent),
            prefix=f".{dst.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
        shutil.copy2(str(src), str(tmp_path))
        tmp_path.replace(dst)
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _managed_video_metadata_path(managed_path: Path) -> Path:
    path = Path(managed_path).resolve()
    return path.with_name(f"{path.name}.json")


def _write_managed_video_metadata(
    managed_path: Path,
    *,
    conversation_id: str,
    run_key: str,
    job_id: str = "",
    preview_version: int | None = None,
    final_version: int | None = None,
    is_final: bool,
) -> None:
    metadata_path = _managed_video_metadata_path(managed_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "conversation_id": conversation_id or "",
        "run_key": run_key,
        "job_id": job_id or "",
        "delivery_type": "mp4",
        "preview_version": preview_version,
        "final_version": final_version,
        "is_final": is_final,
        "updated_at": time.time(),
    }
    _atomic_write_text(metadata_path, json.dumps(payload, ensure_ascii=False, indent=2))


def _publish_managed_artifacts(
    source_path: Path,
    managed_path: Path,
    *,
    conversation_id: str,
    run_key: str,
    job_id: str = "",
    preview_version: int | None = None,
    final_version: int | None = None,
    is_final: bool,
) -> None:
    _publish_managed_video_atomically(source_path, managed_path)
    _write_managed_video_metadata(
        managed_path,
        conversation_id=conversation_id,
        run_key=run_key,
        job_id=job_id,
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


def _coerce_preview_version(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        coerced = int(value)
        return coerced if coerced > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        coerced = int(value.strip())
        return coerced if coerced > 0 else None
    return None


def _playback_url_from_payload(payload: dict[str, Any]) -> str:
    for key in ("playback_url", "manifest_url", "video_url", "preview_url", "delivery_url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _looks_like_hls_url(value: str) -> bool:
    return value.split("?", 1)[0].split("#", 1)[0].lower().endswith(".m3u8")


def _video_version_from_payload(
    payload: dict[str, Any],
    *,
    event_type: str = "",
    fallback_is_final: bool | None = None,
) -> dict[str, Any] | None:
    version = _coerce_preview_version(payload.get("preview_version"))
    video_url = _playback_url_from_payload(payload)
    if not version or not video_url:
        return None
    raw_is_final = payload.get("is_final")
    is_final = (
        bool(raw_is_final)
        if isinstance(raw_is_final, bool)
        else fallback_is_final
        if fallback_is_final is not None
        else event_type == "preview_video_finalized"
    )
    manifest_url = payload.get("manifest_url")
    playback_url = payload.get("playback_url")
    file_name = payload.get("file_name")
    preview_file_name = payload.get("preview_file_name")
    manifest_key = payload.get("manifest_key")
    return {
        "version": version,
        "kind": "final" if is_final else "preview",
        "is_final": is_final,
        "delivery_type": str(payload.get("delivery_type") or ("hls" if _looks_like_hls_url(video_url) else "mp4")),
        "video_url": video_url,
        "playback_url": playback_url.strip() if isinstance(playback_url, str) and playback_url.strip() else video_url,
        "manifest_url": manifest_url.strip() if isinstance(manifest_url, str) and manifest_url.strip() else None,
        "manifest_key": manifest_key.strip() if isinstance(manifest_key, str) and manifest_key.strip() else None,
        "file_name": file_name.strip() if isinstance(file_name, str) and file_name.strip() else None,
        "preview_file_name": preview_file_name.strip()
        if isinstance(preview_file_name, str) and preview_file_name.strip()
        else None,
    }


def _collect_video_versions_from_events(
    events: list[dict[str, Any]],
    result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    by_key: dict[tuple[int, bool], dict[str, Any]] = {}

    def add(payload: dict[str, Any] | None, *, event_type: str = "", fallback_is_final: bool | None = None) -> None:
        if not isinstance(payload, dict):
            return
        version = _video_version_from_payload(payload, event_type=event_type, fallback_is_final=fallback_is_final)
        if not version:
            return
        key = (int(version["version"]), bool(version["is_final"]))
        by_key[key] = version

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        if event_type not in {"preview_video_updated", "preview_video_finalized", "job_status"}:
            continue
        extra = event.get("extra")
        result_payload = event.get("result")
        add(extra if isinstance(extra, dict) else None, event_type=event_type)
        add(
            result_payload if isinstance(result_payload, dict) else None,
            event_type=event_type,
            fallback_is_final=event_type == "job_status" and event.get("status") == "ok",
        )
    add(
        result,
        fallback_is_final=bool(
            isinstance(result, dict)
            and str(result.get("status") or "").strip().lower() == "ok"
        ),
    )

    return [
        by_key[key]
        for key in sorted(
            by_key,
            key=lambda item: (item[0], 1 if item[1] else 0),
        )
    ]


def _coerce_progress_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, min(100, value))
    if isinstance(value, float) and value == value:
        return max(0, min(100, round(value)))
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            return max(0, min(100, int(normalized)))
    return None


def _project_job_progress(
    *,
    status: str,
    events: list[dict[str, Any]],
    result: dict[str, Any] | None,
) -> dict[str, Any]:
    progress: int | None = 0 if status == "queued" else None
    current_stage: str | None = None
    stage_message: str | None = None
    stage_progress: int | None = None

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip()
        event_progress = _coerce_progress_value(event.get("progress"))
        stage = event.get("stage")
        if isinstance(stage, str) and stage.strip():
            current_stage = stage.strip()
            if event_type not in _TEXT_STREAM_EVENT_TYPES or stage_progress is None:
                stage_progress = event_progress
        message = event.get("message")
        if isinstance(message, str) and message.strip():
            stage_message = message.strip()
            if event_progress is not None:
                stage_progress = event_progress
        if event_progress is not None:
            progress = max(progress or 0, event_progress)
        extra = event.get("extra")
        if isinstance(extra, dict):
            extra_progress = _coerce_progress_value(extra.get("progress"))
            if extra_progress is not None:
                progress = max(progress or 0, extra_progress)

    if isinstance(result, dict):
        result_progress = _coerce_progress_value(result.get("progress"))
        if result_progress is not None:
            progress = max(progress or 0, result_progress)

    if status == "ok":
        progress = 100
        current_stage = current_stage or ManimStreamEventStage.DELIVERY.value
    elif status == "error":
        current_stage = current_stage or ManimStreamEventStage.UNKNOWN.value
    elif status == "running" and progress is None:
        progress = 1

    return {
        "stage": current_stage,
        "current_stage": current_stage,
        "stage_message": stage_message,
        "progress": progress,
    }


def _safe_run_path_component(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _string_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _prettify_storyboard_id(value: str) -> str:
    normalized = re.sub(r"Scene$", "", value)
    normalized = re.sub(r"^Segment\d*", "", normalized)
    normalized = re.sub(r"^section[_\s-]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[_-]+", " ", normalized)
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized.title() if normalized else value


def _coerce_storyboard_order(value: Any, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return fallback


def _storyboard_status_from_stage(stage: str, payload: dict[str, Any] | None) -> tuple[str, int]:
    lowered = stage.lower()
    data = payload if isinstance(payload, dict) else {}
    if data.get("success") is False or "failed" in lowered or "error" in lowered:
        return "error", 100
    if data.get("success") is True or "completed" in lowered or "rendered" in lowered:
        return "done", 100
    if "render_started" in lowered or "rendering" in lowered:
        return "running", 75
    if "tts" in lowered:
        return "running", 55
    if "validation" in lowered or "validated" in lowered:
        return "running", 45
    return "running", 30


def _merge_storyboard_item(
    items: dict[str, dict[str, Any]],
    key: str,
    update: dict[str, Any],
) -> None:
    if not key:
        return
    existing = items.get(key)
    if existing is None:
        items[key] = update
        return

    rank = {"pending": 0, "running": 1, "done": 2, "error": 3}
    existing_status = str(existing.get("status") or "pending")
    incoming_status = str(update.get("status") or existing_status)
    existing_progress = _coerce_progress_value(existing.get("progress")) or 0
    incoming_progress = _coerce_progress_value(update.get("progress")) or 0
    next_status = (
        "error"
        if "error" in {existing_status, incoming_status}
        else incoming_status
        if rank.get(incoming_status, -1) >= rank.get(existing_status, -1)
        else existing_status
    )
    existing.update({k: v for k, v in update.items() if v is not None})
    existing["status"] = next_status
    existing["progress"] = max(existing_progress, incoming_progress)


def _build_manim_storyboard_items(
    *,
    conversation_id: str,
    run_key: str,
    job_status: str,
) -> list[dict[str, Any]]:
    if not conversation_id or not run_key:
        return []

    run_dir_name = f"{_safe_run_path_component(conversation_id)}_{_safe_run_path_component(run_key)}"
    candidate_roots = [get_managed_runs_dir()]
    configured_runs_dir = str(settings.manim.runs_output_dir or "").strip()
    if configured_runs_dir:
        candidate_roots.append(Path(configured_runs_dir).resolve())
    else:
        candidate_roots.append(get_manim_runs_dir().resolve())

    run_dir = next(
        (root / run_dir_name for root in candidate_roots if (root / run_dir_name).exists()),
        candidate_roots[0] / run_dir_name,
    )
    if not run_dir.exists():
        return []

    items_by_order: dict[int, dict[str, Any]] = {}
    fallback_items: dict[str, dict[str, Any]] = {}
    plan = _read_json_object(run_dir / "teaching_plan.json") or {}
    sections = plan.get("sections")
    if isinstance(sections, list):
        for index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                continue
            section_id = _string_value(section.get("id")) or f"section_{index}"
            title = _string_value(section.get("title")) or _prettify_storyboard_id(section_id)
            items_by_order[index] = {
                "id": section_id,
                "title": title,
                "order": index,
                "status": "pending",
                "progress": 0,
                "stage": "planned",
                "segment_id": section_id,
            }

    def apply_segment_file(path: Path, *, ready: bool = False, failure: bool = False) -> None:
        data = _read_json_object(path)
        if not data:
            return
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else None
        order_source = data.get("order")
        if order_source is None:
            order_source = data.get("segment_order")
        if order_source is None and payload:
            order_source = payload.get("segment_order")
        order = _coerce_storyboard_order(order_source, len(items_by_order) + len(fallback_items) + 1)
        if order == 0 and items_by_order:
            return
        segment_id = _string_value(data.get("segment_id")) or _string_value(payload.get("segment_id") if payload else None)
        scene_name = _string_value(data.get("scene_name")) or _string_value(payload.get("scene_name") if payload else None)
        title = _prettify_storyboard_id(segment_id or scene_name or f"section_{order}")
        status = "running"
        progress = 25
        stage = _string_value(data.get("stage")) or ("section_ready" if ready else "section_status")
        if failure:
            error = _string_value(data.get("error"))
            if "mark_submitted" in error:
                status, progress = "running", 30
            else:
                status, progress = "error", 100
        elif stage:
            status, progress = _storyboard_status_from_stage(stage, payload)
        update = {
            "id": segment_id or scene_name or f"section_{order}",
            "title": title,
            "order": order,
            "status": status,
            "progress": progress,
            "stage": stage,
            "message": _string_value(data.get("error")) or None,
            "scene_name": scene_name or None,
            "segment_id": segment_id or None,
        }
        if order in items_by_order:
            item = items_by_order[order]
            planned_id = item.get("id")
            planned_title = item.get("title")
            planned_order = item.get("order")
            planned_segment_id = item.get("segment_id")
            _merge_storyboard_item({str(order): item}, str(order), update)
            if planned_id:
                item["id"] = planned_id
            if planned_title:
                item["title"] = planned_title
            if planned_order is not None:
                item["order"] = planned_order
            item["scene_name"] = update["scene_name"] or item.get("scene_name")
            if update["segment_id"] and update["segment_id"] != planned_segment_id:
                item["runtime_segment_id"] = update["segment_id"]
            item["segment_id"] = planned_segment_id or item.get("segment_id") or update["segment_id"]
            return
        _merge_storyboard_item(fallback_items, str(order), update)

    for path in sorted(run_dir.glob("section_ready_*.json")):
        apply_segment_file(path, ready=True)
    for path in sorted(run_dir.glob("section_status_*.json")):
        apply_segment_file(path)
    for path in sorted(run_dir.glob("section_submit_failure_*.json")):
        apply_segment_file(path, failure=True)

    if items_by_order:
        items = list(items_by_order.values())
    elif str(job_status or "").lower() in {"queued", "running"}:
        items = []
    else:
        items = list(fallback_items.values())
    if job_status == "ok":
        for item in items:
            if item.get("status") != "error":
                item["status"] = "done"
                item["progress"] = 100
    return sorted(items, key=lambda item: (int(item.get("order") or 0), str(item.get("title") or "")))


def _manim_delivery_mode() -> str:
    return str(settings.manim.delivery.mode or "local_mp4").strip().lower() or "local_mp4"


def _is_hls_cos_delivery_enabled() -> bool:
    return _manim_delivery_mode() == "hls_cos_cdn"


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


def _latest_incremental_hls_manifest_path(run_dir: Path) -> tuple[Path | None, int]:
    manifest_dir = Path(run_dir) / "round1" / "hls" / "manifests"
    if not manifest_dir.exists():
        return None, 0
    candidates = sorted(
        (item for item in manifest_dir.glob("preview_v*.m3u8") if item.is_file()),
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


def get_managed_jobs_dir() -> Path:
    return _managed_data_root() / "jobs"


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


def _normalized_manim_job_timing(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, bool) or item is None:
            normalized[key] = item
            continue
        if isinstance(item, (int, float)):
            normalized[key] = round(float(item), 3)
            continue
        if isinstance(item, str):
            normalized[key] = item
            continue
        if isinstance(item, dict):
            nested = _normalized_manim_job_timing(item)
            if nested:
                normalized[key] = nested
            continue
        if isinstance(item, list):
            normalized_list: list[Any] = []
            for entry in item:
                if isinstance(entry, dict):
                    nested = _normalized_manim_job_timing(entry)
                    if nested:
                        normalized_list.append(nested)
                elif isinstance(entry, bool) or entry is None:
                    normalized_list.append(entry)
                elif isinstance(entry, (int, float)):
                    normalized_list.append(round(float(entry), 3))
                elif isinstance(entry, str):
                    normalized_list.append(entry)
            if normalized_list:
                normalized[key] = normalized_list
    return normalized or None


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


def _cloud_credential_error_code(message: object) -> str:
    if isinstance(message, dict):
        return str(message.get("code") or message.get("error_code") or "").strip()
    for attr in ("code", "error_code"):
        code = getattr(message, attr, "")
        if code:
            return str(code).strip()
    text = str(message or "")
    for code in _CLOUD_CREDENTIAL_ERROR_CODES:
        if code in text:
            return code
    return ""


def _is_cloud_credential_error(message: object) -> bool:
    code = _cloud_credential_error_code(message)
    if code in _CLOUD_CREDENTIAL_ERROR_CODES:
        return True
    text = str(message or "")
    return any(snippet in text for snippet in _CLOUD_CREDENTIAL_ERROR_SNIPPETS)


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
    if _is_cloud_credential_error(message):
        return _PUBLIC_CLOUD_DELIVERY_ERROR_MESSAGE
    return _redact_public_text(str(message or "").strip() or "Manim render failed.")


def _is_final_video_version(version: dict[str, Any]) -> bool:
    return bool(version.get("is_final")) or str(version.get("kind") or "").strip().lower() == "final"


@dataclass(slots=True)
class RenderJobState:
    job_id: str
    status: str
    message: str
    conversation_id: str = ""
    idempotency_key: str = ""
    request: str = ""
    language: str = ""
    render_backend: str = "manim"
    quality: str = "default"
    flash: bool = True
    source_job_id: str = ""
    run_key: str = ""
    preview_file_name: str = ""
    delivery_type: str = "mp4"
    manifest_url: str = ""
    preview_version: int | None = None
    preview_ready: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RenderJobState:
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")

        events = payload.get("events")
        if not isinstance(events, list):
            events = []

        result = payload.get("result")
        if result is not None and not isinstance(result, dict):
            result = None

        preview_version = payload.get("preview_version")
        if preview_version is not None:
            try:
                preview_version = int(preview_version)
            except (TypeError, ValueError):
                preview_version = None

        return cls(
            job_id=job_id,
            status=str(payload.get("status") or "error"),
            message=str(payload.get("message") or ""),
            conversation_id=str(payload.get("conversation_id") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
            request=str(payload.get("request") or ""),
            language=str(payload.get("language") or ""),
            render_backend=str(payload.get("render_backend") or "manim"),
            quality=str(payload.get("quality") or "default"),
            flash=payload.get("flash") is not False,
            source_job_id=str(payload.get("source_job_id") or ""),
            run_key=str(payload.get("run_key") or ""),
            preview_file_name=str(payload.get("preview_file_name") or ""),
            delivery_type=str(payload.get("delivery_type") or "mp4"),
            manifest_url=str(payload.get("manifest_url") or ""),
            preview_version=preview_version,
            preview_ready=bool(payload.get("preview_ready")),
            result=result,
            error=str(payload.get("error") or "") or None,
            error_code=str(payload.get("error_code") or "") or None,
            events=[dict(event) for event in events if isinstance(event, dict)],
            created_at=float(payload.get("created_at") or time.time()),
            updated_at=float(payload.get("updated_at") or time.time()),
        )

    def to_payload(self) -> dict[str, Any]:
        versions = _collect_video_versions_from_events(self.events, self.result)
        final_version = next((version for version in reversed(versions) if version.get("is_final")), None)
        terminal_error_without_final = self.status == "error" and final_version is None
        public_versions = versions
        if terminal_error_without_final:
            public_versions = [version for version in versions if _is_final_video_version(version)]
        playable_version = final_version or (public_versions[-1] if public_versions else None)
        public_manifest_url = self.manifest_url or None
        public_preview_ready = self.preview_ready
        if terminal_error_without_final:
            public_manifest_url = None
            public_preview_ready = False
        progress_projection = _project_job_progress(
            status=self.status,
            events=self.events,
            result=self.result,
        )
        storyboard_items = _build_manim_storyboard_items(
            conversation_id=self.conversation_id,
            run_key=self.run_key,
            job_status=self.status,
        )
        payload: dict[str, Any] = {
            "status": self.status,
            "job_id": self.job_id,
            "message": self.message,
            "conversation_id": self.conversation_id or None,
            "idempotency_key": self.idempotency_key or None,
            "request": self.request or None,
            "language": self.language or None,
            "render_backend": self.render_backend,
            "quality": self.quality,
            "flash": self.flash,
            "source_job_id": self.source_job_id or None,
            "run_key": self.run_key or None,
            "preview_file_name": self.preview_file_name or None,
            "delivery_type": self.delivery_type,
            "manifest_url": public_manifest_url,
            "preview_version": self.preview_version,
            "preview_ready": public_preview_ready,
            "stage": progress_projection["stage"],
            "current_stage": progress_projection["current_stage"],
            "stage_message": progress_projection["stage_message"],
            "progress": progress_projection["progress"],
            "can_retry": self.status == "error" and bool(self.request.strip()),
            "storyboard_items": storyboard_items,
            "video_versions": public_versions,
            "video_url": final_version.get("video_url") if final_version else None,
            "playback_url": playable_version.get("playback_url") if playable_version else None,
            "preview_url": (
                public_manifest_url
                if self.delivery_type == "hls" and public_manifest_url and public_preview_ready
                else
                _versioned_video_url(self.preview_file_name, self.preview_version)
                if self.preview_file_name and public_preview_ready
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
            result_payload = dict(self.result)
            if terminal_error_without_final:
                for key in (
                    "video_url",
                    "preview_url",
                    "playback_url",
                    "manifest_url",
                    "manifest_key",
                    "delivery_url",
                    "preview_ready",
                    "preview_file_name",
                    "file_name",
                    "delivery_file_name",
                ):
                    result_payload.pop(key, None)
            if public_versions and "video_versions" not in result_payload:
                result_payload["video_versions"] = public_versions
            payload["result"] = _sanitize_public_payload(result_payload)
            timing_payload = _normalized_manim_job_timing(self.result.get("timing"))
            if timing_payload is not None:
                payload["timing"] = timing_payload
            quality_flags = str(self.result.get("quality_flags") or "").strip()
            if quality_flags:
                payload["quality_flags"] = quality_flags
        return payload

    def to_snapshot(self) -> dict[str, Any]:
        payload = self.to_payload()
        payload["events"] = [_sanitize_public_payload(dict(event)) for event in self.events]
        return payload

    def mark_interrupted_after_restart(self) -> None:
        self.status = "error"
        self.message = _RESTART_INTERRUPTED_MESSAGE
        self.error = _RESTART_INTERRUPTED_MESSAGE
        self.error_code = _RESTART_INTERRUPTED_ERROR_CODE
        self.updated_at = time.time()
        self.events.append(
            {
                "type": "job_status",
                "status": self.status,
                "message": self.message,
                "error": self.error,
                "error_code": self.error_code,
                "job_id": self.job_id,
                "ts": self.updated_at,
            }
        )


class ManimRenderRuntime:
    def __init__(self) -> None:
        configured_runs_dir = str(settings.manim.runs_output_dir or "").strip()
        self._runs_dir = Path(configured_runs_dir).resolve() if configured_runs_dir else get_manim_runs_dir().resolve()
        self._videos_dir = get_managed_videos_dir()
        self._jobs_dir = get_managed_jobs_dir()
        self._jobs: dict[str, RenderJobState] = {}
        self._jobs_lock = threading.Lock()
        self._hls_publisher: TencentCosCdnPublisher | None = None
        self._load_job_snapshots()

    def _get_hls_publisher(self) -> TencentCosCdnPublisher:
        if self._hls_publisher is None:
            self._hls_publisher = TencentCosCdnPublisher(
                delivery=settings.manim.delivery,
                cloud=settings.cloud,
            )
        return self._hls_publisher

    def _reset_hls_publisher(self) -> None:
        self._hls_publisher = None

    def _publish_hls_with_credential_refresh(self, payload: HlsPublishInput) -> Any:
        try:
            return self._get_hls_publisher().publish_hls(payload)
        except Exception as exc:
            if not _is_cloud_credential_error(exc):
                raise
            _logger.warning(
                "[Manim] cloud credential error during HLS publish; refreshing settings and retrying once: code=%s",
                _cloud_credential_error_code(exc) or type(exc).__name__,
            )
            try:
                settings.reload()
            except Exception:
                _logger.exception("[Manim] failed to reload settings after cloud credential error")
            self._reset_hls_publisher()
            return self._get_hls_publisher().publish_hls(payload)

    def _job_snapshot_path(self, job_id: str) -> Path:
        normalized = re.sub(r"[^A-Za-z0-9._-]", "_", str(job_id or "").strip())
        if not normalized:
            raise ValueError("job_id is required")
        return self._jobs_dir / f"{normalized}.json"

    def _persist_job_state_locked(self, state: RenderJobState) -> None:
        try:
            self._jobs_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = self._job_snapshot_path(state.job_id)
            _atomic_write_text(snapshot_path, json.dumps(state.to_snapshot(), ensure_ascii=False, indent=2))
        except Exception:
            _logger.exception("[Manim] failed to persist job snapshot: job_id=%s", state.job_id)

    def _load_job_snapshots(self) -> None:
        if not self._jobs_dir.exists():
            self._load_job_snapshots_from_video_metadata()
            return
        loaded = 0
        interrupted = 0
        for snapshot_path in sorted(self._jobs_dir.glob("*.json")):
            try:
                payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    continue
                state = RenderJobState.from_payload(payload)
                if state.status not in _TERMINAL_JOB_STATUSES:
                    state.mark_interrupted_after_restart()
                    interrupted += 1
                self._jobs[state.job_id] = state
                self._persist_job_state_locked(state)
                loaded += 1
            except Exception:
                _logger.warning("[Manim] skipped invalid job snapshot: %s", snapshot_path, exc_info=True)
        if loaded:
            _logger.info("[Manim] restored %s job snapshots from disk, interrupted=%s", loaded, interrupted)
        self._load_job_snapshots_from_video_metadata()

    def _load_job_snapshots_from_video_metadata(self) -> None:
        if not self._videos_dir.exists():
            return
        recovered = 0
        for metadata_path in sorted(self._videos_dir.glob("*.mp4.json")):
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            job_id = str(payload.get("job_id") or "").strip()
            if not job_id or job_id in self._jobs:
                continue
            file_name = metadata_path.name.removesuffix(".json")
            video_path = self._videos_dir / file_name
            if not video_path.exists() or not video_path.is_file():
                continue
            preview_version = payload.get("final_version") or payload.get("preview_version")
            try:
                preview_version = int(preview_version) if preview_version is not None else None
            except (TypeError, ValueError):
                preview_version = None
            conversation_id = str(payload.get("conversation_id") or "").strip()
            run_key = str(payload.get("run_key") or "").strip()
            video_url = _versioned_video_url(file_name, preview_version)
            state = RenderJobState(
                job_id=job_id,
                status="ok",
                message="Manim render job completed.",
                conversation_id=conversation_id,
                run_key=run_key,
                preview_file_name=file_name,
                preview_version=preview_version,
                preview_ready=True,
                result={
                    "status": "ok",
                    "preview_type": "manim_video",
                    "message": "Teaching video generated successfully.",
                    "delivery_type": "mp4",
                    "video_url": video_url,
                    "file_name": file_name,
                    "preview_url": video_url,
                    "playback_url": video_url,
                    "preview_file_name": file_name,
                    "delivery_url": video_url,
                    "delivery_file_name": file_name,
                    "preview_version": preview_version,
                    "preview_ready": True,
                    "conversation_id": conversation_id or None,
                },
            )
            state.events.append(
                {
                    "type": "job_status",
                    "status": "ok",
                    "message": state.message,
                    "result": state.result,
                    "job_id": job_id,
                    "ts": state.updated_at,
                }
            )
            self._jobs[job_id] = state
            self._persist_job_state_locked(state)
            recovered += 1
        if recovered:
            _logger.info("[Manim] recovered %s completed jobs from video metadata", recovered)

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
            if isinstance(extra, dict):
                if isinstance(extra.get("delivery_type"), str):
                    state.delivery_type = str(extra["delivery_type"])
                manifest_url = extra.get("manifest_url") or extra.get("playback_url")
                if isinstance(manifest_url, str) and manifest_url.strip():
                    state.manifest_url = manifest_url.strip()
            result = event.get("result")
            if isinstance(result, dict) and isinstance(result.get("preview_version"), int):
                state.preview_version = int(result["preview_version"])
                if state.preview_version > 0:
                    state.preview_ready = True
            if isinstance(result, dict):
                if isinstance(result.get("delivery_type"), str):
                    state.delivery_type = str(result["delivery_type"])
                manifest_url = result.get("manifest_url") or result.get("playback_url")
                if isinstance(manifest_url, str) and manifest_url.strip():
                    state.manifest_url = manifest_url.strip()
            state.events.append(event)
            state.updated_at = time.time()
            self._persist_job_state_locked(state)

    def _acquire_job_slot(self, *, blocking: bool = True) -> bool:
        """Acquire a concurrent render slot, waiting by default instead of rejecting capacity overflow."""
        sem = _job_slot_semaphore()
        if not blocking:
            return sem.acquire(blocking=False)
        sem.acquire()
        return True

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
                sanitized_result = _sanitize_public_payload(result)
                state.result = sanitized_result
                if isinstance(sanitized_result, dict):
                    if isinstance(sanitized_result.get("preview_version"), int):
                        state.preview_version = int(sanitized_result["preview_version"])
                        if state.preview_version > 0:
                            state.preview_ready = True
                    if isinstance(sanitized_result.get("preview_ready"), bool):
                        state.preview_ready = bool(sanitized_result["preview_ready"])
                    if isinstance(sanitized_result.get("delivery_type"), str):
                        state.delivery_type = str(sanitized_result["delivery_type"])
                    manifest_url = sanitized_result.get("manifest_url") or sanitized_result.get("playback_url")
                    if isinstance(manifest_url, str) and manifest_url.strip():
                        state.manifest_url = manifest_url.strip()
            if error is not None:
                state.error = _sanitize_public_error(error)
            if error_code is not None:
                state.error_code = error_code
            state.updated_at = time.time()
            self._persist_job_state_locked(state)
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
        idempotency_key: str,
        render_backend: str,
        quality: str,
        request: str = "",
        language: str = "",
        flash: bool = True,
        source_job_id: str = "",
    ) -> RenderJobState:
        run_key = _new_run_key()
        preview_file_name = _managed_video_name_for_run(run_key)
        state = RenderJobState(
            job_id=f"manim_job_{uuid.uuid4().hex[:12]}",
            status="queued",
            message="Manim render job queued. Video generation is still in progress.",
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            request=str(request or "").strip(),
            language=str(language or "").strip(),
            render_backend=render_backend,
            quality=quality,
            flash=bool(flash),
            source_job_id=str(source_job_id or "").strip(),
            run_key=run_key,
            preview_file_name=preview_file_name,
            delivery_type="hls" if _is_hls_cos_delivery_enabled() else "mp4",
        )
        with self._jobs_lock:
            self._jobs[state.job_id] = state
            self._persist_job_state_locked(state)
        self._append_job_event(
            state.job_id,
            {
                "type": "job_status",
                "status": "queued",
                "message": state.message,
                "preview_file_name": state.preview_file_name,
                "delivery_type": state.delivery_type,
                "preview_ready": False,
                "preview_url": None,
            },
        )
        return state

    def _find_active_job_by_idempotency_key(self, idempotency_key: str) -> RenderJobState | None:
        normalized = str(idempotency_key or "").strip()
        if not normalized:
            return None
        with self._jobs_lock:
            for state in self._jobs.values():
                if state.idempotency_key != normalized:
                    continue
                if state.status in _TERMINAL_JOB_STATUSES:
                    continue
                return state
        return None

    @staticmethod
    def _build_submit_response_from_state(state: RenderJobState) -> dict[str, Any]:
        payload = state.to_payload()
        payload["preview_type"] = None
        payload["run_key"] = state.run_key
        payload["preview_file_name"] = state.preview_file_name
        payload["idempotency_key"] = state.idempotency_key or None
        if state.status in {"queued", "running"}:
            payload["message"] = "Manim render job accepted. Video generation is still in progress."
        return payload

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
        job_id: str = "",
        flash: bool = True,
        event_callback: Any = None,
    ) -> dict[str, Any]:
        request_text = str(request or "").strip()
        if not request_text:
            raise ValueError("request is required")

        backend = normalize_render_backend(render_backend)

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
        preview_publish_failures: dict[int, int] = {}
        preview_publish_retry_after: dict[int, float] = {}

        def _hls_event_extra(published: Any) -> dict[str, Any]:
            return {
                "delivery_type": "hls",
                "manifest_url": published.manifest_url,
                "playback_url": published.manifest_url,
                "preview_url": published.manifest_url,
                "video_url": published.manifest_url,
                "manifest_key": published.manifest_key,
                "manifest_file_name": Path(str(published.manifest_key)).name,
                "preview_version": published.preview_version,
                "preview_ready": True,
                "preview_sections": published.preview_sections,
                "is_final": published.is_final,
            }

        def _publish_hls_manifest(
            *,
            manifest_path: Path,
            hls_root: Path,
            preview_version: int,
            preview_sections: int,
            is_final: bool,
        ) -> Any:
            return self._publish_hls_with_credential_refresh(
                HlsPublishInput(
                    hls_root=hls_root,
                    manifest_path=manifest_path,
                    preview_version=preview_version,
                    preview_sections=preview_sections,
                    is_final=is_final,
                    run_key=resolved_run_key,
                )
            )

        def _maybe_publish_latest_preview() -> int:
            nonlocal latest_preview_version
            if _is_hls_cos_delivery_enabled():
                preview_path, preview_version = _latest_incremental_hls_manifest_path(run_dir)
            else:
                preview_path, preview_version = _latest_incremental_preview_path(run_dir)
            if preview_path is None or preview_version <= latest_preview_version:
                return latest_preview_version
            now = time.monotonic()
            retry_after = preview_publish_retry_after.get(preview_version, 0.0)
            if retry_after > now:
                return latest_preview_version
            try:
                if _is_hls_cos_delivery_enabled():
                    published = _publish_hls_manifest(
                        manifest_path=preview_path,
                        hls_root=Path(run_dir) / "round1" / "hls",
                        preview_version=preview_version,
                        preview_sections=preview_version,
                        is_final=False,
                    )
                    extra = _hls_event_extra(published)
                else:
                    _publish_managed_artifacts(
                        preview_path,
                        managed_path,
                        conversation_id=conversation_id,
                        run_key=resolved_run_key,
                        job_id=job_id,
                        preview_version=preview_version,
                        final_version=None,
                        is_final=False,
                    )
                    extra = {
                        "delivery_type": "mp4",
                        "preview_url": _versioned_video_url(resolved_managed_name, preview_version),
                        "preview_file_name": resolved_managed_name,
                        "preview_version": preview_version,
                        "preview_ready": True,
                        "preview_sections": preview_version,
                        "is_final": False,
                    }
            except Exception:
                failure_count = preview_publish_failures.get(preview_version, 0) + 1
                preview_publish_failures[preview_version] = failure_count
                delay = min(
                    _PREVIEW_PUBLISH_MAX_RETRY_SECONDS,
                    _PREVIEW_PUBLISH_INITIAL_RETRY_SECONDS * (2 ** min(failure_count - 1, 4)),
                )
                preview_publish_retry_after[preview_version] = now + delay
                _logger.exception(
                    "[Manim] failed to publish latest preview; retry delayed: "
                    "run=%s job_id=%s preview_version=%s retry_seconds=%.1f failure_count=%s",
                    run_dir.name,
                    job_id or "",
                    preview_version,
                    delay,
                    failure_count,
                )
                return latest_preview_version
            if event_callback is not None:
                event_callback(
                    make_manim_stream_event(
                        event_type=ManimStreamEventType.PREVIEW_VIDEO_UPDATED,
                        stage=ManimStreamEventStage.DELIVERY,
                        message=f"Preview video updated: v{preview_version}",
                        run_id=run_id_hint,
                        extra=extra,
                    )
                )
            latest_preview_version = preview_version
            return latest_preview_version

        _logger.info(
            "[Manim] 开始生成教学视频: backend=%s quality=%s run=%s",
            backend,
            quality_normalized,
            run_dir.name,
        )

        queue_start = time.monotonic()
        slot_acquired = self._acquire_job_slot(blocking=False)
        if not slot_acquired:
            _logger.info("[Manim] 并发已满，任务排队等待槽位: run=%s", run_dir.name)
            if event_callback is not None:
                event_callback(
                    {
                        "type": "job_status",
                        "status": "queued",
                        "message": _CAPACITY_QUEUE_MESSAGE,
                        "progress": 0,
                        "run_id": run_id_hint,
                    }
                )
            self._acquire_job_slot()
            slot_acquired = True
        if event_callback is not None:
            event_callback(
                {
                    "type": "job_status",
                    "status": "running",
                    "message": _RENDER_RUNNING_MESSAGE,
                    "progress": 1,
                    "run_id": run_id_hint,
                }
            )
        queue_ms = (time.monotonic() - queue_start) * 1000.0
        if queue_ms > 1.0:
            _logger.info("[Manim] 排队获得槽位: run=%s queue_ms=%.1f", run_dir.name, queue_ms)

        try:
            def _runtime_event_callback(event: Any) -> None:
                nonlocal run_id_hint
                if hasattr(event, "run_id"):
                    run_id_hint = str(event.run_id or run_id_hint)
                elif isinstance(event, dict) and event.get("run_id"):
                    run_id_hint = str(event.get("run_id") or run_id_hint)
                if event_callback is not None:
                    event_callback(event)
                try:
                    _maybe_publish_latest_preview()
                except Exception:
                    _logger.exception(
                        "[Manim] failed to publish latest preview: run=%s job_id=%s",
                        run_dir.name,
                        job_id or "",
                    )
                    return

            summary = run_pipeline(
                request_text=request_text,
                run_dir=run_dir,
                language=language or None,
                render_backend=backend,
                quality_flags=quality_flags,
                flash=bool(flash),
                event_callback=_runtime_event_callback,
            )
        finally:
            if slot_acquired:
                _job_slot_semaphore().release()

        _maybe_publish_latest_preview()

        source_video = summary.get("delivery_video") or summary.get("final_video_with_audio") or summary.get("final_video")
        if not source_video:
            raise RuntimeError("Pipeline finished without producing a final video.")

        source_path = Path(str(source_video)).resolve()
        if not source_path.exists():
            raise RuntimeError(f"Final video is missing: {source_path}")

        final_preview_version = (latest_preview_version or 0) + 1
        delivery_type = "mp4"
        final_manifest_key = ""
        final_video_url: str
        if _is_hls_cos_delivery_enabled():
            final_manifest_path, hls_error = build_hls_video_file(
                source_path,
                run_dir / "final_hls",
                manifest_name=f"final_v{final_preview_version:02d}.m3u8",
                target_segment_seconds=settings.manim.delivery.hls_segment_seconds,
            )
            if hls_error or final_manifest_path is None:
                raise RuntimeError(hls_error or "Failed to package final HLS output.")
            published_final = _publish_hls_manifest(
                manifest_path=final_manifest_path,
                hls_root=run_dir / "final_hls",
                preview_version=final_preview_version,
                preview_sections=latest_preview_version or final_preview_version,
                is_final=True,
            )
            delivery_type = "hls"
            final_manifest_key = str(published_final.manifest_key)
            final_video_url = published_final.manifest_url
            final_event_extra = _hls_event_extra(published_final)
        else:
            _publish_managed_artifacts(
                source_path,
                managed_path,
                conversation_id=conversation_id,
                run_key=resolved_run_key,
                job_id=job_id,
                preview_version=latest_preview_version or None,
                final_version=final_preview_version,
                is_final=True,
            )
            final_video_url = _versioned_video_url(resolved_managed_name, final_preview_version)
            final_event_extra = {
                "delivery_type": "mp4",
                "preview_url": final_video_url,
                "preview_file_name": resolved_managed_name,
                "video_url": final_video_url,
                "file_name": resolved_managed_name,
                "preview_version": final_preview_version,
                "preview_ready": True,
                "is_final": True,
            }
        if event_callback is not None:
            event_callback(
                make_manim_stream_event(
                    event_type=ManimStreamEventType.PREVIEW_VIDEO_FINALIZED,
                    stage=ManimStreamEventStage.DELIVERY,
                    message="Preview video finalized",
                    run_id=run_id_hint,
                    progress=100,
                    extra=final_event_extra,
                )
            )

        duration_seconds = _probe_duration_seconds(source_path)
        _logger.info(
            "[Manim] 生成完成: delivery=%s target=%s duration=%s",
            delivery_type,
            final_manifest_key or resolved_managed_name,
            duration_seconds,
        )
        return {
            "status": "ok",
            "preview_type": "manim_video",
            "message": "Teaching video generated successfully.",
            "delivery_type": delivery_type,
            "video_url": final_video_url,
            "file_name": resolved_managed_name if delivery_type == "mp4" else None,
            "preview_url": final_video_url,
            "playback_url": final_video_url,
            "manifest_url": final_video_url if delivery_type == "hls" else None,
            "manifest_key": final_manifest_key or None,
            "preview_file_name": resolved_managed_name if delivery_type == "mp4" else None,
            "delivery_url": final_video_url,
            "delivery_file_name": resolved_managed_name if delivery_type == "mp4" else None,
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
        flash: bool,
    ) -> None:
        try:
            def _job_event_callback(event: Any) -> None:
                payload = event.model_dump() if hasattr(event, "model_dump") else dict(event)
                if payload.get("type") == "job_status":
                    status_value = str(payload.get("status") or "").strip()
                    message = str(payload.get("message") or "").strip()
                    if status_value in {"queued", "running"} and message:
                        self._set_job_state(job_id, status=status_value, message=message)
                self._append_job_event(job_id, payload)

            result = self._execute_render_video(
                request=request,
                language=language,
                render_backend=render_backend,
                quality=quality,
                conversation_id=conversation_id,
                run_key=run_key,
                managed_name=managed_name,
                job_id=job_id,
                flash=flash,
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
        idempotency_key: str = "",
        source_job_id: str = "",
        flash: bool = True,
    ) -> dict[str, Any]:
        request_text = str(request or "").strip()
        if not request_text:
            raise ValueError("request is required")

        backend = normalize_render_backend(render_backend)

        quality_normalized = (quality or "default").strip().lower() or "default"
        _normalize_quality_flags(quality_normalized)
        normalized_idempotency_key = str(idempotency_key or "").strip()

        existing_state = self._find_active_job_by_idempotency_key(normalized_idempotency_key)
        if existing_state is not None:
            return self._build_submit_response_from_state(existing_state)

        state = self._create_job(
            conversation_id=str(conversation_id or "").strip(),
            idempotency_key=normalized_idempotency_key,
            render_backend=backend,
            quality=quality_normalized,
            request=request_text,
            language=language,
            flash=flash,
            source_job_id=source_job_id,
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
                "flash": state.flash,
            },
            daemon=True,
        )
        worker.start()

        return self._build_submit_response_from_state(state)

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
        idempotency_key: str = "",
        flash: bool = True,
    ) -> dict[str, Any]:
        return self._execute_render_video(
            request=request,
            language=language,
            render_backend=render_backend,
            quality=quality,
            conversation_id=conversation_id,
            run_key=_new_run_key(),
            flash=flash,
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
