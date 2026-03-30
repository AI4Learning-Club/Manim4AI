"""
Manim rendering wrapper for Scene Pack code.

Writes generated code to a single ``scene.py`` file, renders each segment scene
declared in ``SCENE_MANIFEST`` as its own Manim subprocess, and concatenates
the segment videos into one final ``video.mp4``.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import List, Optional

from .scene_pack import SegmentSpec, parse_scene_pack
from .tts import (
    SCENE_TTS_RATE,
    TTS_GLOBAL_CACHE_ENV,
    TTS_RATE_ENV,
    TTS_VOICE_ENV,
    generate_audio,
    get_global_tts_cache_dir,
    has_audio_stream,
    scene_tts_global_cache_path,
    scene_tts_round_cache_path,
    voice_for_language,
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


TTS_MAX_WORKERS = max(1, _int_env("A4L_TTS_WORKERS", 4))


@dataclass
class SegmentRenderResult:
    segment_id: str
    scene_name: str
    order: int
    output_dir: Path
    success: bool
    video_path: Optional[Path] = None
    error_log: str = ""


@dataclass
class RenderResult:
    success: bool
    video_path: Optional[Path] = None
    error_log: str = ""
    scene_name: str = ""
    segments: List[SegmentRenderResult] = field(default_factory=list)


def _sanitize_chinese_in_latex(code: str) -> str:
    """Auto-fix Chinese characters inside MathTex/Tex raw strings."""

    def _has_chinese(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text))

    replacements: list[tuple[int, int, str]] = []
    for match in re.finditer(r'(MathTex|Tex)\s*\(r?"', code):
        quote_start = match.end() - 1
        i = quote_start + 1
        while i < len(code) and code[i] != '"':
            if code[i] == "\\":
                i += 1
            i += 1
        if i >= len(code):
            continue
        raw_content = code[quote_start + 1 : i]
        if not _has_chinese(raw_content):
            continue
        cleaned = re.sub(
            r"\\text\{([^}]*[\u4e00-\u9fff][^}]*)\}",
            r"\\quad",
            raw_content,
        )
        cleaned = re.sub(
            r"\\mathrm\{([^}]*[\u4e00-\u9fff][^}]*)\}",
            r"\\quad",
            cleaned,
        )
        cleaned = re.sub(r"[\u4e00-\u9fff]+", " ", cleaned)
        cleaned = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff\ufffd]", " ", cleaned)
        cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            cleaned = r"\\quad"
        if cleaned != raw_content:
            replacements.append((quote_start + 1, i, cleaned))

    if not replacements:
        return code

    parts: list[str] = []
    cursor = 0
    for start, end, cleaned in replacements:
        parts.append(code[cursor:start])
        parts.append(cleaned)
        cursor = end
    parts.append(code[cursor:])
    return "".join(parts)


def _pregenererate_tts(
    code: str,
    output_dir: Path,
    *,
    tts_voice: str | None = None,
) -> None:
    """Extract narration texts and pre-generate TTS audio once per round."""
    texts: list[str] = []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"speak", "speak_with_subtitle"}:
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                texts.append(first_arg.value)
    except SyntaxError:
        texts = []

    if not texts:
        return

    try:
        cache_dir = output_dir / "tts_cache"
        global_cache_dir = get_global_tts_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        global_cache_dir.mkdir(parents=True, exist_ok=True)
        unique_texts = list(dict.fromkeys(texts))

        def _ensure_tts(text: str) -> bool:
            voice = tts_voice or voice_for_language("en")
            round_fp = scene_tts_round_cache_path(text, cache_dir)
            global_fp = scene_tts_global_cache_path(
                text,
                voice,
                SCENE_TTS_RATE,
                global_cache_dir,
            )
            if round_fp.exists():
                return False
            if global_fp.exists():
                shutil.copy2(str(global_fp), str(round_fp))
                return False
            if not generate_audio(text, global_fp, voice=voice, rate=SCENE_TTS_RATE):
                return False
            shutil.copy2(str(global_fp), str(round_fp))
            return True

        generated = 0
        max_workers = min(TTS_MAX_WORKERS, len(unique_texts))
        if max_workers <= 1:
            for text in unique_texts:
                if _ensure_tts(text):
                    generated += 1
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_ensure_tts, text) for text in unique_texts]
                for future in as_completed(futures):
                    if future.result():
                        generated += 1
        print(f"  Pre-generated {generated} TTS audio files")
    except Exception as exc:
        print(f"  TTS pre-generation warning: {exc}")


def _run_subprocess_streaming(
    cmd: list[str],
    cwd: Path,
    log_file: Path,
) -> tuple[int, str]:
    """Run a subprocess while streaming combined stdout/stderr to console and log."""
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    queue: Queue[Optional[str]] = Queue()
    output_chunks: list[str] = []

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for line in iter(process.stdout.readline, ""):
                queue.put(line)
        finally:
            process.stdout.close()
            queue.put(None)

    reader = Thread(target=_reader, daemon=True)
    reader.start()

    reader_done = False
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with log_file.open("w", encoding="utf-8") as handle:
        while True:
            try:
                chunk = queue.get(timeout=0.2)
            except Empty:
                if reader_done and process.poll() is not None:
                    break
                continue

            if chunk is None:
                reader_done = True
                if process.poll() is not None and queue.empty():
                    break
                continue

            print(chunk, end="", flush=True)
            handle.write(chunk)
            handle.flush()
            output_chunks.append(chunk)

        while not queue.empty():
            chunk = queue.get_nowait()
            if chunk is None:
                continue
            print(chunk, end="", flush=True)
            handle.write(chunk)
            handle.flush()
            output_chunks.append(chunk)

    return process.wait(), "".join(output_chunks)


def render_scene_pack(
    code: str,
    output_dir: Path,
    quality_flags: str = "-qm --fps 60",
    enable_tts: bool = True,
    tts_voice: str | None = None,
    selected_segment_ids: Optional[set[str]] = None,
) -> RenderResult:
    """
    Render a Scene Pack from source code.

    Writes *code* to ``output_dir/scene.py``, renders the selected segment
    scenes declared in ``SCENE_MANIFEST`` in parallel, and concatenates the
    available segment videos into ``output_dir/video.mp4``.
    """
    code = _sanitize_chinese_in_latex(code)

    try:
        scene_pack = parse_scene_pack(code)
    except ValueError as exc:
        return RenderResult(
            success=False,
            error_log=str(exc),
            scene_name="ScenePack",
            segments=[],
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    segments_root = output_dir / "segments"
    full_render = not selected_segment_ids
    if full_render and segments_root.exists():
        shutil.rmtree(segments_root)
    segments_root.mkdir(parents=True, exist_ok=True)

    full_code = _build_scene_file_code(code)
    scene_file = output_dir / "scene.py"
    scene_file.write_text(full_code, encoding="utf-8")

    if enable_tts:
        voice = tts_voice or voice_for_language("en")
        os.environ[TTS_VOICE_ENV] = voice
        os.environ[TTS_RATE_ENV] = SCENE_TTS_RATE
        os.environ.setdefault(TTS_GLOBAL_CACHE_ENV, str(get_global_tts_cache_dir()))
        _pregenererate_tts(code, output_dir, tts_voice=tts_voice)

    segment_results = _render_segments(
        manifest=scene_pack.manifest,
        scene_file=scene_file,
        output_dir=output_dir,
        quality_flags=quality_flags,
        selected_segment_ids=selected_segment_ids,
    )
    ordered_results = sorted(segment_results, key=lambda item: item.order)

    failed = [segment for segment in ordered_results if not segment.success]
    if failed:
        error_log = _format_segment_failures(failed)
        _write_round_render_log(output_dir, ordered_results, final_error=error_log)
        return RenderResult(
            success=False,
            error_log=error_log,
            scene_name="ScenePack",
            segments=ordered_results,
        )

    final_video = output_dir / "video.mp4"
    concat_error = _concat_segment_videos(ordered_results, final_video)
    if concat_error:
        _write_round_render_log(output_dir, ordered_results, final_error=concat_error)
        return RenderResult(
            success=False,
            error_log=concat_error,
            scene_name="ScenePack",
            segments=ordered_results,
        )

    has_tts_calls = "self.speak(" in code or "self.speak_with_subtitle(" in code
    if enable_tts and has_tts_calls and not has_audio_stream(final_video):
        error_log = (
            "Rendered Scene Pack video is missing an audio track even though the code uses TTS calls."
        )
        _write_round_render_log(output_dir, ordered_results, final_error=error_log)
        return RenderResult(
            success=False,
            error_log=error_log,
            scene_name="ScenePack",
            segments=ordered_results,
        )

    _write_round_render_log(output_dir, ordered_results, final_video=final_video)
    return RenderResult(
        success=True,
        video_path=final_video,
        scene_name="ScenePack",
        segments=ordered_results,
    )


def render_scene(
    code: str,
    output_dir: Path,
    quality_flags: str = "-qm --fps 60",
    enable_tts: bool = True,
    tts_voice: str | None = None,
) -> RenderResult:
    """Thin compatibility wrapper for the Scene Pack renderer."""
    return render_scene_pack(
        code,
        output_dir,
        quality_flags=quality_flags,
        enable_tts=enable_tts,
        tts_voice=tts_voice,
    )


def _build_scene_file_code(code: str) -> str:
    project_root = Path(__file__).resolve().parent.parent
    path_bootstrap = (
        "import sys\n"
        "from pathlib import Path\n"
        f'_PROJECT_ROOT = Path("{project_root.as_posix()}")\n'
        "if str(_PROJECT_ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(_PROJECT_ROOT))\n"
    )
    compatibility_imports = "from colortest.narrated_scene import NarratedScene\n"
    return path_bootstrap + "\n" + compatibility_imports + "\n" + code


def _render_segments(
    *,
    manifest: List[SegmentSpec],
    scene_file: Path,
    output_dir: Path,
    quality_flags: str,
    selected_segment_ids: Optional[set[str]],
) -> List[SegmentRenderResult]:
    selected = set(selected_segment_ids or [])
    if selected:
        return [
            _render_or_reuse_segment(
                segment=segment,
                scene_file=scene_file,
                output_dir=output_dir,
                quality_flags=quality_flags,
                should_render=segment.segment_id in selected,
            )
            for segment in manifest
        ]

    max_workers = len(manifest)
    if max_workers <= 1:
        return [
            _render_or_reuse_segment(
                segment=segment,
                scene_file=scene_file,
                output_dir=output_dir,
                quality_flags=quality_flags,
                should_render=True,
            )
            for segment in manifest
        ]

    results: List[SegmentRenderResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                _render_or_reuse_segment,
                segment=segment,
                scene_file=scene_file,
                output_dir=output_dir,
                quality_flags=quality_flags,
                should_render=True,
            )
            for segment in manifest
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def _render_or_reuse_segment(
    *,
    segment: SegmentSpec,
    scene_file: Path,
    output_dir: Path,
    quality_flags: str,
    should_render: bool,
) -> SegmentRenderResult:
    segment_dir = _segment_output_dir(output_dir, segment)
    if not should_render:
        existing = _existing_segment_result(segment, segment_dir)
        if existing is not None:
            return existing
        return SegmentRenderResult(
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            order=segment.order,
            output_dir=segment_dir,
            success=False,
            error_log=(
                f"Segment `{segment.segment_id}` was not selected for rerender, "
                "but no prior segment video exists to reuse."
            ),
        )

    segment_dir.mkdir(parents=True, exist_ok=True)
    media_dir = segment_dir / "media"
    log_file = segment_dir / "render_log.txt"

    cmd = [
        sys.executable,
        "-m",
        "manim",
        str(scene_file.resolve()),
        segment.scene_name,
        *shlex.split(quality_flags),
        "--media_dir",
        str(media_dir.resolve()),
    ]

    returncode, combined_output = _run_subprocess_streaming(
        cmd=cmd,
        cwd=output_dir,
        log_file=log_file,
    )
    if returncode != 0:
        return SegmentRenderResult(
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            order=segment.order,
            output_dir=segment_dir,
            success=False,
            error_log=combined_output[-5000:],
        )

    video_path = _find_video(media_dir, segment.scene_name)
    if video_path is None:
        return SegmentRenderResult(
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            order=segment.order,
            output_dir=segment_dir,
            success=False,
            error_log=f"Render completed but segment video not found under {media_dir}",
        )

    final_segment_video = segment_dir / "video.mp4"
    shutil.copy2(str(video_path), str(final_segment_video))
    return SegmentRenderResult(
        segment_id=segment.segment_id,
        scene_name=segment.scene_name,
        order=segment.order,
        output_dir=segment_dir,
        success=True,
        video_path=final_segment_video,
    )


def _segment_output_dir(output_dir: Path, segment: SegmentSpec) -> Path:
    return output_dir / "segments" / f"{segment.order:02d}_{_sanitize_segment_id(segment.segment_id)}"


def _existing_segment_result(
    segment: SegmentSpec,
    segment_dir: Path,
) -> Optional[SegmentRenderResult]:
    final_segment_video = segment_dir / "video.mp4"
    if not final_segment_video.exists():
        return None
    return SegmentRenderResult(
        segment_id=segment.segment_id,
        scene_name=segment.scene_name,
        order=segment.order,
        output_dir=segment_dir,
        success=True,
        video_path=final_segment_video,
    )


def _concat_segment_videos(
    segment_results: List[SegmentRenderResult],
    output_path: Path,
) -> str:
    if not segment_results:
        return "No segment videos were produced for concatenation."

    ordered_videos = [segment.video_path for segment in segment_results if segment.video_path]
    if len(ordered_videos) != len(segment_results):
        return "Cannot concatenate segment videos because at least one segment is missing its final video."

    if len(ordered_videos) == 1:
        shutil.copy2(str(ordered_videos[0]), str(output_path))
        return ""

    if not shutil.which("ffmpeg"):
        return "ffmpeg not found, cannot concatenate Scene Pack segments."

    concat_list = output_path.parent / "segments_concat.txt"
    concat_list.write_text(
        "".join(_concat_list_line(video) for video in ordered_videos),
        encoding="utf-8",
    )

    copy_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output_path),
    ]
    copy_result = subprocess.run(
        copy_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if copy_result.returncode == 0 and output_path.exists():
        return ""

    reencode_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]
    reencode_result = subprocess.run(
        reencode_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if reencode_result.returncode == 0 and output_path.exists():
        return ""

    return (
        "ffmpeg concat failed.\n\n"
        "Copy-mode stderr:\n"
        f"{copy_result.stderr[-3000:]}\n\n"
        "Re-encode stderr:\n"
        f"{reencode_result.stderr[-3000:]}"
    )


def _concat_list_line(path: Path) -> str:
    normalized = path.resolve().as_posix().replace("'", r"'\''")
    return f"file '{normalized}'\n"


def _format_segment_failures(failed_segments: List[SegmentRenderResult]) -> str:
    chunks = []
    for segment in failed_segments:
        header = f"[{segment.order:02d}:{segment.segment_id} -> {segment.scene_name}]"
        body = segment.error_log.strip() or "Unknown segment render failure."
        chunks.append(f"{header}\n{body}")
    return "\n\n".join(chunks)


def _write_round_render_log(
    output_dir: Path,
    segment_results: List[SegmentRenderResult],
    *,
    final_video: Optional[Path] = None,
    final_error: str = "",
) -> None:
    lines = ["Scene Pack render summary", ""]
    for segment in sorted(segment_results, key=lambda item: item.order):
        status = "OK" if segment.success else "FAIL"
        lines.append(
            f"[{segment.order:02d}] {segment.segment_id} | {segment.scene_name} | {status}"
        )
        lines.append(f"  dir: {segment.output_dir}")
        if segment.video_path:
            lines.append(f"  video: {segment.video_path}")
        if segment.error_log:
            snippet = segment.error_log.strip()
            if len(snippet) > 1000:
                snippet = snippet[-1000:]
            lines.append("  error:")
            lines.append(snippet)
        lines.append("")

    if final_video:
        lines.append(f"Final video: {final_video}")
    if final_error:
        lines.append("Final error:")
        lines.append(final_error)

    (output_dir / "render_log.txt").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _sanitize_segment_id(segment_id: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_-]+", "_", segment_id.strip())
    cleaned = cleaned.strip("_").lower()
    return cleaned or "segment"


def _find_video(media_dir: Path, scene_name: str) -> Optional[Path]:
    """Search for the rendered .mp4 under media_dir."""
    if not media_dir.exists():
        return None

    candidates = [
        mp4
        for mp4 in media_dir.rglob("*.mp4")
        if "partial_movie_files" not in {part.lower() for part in mp4.parts}
    ]

    for mp4 in candidates:
        if scene_name in mp4.stem:
            return mp4

    all_mp4 = sorted(candidates, key=lambda item: item.stat().st_mtime)
    if all_mp4:
        return all_mp4[-1]
    return None


__all__ = [
    "SegmentRenderResult",
    "RenderResult",
    "render_scene_pack",
    "render_scene",
]
