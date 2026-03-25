"""
Manim rendering wrapper.

Writes generated code to a .py file, invokes Manim as a subprocess,
and returns the output video path or error log.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import List, Optional

from .tts import generate_audio, has_audio_stream, voice_for_language


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


TTS_MAX_WORKERS = max(1, _int_env("A4L_TTS_WORKERS", 4))


@dataclass
class RenderResult:
    success: bool
    video_path: Optional[Path] = None
    error_log: str = ""
    scene_name: str = ""


def find_scene_classes(code: str) -> List[str]:
    """Extract Scene subclass names from Manim code."""
    _SKIP = {"NarratedScene"}

    pattern = r"class\s+(\w+)\s*\(\s*\w*Scene\s*\)"
    matches = [m for m in re.findall(pattern, code) if m not in _SKIP]
    if matches:
        return matches

    pattern_broad = r"class\s+(\w+)\s*\([^)]*Scene[^)]*\)"
    matches_broad = [m for m in re.findall(pattern_broad, code) if m not in _SKIP]
    if matches_broad:
        return matches_broad

    pattern_any = r"class\s+(\w+)\s*\("
    return [m for m in re.findall(pattern_any, code) if m not in _SKIP]


def _sanitize_chinese_in_latex(code: str) -> str:
    """Auto-fix Chinese characters inside MathTex/Tex raw strings.

    Removes unsafe Chinese fragments from MathTex/Tex strings without leaking
    placeholder tokens into the rendered video.
    """
    import re

    def _has_chinese(s: str) -> bool:
        return bool(re.search(r'[\u4e00-\u9fff]', s))

    # Find all MathTex(...) and Tex(...) calls, check for Chinese in raw strings
    fixed = code
    for match in re.finditer(r'(MathTex|Tex)\s*\(r?"', code):
        # Find the closing quote of the raw string
        quote_start = match.end() - 1
        # Simple heuristic: find the matching closing quote
        i = quote_start + 1
        while i < len(code) and code[i] != '"':
            if code[i] == '\\':
                i += 1
            i += 1
        if i < len(code):
            raw_content = code[quote_start + 1:i]
            if _has_chinese(raw_content):
                # Strip Chinese text commands and raw Chinese characters.
                cleaned = re.sub(
                    r'\\text\{([^}]*[\u4e00-\u9fff][^}]*)\}',
                    r'\\quad',
                    raw_content,
                )
                cleaned = re.sub(
                    r'\\mathrm\{([^}]*[\u4e00-\u9fff][^}]*)\}',
                    r'\\quad',
                    cleaned,
                )
                cleaned = re.sub(r'[\u4e00-\u9fff]+', ' ', cleaned)
                cleaned = re.sub(r'[，。；：、“”‘’（）【】《》]', ' ', cleaned)
                cleaned = re.sub(r'\s+', ' ', cleaned).strip()
                if not cleaned:
                    cleaned = r"\\quad"
                if cleaned != raw_content:
                    fixed = fixed.replace(raw_content, cleaned)
    return fixed


def _pregenererate_tts(
    code: str,
    output_dir: Path,
    *,
    tts_voice: str | None = None,
) -> None:
    """Extract narration texts and pre-generate TTS audio."""
    import ast
    import hashlib

    texts = []
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
        cache_dir.mkdir(parents=True, exist_ok=True)
        unique_texts = list(dict.fromkeys(texts))

        def _ensure_tts(text: str) -> bool:
            h = hashlib.md5(text.encode('utf-8')).hexdigest()
            fp = cache_dir / f"{h}.mp3"
            if fp.exists():
                return False
            voice = tts_voice or voice_for_language("en")
            return bool(generate_audio(text, fp, voice=voice, rate="+5%"))

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


def render_scene(
    code: str,
    output_dir: Path,
    quality_flags: str = "-qm --fps 60",
    enable_tts: bool = True,
    tts_voice: str | None = None,
) -> RenderResult:
    """
    Render a Manim scene from source code.

    Writes *code* to ``output_dir/scene.py``, runs Manim, and locates
    the output video.

    Returns a RenderResult with success status, video path, and any
    error output.
    """
    code = _sanitize_chinese_in_latex(code)

    project_root = Path(__file__).resolve().parent.parent
    path_bootstrap = (
        "import sys\n"
        "from pathlib import Path\n"
        f'_PROJECT_ROOT = Path("{project_root.as_posix()}")\n'
        "if str(_PROJECT_ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(_PROJECT_ROOT))\n"
    )

    compatibility_imports = "from colortest.narrated_scene import NarratedScene\n"

    # Inject project import bootstrap and NarratedScene compatibility import.
    full_code = path_bootstrap + "\n" + compatibility_imports + "\n" + code
    
    output_dir.mkdir(parents=True, exist_ok=True)
    scene_file = output_dir / "scene.py"
    scene_file.write_text(full_code, encoding="utf-8")

    # Pre-generate TTS audio for renders that explicitly enable narration.
    if enable_tts:
        _pregenererate_tts(code, output_dir, tts_voice=tts_voice)

    scene_names = find_scene_classes(code)
    if not scene_names:
        return RenderResult(
            success=False,
            error_log="No Scene subclass found in generated code.",
            scene_name="",
        )

    scene_name = scene_names[0]
    media_dir = output_dir / "media"

    cmd = [
        sys.executable,
        "-m",
        "manim",
        str(scene_file),
        scene_name,
        *shlex.split(quality_flags),
        "--media_dir",
        str(media_dir),
    ]
    log_file = output_dir / "render_log.txt"

    returncode, combined_output = _run_subprocess_streaming(
        cmd=cmd,
        cwd=output_dir,
        log_file=log_file,
    )
    if returncode != 0:
        return RenderResult(
            success=False,
            error_log=combined_output[-5000:],
            scene_name=scene_name,
        )

    video_path = _find_video(media_dir, scene_name)
    if video_path is None:
        return RenderResult(
            success=False,
            error_log=f"Render completed but video not found under {media_dir}",
            scene_name=scene_name,
        )

    final_video = output_dir / "video.mp4"
    shutil.copy2(str(video_path), str(final_video))

    has_tts_calls = (
        "self.speak(" in code or
        "self.speak_with_subtitle(" in code
    )
    if enable_tts and has_tts_calls and not has_audio_stream(final_video):
        return RenderResult(
            success=False,
            error_log="Rendered video is missing an audio track even though the scene uses TTS calls.",
            scene_name=scene_name,
        )

    return RenderResult(
        success=True,
        video_path=final_video,
        scene_name=scene_name,
    )


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

    all_mp4 = sorted(candidates, key=lambda p: p.stat().st_mtime)
    if all_mp4:
        return all_mp4[-1]

    return None
