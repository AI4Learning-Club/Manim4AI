from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import subprocess

from manim import *

try:
    from mutagen.mp3 import MP3

    _HAS_MUTAGEN = True
except ImportError:
    _HAS_MUTAGEN = False


class NarratedScene(Scene):
    TOP_BAND_TOP = 3.68
    TOP_BAND_BOTTOM = 2.82
    BODY_BAND_TOP = TOP_BAND_BOTTOM
    SUBTITLE_SAFE_BOTTOM = -0.9
    CONTENT_TOP_LIMIT = BODY_BAND_TOP
    CONTENT_SIDE_LIMIT = 6.1
    SECTION_BADGE_BUFF = 0.34
    SUBTITLE_TRANSITION_TIME = 0.18
    SUBTITLE_TEXT_COLOR = "#EDF5FF"
    SUBTITLE_STROKE_COLOR = "#08182D"

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
        digest = hashlib.md5(text.encode("utf-8")).hexdigest()
        candidates = glob.glob(os.path.join("tts_cache", f"{digest}.mp3"))
        if not candidates:
            candidates = glob.glob(
                os.path.join("**", "tts_cache", f"{digest}.mp3"),
                recursive=True,
            )
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

    def _band_center_y(self, top: float, bottom: float) -> float:
        return 0.5 * (top + bottom)

    def top_band_center(self):
        return UP * self._band_center_y(self.TOP_BAND_TOP, self.TOP_BAND_BOTTOM)

    def body_band_center(self):
        return UP * self._band_center_y(self.CONTENT_TOP_LIMIT, self._content_bottom_limit())

    def top_band_height(self) -> float:
        return self.TOP_BAND_TOP - self.TOP_BAND_BOTTOM

    def body_band_height(self) -> float:
        return self.CONTENT_TOP_LIMIT - self._content_bottom_limit()

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

    def _clamp_vertical_band(self, group, *, top_limit: float, bottom_limit: float, side_limit: float | None = None):
        """Clamp a block into a fixed vertical band and side limits."""
        side_limit = self.CONTENT_SIDE_LIMIT if side_limit is None else side_limit
        if group.get_left()[0] < -side_limit:
            group.shift(RIGHT * (-side_limit - group.get_left()[0]))
        if group.get_right()[0] > side_limit:
            group.shift(LEFT * (group.get_right()[0] - side_limit))
        if group.get_bottom()[1] < bottom_limit:
            group.shift(UP * (bottom_limit - group.get_bottom()[1]))
        if group.get_top()[1] > top_limit:
            group.shift(DOWN * (group.get_top()[1] - top_limit))
        return group

    def _fit_to_vertical_band(
        self,
        group,
        *,
        band_top: float,
        band_bottom: float,
        max_width: float,
        max_height: float | None = None,
        center=None,
    ):
        """Scale a block for a fixed vertical band, then place and clamp it."""
        if max_height is None:
            max_height = band_top - band_bottom
        else:
            max_height = min(max_height, max(0.1, band_top - band_bottom))
        if group.width > max_width:
            group.scale_to_fit_width(max_width)
        if group.height > max_height:
            group.scale_to_fit_height(max_height)
        if center is not None:
            group.move_to(center)
        return self._clamp_vertical_band(group, top_limit=band_top, bottom_limit=band_bottom)

    def clamp_group(self, group):
        """Clamp a finished body block into the body band above subtitles."""
        return self._clamp_vertical_band(
            group,
            top_limit=self.BODY_BAND_TOP,
            bottom_limit=self._content_bottom_limit(),
        )

    def fit_group(self, group, max_width: float = 12.0, max_height: float | None = None, center=None):
        """Scale/place a finished body block into the body band above subtitles."""
        if center is None:
            center = self.body_band_center()
        return self._fit_to_vertical_band(
            group,
            band_top=self.BODY_BAND_TOP,
            band_bottom=self._content_bottom_limit(),
            max_width=max_width,
            max_height=max_height,
            center=center,
        )

    def fit_to_top_band(self, group, max_width: float = 11.8, max_height: float | None = None, center=None):
        """Scale/place a title-like block into the shared top band."""
        if max_height is None:
            max_height = self.top_band_height()
        if center is None:
            center = self.top_band_center()
        return self._fit_to_vertical_band(
            group,
            band_top=self.TOP_BAND_TOP,
            band_bottom=self.TOP_BAND_BOTTOM,
            max_width=max_width,
            max_height=max_height,
            center=center,
        )

    def fit_to_body_band(self, group, max_width: float = 12.0, max_height: float | None = None, center=None):
        """Scale/place a finished body block into the body band above subtitles."""
        return self.fit_group(group, max_width=max_width, max_height=max_height, center=center)

    def safe_top_title(self, title, font_size: float = 34, max_width: float = 11.4):
        title = self._coerce_page_title(title, font_size=font_size)
        return self.fit_to_top_band(title, max_width=max_width, max_height=self.top_band_height() * 0.95)

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
        self.fit_to_top_band(intro, max_width=8.4, max_height=self.top_band_height() * 0.96)

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
        target = intro.copy().scale(0.64).to_corner(UR, buff=self.SECTION_BADGE_BUFF)
        self._clamp_vertical_band(
            target,
            top_limit=self.TOP_BAND_TOP,
            bottom_limit=self.TOP_BAND_BOTTOM,
        )
        self.play(
            Transform(intro, target),
            run_time=0.38,
        )
        self._section_badge = intro
        self._section_badge_text = text
        return self._section_badge

    def _normalize_subtitle_text(self, text: str):
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return ""
        return cleaned

    def make_subtitle_panel(self, text: str, font_size: float = 17, max_width: float = 11.8):
        text = self._normalize_subtitle_text(text)
        label = Text(text, font_size=font_size, weight=MEDIUM, color=self.SUBTITLE_TEXT_COLOR)
        if label.width > max_width:
            label.scale_to_fit_width(max_width)
        if label.height > 0.42:
            label.scale_to_fit_height(0.42)
        label.set_stroke(color=self.SUBTITLE_STROKE_COLOR, width=6, background=True)
        label.to_edge(DOWN, buff=0.18)
        label.set_z_index(100)
        return label

    def _show_subtitle_panel(self, new_panel, run_time: float | None = None):
        transition_time = min(
            self.SUBTITLE_TRANSITION_TIME,
            run_time if run_time is not None else self.SUBTITLE_TRANSITION_TIME,
        )
        if self._subtitle_mob is None:
            self.play(FadeIn(new_panel, shift=UP * 0.06), run_time=transition_time)
        else:
            old_panel = self._subtitle_mob
            self.play(
                FadeOut(old_panel, shift=DOWN * 0.04),
                FadeIn(new_panel, shift=UP * 0.04),
                run_time=transition_time,
            )
        self._subtitle_mob = new_panel
        return transition_time

    def set_subtitle(self, text: str, run_time: float = 0.25):
        new_panel = self.make_subtitle_panel(text)
        self._show_subtitle_panel(new_panel, run_time=run_time)
        return self._subtitle_mob

    def clear_subtitle(self, run_time: float = 0.2):
        if self._subtitle_mob is not None:
            self.play(FadeOut(self._subtitle_mob, shift=DOWN * 0.08), run_time=run_time)
            self._subtitle_mob = None

    def speak_with_subtitle(
        self,
        text: str,
        *animations,
        run_time: float | None = None,
        clear_after: bool = False,
    ):
        dur = self.speak(text)
        new_panel = self.make_subtitle_panel(text)
        anim_time = run_time or dur
        subtitle_time = self._show_subtitle_panel(new_panel, run_time=anim_time)
        remaining_anim_time = max(anim_time - subtitle_time, 0)
        if animations:
            if remaining_anim_time > 0:
                self.play(*animations, run_time=remaining_anim_time)
            if dur > anim_time:
                self.wait(dur - anim_time)
        else:
            remaining_wait = max(dur - subtitle_time, 0)
            if remaining_wait > 0:
                self.wait(remaining_wait)
        if clear_after:
            self.clear_subtitle()
        return dur

    def _coerce_page_title(self, title, font_size: float = 34):
        if not isinstance(title, str):
            return title
        title = self._normalize_subtitle_text(title) or " "
        getter = getattr(self, "get_text", None)
        if callable(getter):
            try:
                return getter(title, font_size=font_size, weight=BOLD)
            except Exception:
                pass
        return Text(title, font_size=font_size, weight=BOLD)

    def make_page(self, title, body, buff: float = 0.35):
        title = self.safe_top_title(title)
        self.fit_to_body_band(body)
        return Group(title, body)

    def make_two_panel_page(self, title, left, right, panel_gap: float = 0.8):
        if left.width > 5.0:
            left.scale_to_fit_width(5.0)
        if right.width > 4.4:
            right.scale_to_fit_width(4.4)
        if left.height > 4.35:
            left.scale_to_fit_height(4.35)
        if right.height > 4.35:
            right.scale_to_fit_height(4.35)
        body = Group(left, right).arrange(RIGHT, buff=panel_gap, aligned_edge=UP)
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
        body = Group(graph_group, text_group).arrange(
            RIGHT,
            buff=panel_gap,
            aligned_edge=UP,
        )
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
        panel = Group(top, bottom).arrange(DOWN, buff=buff)
        if panel.width > max_width:
            panel.scale_to_fit_width(max_width)
        if panel.height > max_height:
            panel.scale_to_fit_height(max_height)
        return panel

    def connect_side(self, source, target, direction=RIGHT, buff: float = 0.12, **kwargs):
        start = source.get_critical_point(direction) + direction * buff
        end = target.get_critical_point(-direction) - direction * buff
        return Arrow(start, end, buff=0, **kwargs)

    def connect_vertical(self, source, target, buff: float = 0.12, **kwargs):
        direction = UP if target.get_center()[1] >= source.get_center()[1] else DOWN
        start = source.get_critical_point(direction) + direction * buff
        end = target.get_critical_point(-direction) - direction * buff
        return Arrow(start, end, buff=0, **kwargs)


__all__ = ["NarratedScene"]
