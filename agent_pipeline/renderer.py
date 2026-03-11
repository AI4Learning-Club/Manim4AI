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

from .tts import VOICE_ZH, generate_audio, has_audio_stream


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


_NARRATED_SCENE_CODE = """
import os, hashlib, glob, re, subprocess, shutil
from manim import *

try:
    from mutagen.mp3 import MP3
    _HAS_MUTAGEN = True
except ImportError:
    _HAS_MUTAGEN = False

class NarratedScene(Scene):
    SUBTITLE_SAFE_BOTTOM = -0.9
    CONTENT_TOP_LIMIT = 2.95
    CONTENT_SIDE_LIMIT = 6.1
    SECTION_BADGE_BUFF = 0.34

    def setup(self):
        self._section_badge = None
        self._section_badge_text = None
        self._subtitle_mob = None

    def _audio_duration(self, fp: str, text: str) -> float:
        if _HAS_MUTAGEN:
            try:
                return MP3(fp).info.length
            except Exception:
                pass
        if shutil.which("ffprobe"):
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                fp,
            ]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0 and result.stdout.strip():
                    return float(result.stdout.strip())
            except Exception:
                pass
        return max(1.6, len(text) * 0.22)

    def speak(self, text: str) -> float:
        h = hashlib.md5(text.encode('utf-8')).hexdigest()
        # Search for pre-generated audio in tts_cache
        candidates = glob.glob(os.path.join("tts_cache", f"{h}.mp3"))
        if not candidates:
            candidates = glob.glob(os.path.join("**", "tts_cache", f"{h}.mp3"), recursive=True)
        if candidates:
            fp = os.path.abspath(candidates[0])
            try:
                self.add_sound(fp)
                return self._audio_duration(fp, text)
            except Exception:
                pass
        return max(1.6, len(text) * 0.22)

    def _content_bottom_limit(self):
        return self.SUBTITLE_SAFE_BOTTOM

    def _keep_clear_of_section_badge(self, group):
        if self._section_badge is None:
            return group

        badge_left = self._section_badge.get_left()[0] - 0.18
        badge_bottom = self._section_badge.get_bottom()[1] - 0.14
        group_right = group.get_right()[0]
        group_top = group.get_top()[1]
        if group_right > badge_left and group_top > badge_bottom:
            dx = group_right - badge_left
            dy = group_top - badge_bottom
            if dx >= dy:
                group.shift(LEFT * (dx + 0.18))
            else:
                group.shift(DOWN * (dy + 0.12))
        return group

    def fit_group(self, group, max_width: float = 12.0, max_height: float = 6.5):
        if group.width > max_width:
            group.scale_to_fit_width(max_width)
        if group.height > max_height:
            group.scale_to_fit_height(max_height)
        group.move_to(UP * 0.28)
        if group.get_left()[0] < -self.CONTENT_SIDE_LIMIT:
            group.shift(RIGHT * (-self.CONTENT_SIDE_LIMIT - group.get_left()[0]))
        if group.get_right()[0] > self.CONTENT_SIDE_LIMIT:
            group.shift(LEFT * (group.get_right()[0] - self.CONTENT_SIDE_LIMIT))
        content_bottom_limit = self._content_bottom_limit()
        if group.get_bottom()[1] < content_bottom_limit:
            group.shift(UP * (content_bottom_limit - group.get_bottom()[1]))
        if group.get_top()[1] > self.CONTENT_TOP_LIMIT:
            group.shift(DOWN * (group.get_top()[1] - self.CONTENT_TOP_LIMIT))
        self._keep_clear_of_section_badge(group)
        if group.get_bottom()[1] < content_bottom_limit:
            group.shift(UP * (content_bottom_limit - group.get_bottom()[1]))
        return group

    def _build_title_chip(self, text: str, font_size: float = 22, max_width: float = 4.6):
        label = Text(text, font_size=font_size, weight=BOLD)
        if label.width > max_width:
            label.scale_to_fit_width(max_width)
        box = RoundedRectangle(
            corner_radius=0.22,
            width=label.width + 0.6,
            height=label.height + 0.38,
            stroke_color=YELLOW,
            stroke_width=2,
            fill_color="#18263C",
            fill_opacity=0.92,
        )
        return VGroup(box, label.move_to(box.get_center()))

    def show_section_header(self, text: str):
        if self._section_badge is not None and self._section_badge_text == text:
            return self._section_badge

        intro = self._build_title_chip(text, font_size=32, max_width=8.4)
        intro.move_to(ORIGIN)

        animations = []
        if self._section_badge is not None:
            animations.append(FadeOut(self._section_badge, shift=UP * 0.15))
        if animations:
            self.play(*animations, run_time=0.25)

        self.play(
            DrawBorderThenFill(intro[0]),
            FadeIn(intro[1], shift=UP * 0.08),
            run_time=0.38,
        )
        self.play(intro.animate.scale(0.64).to_corner(UR, buff=self.SECTION_BADGE_BUFF), run_time=0.38)
        self._section_badge = intro
        self._section_badge_text = text
        return self._section_badge

    def _normalize_subtitle_text(self, text: str):
        cleaned = re.sub(r"\\s+", " ", text).strip()
        if not cleaned:
            return ""
        return cleaned

    def make_subtitle_panel(self, text: str, font_size: float = 17, max_width: float = 11.8):
        text = self._normalize_subtitle_text(text)
        label = Text(text, font_size=font_size, weight=MEDIUM)
        if label.width > max_width:
            label.scale_to_fit_width(max_width)
        if label.height > 0.42:
            label.scale_to_fit_height(0.42)
        label.set_stroke(color=BLACK, width=8, background=True)
        label.to_edge(DOWN, buff=0.18)
        label.set_z_index(100)
        return label

    def set_subtitle(self, text: str, run_time: float = 0.25):
        new_panel = self.make_subtitle_panel(text)
        if self._subtitle_mob is None:
            self.play(FadeIn(new_panel, shift=UP * 0.08), run_time=run_time)
        else:
            self.play(ReplacementTransform(self._subtitle_mob, new_panel), run_time=run_time)
        self._subtitle_mob = new_panel
        return self._subtitle_mob

    def clear_subtitle(self, run_time: float = 0.2):
        if self._subtitle_mob is not None:
            self.play(FadeOut(self._subtitle_mob, shift=DOWN * 0.08), run_time=run_time)
            self._subtitle_mob = None

    def speak_with_subtitle(self, text: str, *animations, run_time: float | None = None, clear_after: bool = False):
        dur = self.speak(text)
        new_panel = self.make_subtitle_panel(text)
        subtitle_anim = (
            FadeIn(new_panel, shift=UP * 0.08)
            if self._subtitle_mob is None
            else ReplacementTransform(self._subtitle_mob, new_panel)
        )
        self._subtitle_mob = new_panel
        anim_time = run_time or dur
        if animations:
            self.play(subtitle_anim, *animations, run_time=anim_time)
            if dur > anim_time:
                self.wait(dur - anim_time)
        else:
            self.play(subtitle_anim, run_time=min(dur, 0.35))
            if dur > 0.35:
                self.wait(dur - 0.35)
        if clear_after:
            self.clear_subtitle()
        return dur

    def make_page(self, title, body, buff: float = 0.35):
        page = VGroup(title, body).arrange(DOWN, buff=buff)
        return self.fit_group(page, max_height=5.9)

    def make_two_panel_page(self, title, left, right, panel_gap: float = 0.8):
        if left.width > 5.0:
            left.scale_to_fit_width(5.0)
        if right.width > 4.4:
            right.scale_to_fit_width(4.4)
        if left.height > 4.35:
            left.scale_to_fit_height(4.35)
        if right.height > 4.35:
            right.scale_to_fit_height(4.35)
        body = VGroup(left, right).arrange(RIGHT, buff=panel_gap, aligned_edge=UP)
        if body.width > 10.6:
            body.scale_to_fit_width(10.6)
        return self.make_page(title, body)

    def make_graph_text_page(self, title, graph_group, text_group, panel_gap: float = 1.0):
        if graph_group.width > 4.8:
            graph_group.scale_to_fit_width(4.8)
        if graph_group.height > 4.15:
            graph_group.scale_to_fit_height(4.15)
        if text_group.width > 4.0:
            text_group.scale_to_fit_width(4.0)
        if text_group.height > 4.15:
            text_group.scale_to_fit_height(4.15)
        body = VGroup(graph_group, text_group).arrange(RIGHT, buff=panel_gap, aligned_edge=UP)
        if body.width > 10.4:
            body.scale_to_fit_width(10.4)
        return self.make_page(title, body, buff=0.4)

    def limit_text_block(self, block, max_width: float = 4.0, max_height: float = 4.0):
        if block.width > max_width:
            block.scale_to_fit_width(max_width)
        if block.height > max_height:
            block.scale_to_fit_height(max_height)
        return block

    def stack_panel(
        self,
        top,
        bottom,
        buff: float = 0.18,
        max_width: float = 5.4,
        max_height: float = 4.2,
    ):
        panel = VGroup(top, bottom).arrange(DOWN, buff=buff)
        if panel.width > max_width:
            panel.scale_to_fit_width(max_width)
        if panel.height > max_height:
            panel.scale_to_fit_height(max_height)
        return panel
"""

def _pregenererate_tts(code: str, output_dir: Path) -> None:
    """Extract narration texts and pre-generate TTS audio."""
    import ast

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
        import hashlib

        cache_dir = output_dir / "tts_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        unique_texts = list(dict.fromkeys(texts))

        generated = 0
        for text in unique_texts:
            h = hashlib.md5(text.encode('utf-8')).hexdigest()
            fp = cache_dir / f"{h}.mp3"
            if fp.exists():
                continue
            if generate_audio(text, fp, voice=VOICE_ZH, rate="+5%"):
                generated += 1
        print(f"  Pre-generated {generated} TTS audio files")
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

    has_tts_calls = (
        "self.speak(" in code or
        "self.speak_with_subtitle(" in code
    )
    if has_tts_calls and not has_audio_stream(final_video):
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

    for mp4 in media_dir.rglob("*.mp4"):
        if scene_name in mp4.stem:
            return mp4

    all_mp4 = sorted(media_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime)
    if all_mp4:
        return all_mp4[-1]

    return None
