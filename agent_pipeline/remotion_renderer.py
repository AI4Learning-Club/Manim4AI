"""Helpers for packaging the final Manim video into a Remotion hybrid project.

Adapted for Scene Pack architecture: the source video is the concatenated
output of all segments declared in SCENE_MANIFEST, so segment boundaries
can be mapped directly to Remotion manim_chunk slots.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
REMOTION_PROJECT_DIR = ROOT_DIR / "remotion_renderer"
REMOTION_RUNTIME_PUBLIC_DIR = REMOTION_PROJECT_DIR / "public" / "runtime"


def _require_cv2():
    try:
        import cv2  # type: ignore
    except ModuleNotFoundError as exc:
        raise RuntimeError("OpenCV dependency `cv2` is required for Remotion hybrid rendering") from exc
    return cv2


def _safe_name(path: Path) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in path.name)


def _video_metadata(video_path: Path) -> Dict[str, float]:
    cv2 = _require_cv2()
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {"fps": 30.0, "duration_sec": 1.0, "frames": 30.0}
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    cap.release()
    if fps <= 0:
        fps = 30.0
    duration = frames / fps if frames > 0 else 1.0
    if duration <= 0:
        duration = 1.0
    return {"fps": fps, "duration_sec": duration, "frames": max(frames, fps)}


def _int_frames(seconds: float, fps: int) -> int:
    return max(1, int(round(max(seconds, 0.0) * fps)))


def _normalize_label(text: Any, default: str = "") -> str:
    if isinstance(text, str):
        text = text.strip()
        return text or default
    if text is None:
        return default
    return str(text).strip() or default


def _chapter_captions(teaching_plan: Dict[str, Any], core_frames: int) -> List[Dict[str, Any]]:
    sections = teaching_plan.get("sections") if isinstance(teaching_plan.get("sections"), list) else []
    cleaned = [section for section in sections if isinstance(section, dict)]
    if not cleaned:
        return [{"start": 0, "end": core_frames, "text": "Core explanation"}]

    step = max(1, core_frames // len(cleaned))
    captions: List[Dict[str, Any]] = []
    for index, section in enumerate(cleaned):
        start = index * step
        end = core_frames if index == len(cleaned) - 1 else min(core_frames, (index + 1) * step)
        takeaway = _normalize_label(section.get("key_takeaway"))
        title = _normalize_label(section.get("title"), f"Part {index + 1}")
        captions.append(
            {
                "start": start,
                "end": max(start + 1, end),
                "text": title if not takeaway else f"{title}: {takeaway}",
            }
        )
    return captions


def _find_scene(storyboard: Dict[str, Any], scene_id: str) -> Optional[Dict[str, Any]]:
    scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    for scene in scenes:
        if isinstance(scene, dict) and scene.get("id") == scene_id:
            return scene
    return None


def _find_scene_by_type(storyboard: Dict[str, Any], scene_type: str) -> Optional[Dict[str, Any]]:
    scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    for scene in scenes:
        if isinstance(scene, dict) and scene.get("type") == scene_type:
            return scene
    backend_map = {"title_card": "remotion", "core_lesson": "manim", "summary_card": "remotion"}
    target_backend = backend_map.get(scene_type)
    if not target_backend:
        return None
    matches = [s for s in scenes if isinstance(s, dict) and s.get("backend") == target_backend]
    if scene_type == "title_card" and matches:
        return matches[0]
    if scene_type == "summary_card" and matches:
        return matches[-1]
    if scene_type == "core_lesson" and matches:
        return matches[0]
    return None


def _strip_latex(text: str) -> str:
    text = re.sub(r'\$[^$]+\$', '', text)
    text = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', text)
    return re.sub(r'\s{2,}', ' ', text).strip()


def _runtime_asset_dir(run_dir: Path) -> Path:
    return REMOTION_RUNTIME_PUBLIC_DIR / _safe_name(run_dir)


def _collect_segment_durations(run_dir: Path) -> List[Dict[str, Any]]:
    """Read individual segment videos and collect their durations.

    This leverages the Scene Pack segment structure where each segment
    has its own video file under round1/segments/<order>_<id>/video.mp4.
    Returns a list of {segment_id, duration_sec, order} dicts.
    """
    segments_dir = run_dir / "round1" / "segments"
    if not segments_dir.exists():
        return []

    segment_infos: List[Dict[str, Any]] = []
    for segment_dir in sorted(segments_dir.iterdir()):
        if not segment_dir.is_dir():
            continue
        video = segment_dir / "video.mp4"
        if not video.exists():
            continue
        meta = _video_metadata(video)
        parts = segment_dir.name.split("_", 1)
        order = int(parts[0]) if parts[0].isdigit() else 0
        segment_id = parts[1] if len(parts) > 1 else segment_dir.name
        segment_infos.append({
            "segment_id": segment_id,
            "order": order,
            "duration_sec": meta["duration_sec"],
        })
    return sorted(segment_infos, key=lambda s: s["order"])


def _build_props(
    request_text: str,
    teaching_plan: Dict[str, Any],
    storyboard: Dict[str, Any],
    source_video: Path,
    runtime_asset_dir: Path,
    segment_durations: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    metadata = _video_metadata(source_video)
    fps = 30
    core_frames = _int_frames(metadata["duration_sec"], fps)
    theme = storyboard.get("theme") if isinstance(storyboard.get("theme"), dict) else {}
    asset_name = source_video.name
    video_asset = f"runtime/{runtime_asset_dir.name}/{asset_name}"

    sections = teaching_plan.get("sections") if isinstance(teaching_plan.get("sections"), list) else []
    closing = teaching_plan.get("closing") if isinstance(teaching_plan.get("closing"), dict) else {}
    section_titles = [
        _normalize_label(s.get("title"))
        for s in sections[:8]
        if isinstance(s, dict) and _normalize_label(s.get("title"))
    ]

    sb_scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    subtitle_plan = storyboard.get("subtitle_plan") if isinstance(storyboard.get("subtitle_plan"), dict) else {}
    highlight_keywords = subtitle_plan.get("highlight_keywords") if isinstance(subtitle_plan.get("highlight_keywords"), list) else []

    content_scenes = [
        s for s in sb_scenes
        if isinstance(s, dict) and s.get("type") not in {"title_card", "summary_card"}
    ]
    manim_chunks = [s for s in content_scenes if s.get("type") == "manim_chunk"]
    has_remotion_content = any(
        s.get("type") in {"concept_card", "chapter_card"}
        for s in content_scenes
    )
    use_segment_durations = bool(segment_durations and len(segment_durations) >= 1)
    is_interleaved = len(content_scenes) >= 2 and len(manim_chunks) >= 1 and has_remotion_content

    intro_scene = (
        next((s for s in sb_scenes if isinstance(s, dict) and s.get("type") == "title_card"), None)
        or {}
    )
    summary_scene = (
        next((s for s in sb_scenes if isinstance(s, dict) and s.get("type") == "summary_card"), None)
        or {}
    )

    def _clean(text: Any, fallback: str = "") -> str:
        return _strip_latex(_normalize_label(text, fallback))

    def _clean_list(items: Any, fallback: Optional[List[str]] = None) -> List[str]:
        if isinstance(items, list):
            result = [_strip_latex(str(b)) for b in items if isinstance(b, str) and b.strip()]
            if result:
                return result
        return list(fallback or [])

    intro_title = _clean(intro_scene.get("title")) or (section_titles[0] if section_titles else request_text[:60])
    intro_body = _clean(intro_scene.get("body")) or _clean(teaching_plan.get("teaching_promise"), "A visual explanation.")
    intro_bullets = _clean_list(intro_scene.get("bullets"), section_titles[:3])

    summary_title = _clean(summary_scene.get("title")) or "Key Takeaway"
    summary_body = _clean(summary_scene.get("body")) or _clean(closing.get("summary"), "Remember the core mechanism.")
    summary_bullets = _clean_list(
        summary_scene.get("bullets"),
        [_clean(closing.get("transfer_question", "")), _clean(closing.get("after_class_prompt", ""))],
    )
    summary_bullets = [b for b in summary_bullets if b]

    segments: List[Dict[str, Any]] = []

    segments.append({
        "id": "intro_card",
        "type": "title_card",
        "durationInFrames": _int_frames(float(intro_scene.get("duration_sec", 5) or 5), fps),
        "title": intro_title,
        "body": intro_body,
        "bullets": intro_bullets,
    })

    if content_scenes:
        n_chunks = max(1, len(manim_chunks))
        segment_frames = []
        if use_segment_durations and segment_durations:
            segment_frames = [
                _int_frames(float(seg_info.get("duration_sec", 1.0) or 1.0), fps)
                for seg_info in segment_durations
            ]
        else:
            frames_per_chunk = max(1, core_frames // n_chunks)
            remainder = core_frames - frames_per_chunk * n_chunks
            for idx in range(n_chunks):
                segment_frames.append(frames_per_chunk + (remainder if idx == n_chunks - 1 else 0))

        cumulative_frame = 0
        manim_index = 0
        for idx, scene in enumerate(content_scenes):
            scene_type = scene.get("type")
            if scene_type == "manim_chunk":
                chunk_frames = segment_frames[min(manim_index, len(segment_frames) - 1)]
                segments.append({
                    "id": scene.get("id", f"ch{manim_index + 1}_manim"),
                    "type": "manim_chunk",
                    "durationInFrames": chunk_frames,
                    "title": _clean(
                        scene.get("title"),
                        section_titles[manim_index] if manim_index < len(section_titles) else f"Part {manim_index + 1}",
                    ),
                    "body": _clean(scene.get("body")),
                    "videoAsset": video_asset,
                    "videoStartFrame": cumulative_frame,
                })
                cumulative_frame += chunk_frames
                manim_index += 1
                continue

            if scene_type in {"concept_card", "chapter_card"}:
                segments.append({
                    "id": scene.get("id", f"scene_{idx + 1}"),
                    "type": "concept_card" if scene_type == "concept_card" else "chapter_card",
                    "durationInFrames": _int_frames(float(scene.get("duration_sec", 6) or 6), fps),
                    "title": _clean(scene.get("title"), "Core idea"),
                    "body": _clean(scene.get("body")),
                    "bullets": _clean_list(scene.get("bullets")),
                    "visualNotes": _clean(scene.get("visual_notes")),
                    "chapterNumber": manim_index if scene_type == "chapter_card" else None,
                    "totalChapters": len(manim_chunks) if scene_type == "chapter_card" else None,
                })
    else:
        core_scene = next((s for s in sb_scenes if isinstance(s, dict) and s.get("backend") == "manim"), None) or {}
        segments.append({
            "id": "core_lesson",
            "type": "manim_video",
            "durationInFrames": core_frames,
            "title": _clean(core_scene.get("title"), "Core lesson"),
            "body": _clean(core_scene.get("body")),
            "videoAsset": video_asset,
        })

    segments.append({
        "id": "summary_card",
        "type": "summary_card",
        "durationInFrames": _int_frames(float(summary_scene.get("duration_sec", 5) or 5), fps),
        "title": summary_title,
        "body": summary_body,
        "bullets": summary_bullets,
    })

    props = {
        "meta": {
            "request": request_text,
            "lessonGoal": intro_title,
            "delivery": "hybrid-routed" if has_remotion_content else ("hybrid-interleaved" if is_interleaved else "hybrid-wrapped"),
        },
        "fps": fps,
        "width": 1920,
        "height": 1080,
        "theme": {
            "visualTone": _normalize_label(theme.get("visual_tone"), "modern classroom explainer"),
            "accent": _normalize_label(theme.get("accent_color"), "#7C3AED"),
            "accent2": _normalize_label(theme.get("accent_color_2"), "#22D3EE"),
            "background": _normalize_label(theme.get("background"), "#0F172A"),
            "panelBackground": _normalize_label(theme.get("panel_background"), "rgba(15, 23, 42, 0.72)"),
            "fontFamily": _normalize_label(theme.get("font_family"), "Inter, Arial, sans-serif"),
        },
        "captions": _chapter_captions(teaching_plan, core_frames),
        "chapters": [
            _clean(scene.get("title"))
            for scene in content_scenes
            if _clean(scene.get("title"))
        ] or section_titles,
        "highlightKeywords": highlight_keywords[:6],
        "segments": segments,
    }
    return props


def build_remotion_hybrid(
    run_dir: Path,
    request_text: str,
    teaching_plan: Dict[str, Any],
    storyboard: Dict[str, Any],
    source_video: Path,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "enabled": True,
        "project_dir": str(REMOTION_PROJECT_DIR),
        "rendered": False,
        "status": "scaffolded",
        "video_path": None,
    }

    if not REMOTION_PROJECT_DIR.exists():
        result["status"] = "missing_project_template"
        return result

    runtime_asset_dir = _runtime_asset_dir(run_dir)
    runtime_asset_dir.mkdir(parents=True, exist_ok=True)
    asset_path = runtime_asset_dir / source_video.name
    shutil.copy2(str(source_video), str(asset_path))

    segment_durations = _collect_segment_durations(run_dir)

    props = _build_props(
        request_text, teaching_plan, storyboard,
        asset_path, runtime_asset_dir,
        segment_durations=segment_durations,
    )
    props_path = runtime_asset_dir / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest_path = run_dir / "hybrid_timeline.json"
    manifest_path.write_text(json.dumps(props, ensure_ascii=False, indent=2), encoding="utf-8")

    result.update(
        {
            "runtime_dir": str(runtime_asset_dir),
            "asset_path": str(asset_path),
            "props_file": str(props_path),
            "timeline_file": str(manifest_path),
            "segment_count": len(segment_durations),
            "install_command": "cd remotion_renderer && npm install",
            "render_command": f"cd remotion_renderer && node scripts/render.mjs --props \"{props_path}\" --out \"{run_dir / 'delivery_hybrid.mp4'}\"",
        }
    )

    node_modules = REMOTION_PROJECT_DIR / "node_modules"
    if not node_modules.exists():
        result["status"] = "dependencies_missing"
        return result

    output_path = (run_dir / "delivery_hybrid.mp4").resolve()
    command = [
        "node",
        "scripts/render.mjs",
        "--props",
        str(props_path.resolve()),
        "--out",
        str(output_path),
    ]
    render_process = subprocess.run(
        command,
        cwd=str(REMOTION_PROJECT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log_path = run_dir / "hybrid_render_log.txt"
    log_path.write_text(
        (render_process.stdout or "") + "\n" + (render_process.stderr or ""),
        encoding="utf-8",
    )
    result["log_file"] = str(log_path)

    if render_process.returncode == 0 and output_path.exists():
        result.update({"rendered": True, "status": "rendered", "video_path": str(output_path)})
    else:
        result.update({"status": "render_failed", "error": (render_process.stderr or render_process.stdout or "").strip()[-4000:]})

    return result
