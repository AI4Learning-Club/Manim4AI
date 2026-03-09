"""
Manim rendering wrapper.

Writes generated code to a .py file, invokes Manim as a subprocess,
and returns the output video path or error log.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


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

    Replaces patterns like MathTex(r"\\text{中文}") with safe alternatives.
    This is a safety net — the LLM should avoid this, but sometimes doesn't.
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
                # Replace \text{中文} patterns with placeholder
                cleaned = re.sub(
                    r'\\text\{([^}]*[\u4e00-\u9fff][^}]*)\}',
                    r'\\mathrm{CHINESE}',
                    raw_content,
                )
                cleaned = re.sub(
                    r'\\mathrm\{([^}]*[\u4e00-\u9fff][^}]*)\}',
                    r'\\mathrm{CHINESE}',
                    cleaned,
                )
                if cleaned != raw_content:
                    fixed = fixed.replace(raw_content, cleaned)
    return fixed


_NARRATED_SCENE_CODE = """
import os, hashlib, glob
from manim import *

try:
    from mutagen.mp3 import MP3
    _HAS_MUTAGEN = True
except ImportError:
    _HAS_MUTAGEN = False

class NarratedScene(Scene):
    def speak(self, text: str) -> float:
        h = hashlib.md5(text.encode('utf-8')).hexdigest()
        # Search for pre-generated audio in tts_cache
        candidates = glob.glob(os.path.join("tts_cache", f"{h}.mp3"))
        if not candidates:
            candidates = glob.glob(os.path.join("**", "tts_cache", f"{h}.mp3"), recursive=True)
        if candidates and _HAS_MUTAGEN:
            fp = os.path.abspath(candidates[0])
            try:
                duration = MP3(fp).info.length
                self.add_sound(fp)
                return duration
            except Exception:
                pass
        return max(1.0, len(text) * 0.12)

    def fit_group(self, group, max_width: float = 12.0, max_height: float = 6.5):
        if group.width > max_width:
            group.scale_to_fit_width(max_width)
        if group.height > max_height:
            group.scale_to_fit_height(max_height)
        group.move_to(ORIGIN)
        return group

    def make_page(self, title, body, buff: float = 0.35):
        page = VGroup(title, body).arrange(DOWN, buff=buff)
        return self.fit_group(page)

    def make_two_panel_page(self, title, left, right, panel_gap: float = 0.6):
        if left.width > 5.4:
            left.scale_to_fit_width(5.4)
        if right.width > 5.4:
            right.scale_to_fit_width(5.4)
        if left.height > 4.8:
            left.scale_to_fit_height(4.8)
        if right.height > 4.8:
            right.scale_to_fit_height(4.8)
        body = VGroup(left, right).arrange(RIGHT, buff=panel_gap, aligned_edge=UP)
        return self.make_page(title, body)

    def stack_panel(
        self,
        top,
        bottom,
        buff: float = 0.18,
        max_width: float = 5.4,
        max_height: float = 4.8,
    ):
        panel = VGroup(top, bottom).arrange(DOWN, buff=buff)
        if panel.width > max_width:
            panel.scale_to_fit_width(max_width)
        if panel.height > max_height:
            panel.scale_to_fit_height(max_height)
        return panel
"""

def _pregenererate_tts(code: str, output_dir: Path) -> None:
    """Extract all self.speak("...") texts and pre-generate TTS audio."""
    import re as _re
    texts = _re.findall(r'self\.speak\(["\'](.+?)["\']\)', code)
    if not texts:
        return
    try:
        import hashlib
        import asyncio
        import edge_tts

        cache_dir = output_dir / "tts_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        async def _gen_all():
            for text in texts:
                h = hashlib.md5(text.encode('utf-8')).hexdigest()
                fp = cache_dir / f"{h}.mp3"
                if fp.exists():
                    continue
                comm = edge_tts.Communicate(text, "zh-CN-YunxiNeural", rate="+5%")
                await comm.save(str(fp))

        asyncio.run(_gen_all())
        print(f"  Pre-generated {len(texts)} TTS audio files")
    except Exception as exc:
        print(f"  TTS pre-generation warning: {exc}")


def render_scene(
    code: str,
    output_dir: Path,
    quality_flags: str = "-qm --fps 60",
    timeout_sec: int = 360,
) -> RenderResult:
    """
    Render a Manim scene from source code.

    Writes *code* to ``output_dir/scene.py``, runs Manim, and locates
    the output video.

    Returns a RenderResult with success status, video path, and any
    error output.
    """
    code = _sanitize_chinese_in_latex(code)
    
    # Inject NarratedScene class at the top
    full_code = _NARRATED_SCENE_CODE + "\n" + code
    
    output_dir.mkdir(parents=True, exist_ok=True)
    scene_file = output_dir / "scene.py"
    scene_file.write_text(full_code, encoding="utf-8")

    # Pre-generate all TTS audio so rendering doesn't block on network
    _pregenererate_tts(code, output_dir)

    scene_names = find_scene_classes(code)
    if not scene_names:
        return RenderResult(
            success=False,
            error_log="No Scene subclass found in generated code.",
            scene_name="",
        )

    scene_name = scene_names[0]
    media_dir = output_dir / "media"

    cmd = (
        f"python -m manim {scene_file} {scene_name} "
        f"{quality_flags} --media_dir \"{media_dir}\""
    )

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(output_dir),
            encoding="utf-8",
            errors="replace",
        )

        combined_output = (result.stdout or "") + "\n" + (result.stderr or "")
        log_file = output_dir / "render_log.txt"
        log_file.write_text(combined_output, encoding="utf-8")

        if result.returncode != 0:
            return RenderResult(
                success=False,
                error_log=combined_output[-5000:],
                scene_name=scene_name,
            )

    except subprocess.TimeoutExpired:
        return RenderResult(
            success=False,
            error_log=f"Manim render timed out after {timeout_sec}s",
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

    return RenderResult(
        success=True,
        video_path=final_video,
        scene_name=scene_name,
    )


def _find_video(media_dir: Path, scene_name: str) -> Optional[Path]:
    """Search for the rendered .mp4 under media_dir."""
    if not media_dir.exists():
        return None

    for mp4 in media_dir.rglob("*.mp4"):
        if scene_name in mp4.stem:
            return mp4

    all_mp4 = sorted(media_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if all_mp4:
        return all_mp4[-1]

    return None
