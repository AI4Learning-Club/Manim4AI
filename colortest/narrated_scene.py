from __future__ import annotations

import glob
import hashlib
import os
import re
import shutil
import subprocess
import weakref
import numpy as np

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
    SUBTITLE_SAFE_RATIO = 0.10  # Hard constraint: reserve exactly the bottom 10% for subtitles.
    SUBTITLE_SAFE_BOTTOM = -config.frame_height / 2 + config.frame_height * SUBTITLE_SAFE_RATIO
    CONTENT_TOP_LIMIT = BODY_BAND_TOP
    CONTENT_SIDE_LIMIT = 6.1
    SUBTITLE_TRANSITION_TIME = 0.18
    SUBTITLE_TEXT_COLOR = "#EDF5FF"

    def setup(self):
        self._section_badge = None
        self._subtitle_mob = None
        self._block_bindings = []
        self._anchor_bindings = []

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
        fitted = self._clamp_vertical_band(group, top_limit=band_top, bottom_limit=band_bottom)
        self.sync_bound_blocks()
        self.sync_bound_anchors()
        return fitted

    @staticmethod
    def _binding_scale_factor(binding):
        block = binding["block"]
        old_width = max(float(binding["width"]), 1e-6)
        old_height = max(float(binding["height"]), 1e-6)
        ratios = []
        if block.width > 1e-6:
            ratios.append(float(block.width) / old_width)
        if block.height > 1e-6:
            ratios.append(float(block.height) / old_height)
        if not ratios:
            return 1.0
        return sum(ratios) / len(ratios)

    @staticmethod
    def _sync_block_binding(binding):
        mob = binding.get("mob")
        block = binding.get("block")
        if mob is None or block is None:
            return

        old_center = np.array(binding["center"], dtype=float)
        new_center = np.array(block.get_center(), dtype=float)
        scale = NarratedScene._binding_scale_factor(binding)
        if abs(scale - 1.0) > 1e-6:
            mob.scale(scale, about_point=old_center)
        delta = new_center - old_center
        if np.linalg.norm(delta) > 1e-8:
            mob.shift(delta)

        binding["center"] = new_center
        binding["width"] = max(float(block.width), 1e-6)
        binding["height"] = max(float(block.height), 1e-6)

    @staticmethod
    def _install_binding_updater(mob, binding, sync_fn):
        def _follow_binding(bound_mob, dt=0, _binding=binding, _sync=sync_fn):
            _sync(_binding)
            return bound_mob

        mob.add_updater(_follow_binding)
        binding["updater"] = _follow_binding

    @staticmethod
    def _remove_binding_updater(binding):
        mob = binding.get("mob")
        updater = binding.get("updater")
        if mob is not None and updater is not None:
            mob.remove_updater(updater)

    def _unbind_bindings(self, binding_attr: str, *mobs):
        if not mobs:
            return

        targets = {id(mob) for mob in mobs if mob is not None}
        if not targets:
            return

        kept = []
        for binding in getattr(self, binding_attr):
            mob = binding.get("mob")
            if mob is None or id(mob) not in targets:
                kept.append(binding)
                continue
            NarratedScene._remove_binding_updater(binding)
        setattr(self, binding_attr, kept)

    def _clear_bindings(self, binding_attr: str):
        for binding in getattr(self, binding_attr):
            NarratedScene._remove_binding_updater(binding)
        setattr(self, binding_attr, [])

    def _sync_bindings(self, binding_attr: str, sync_fn):
        for binding in list(getattr(self, binding_attr)):
            sync_fn(binding)

    def bind_to_block(self, mob, block, *, live=True):
        """Bind a dependent mobject to a parent block's future scale/shift lifecycle."""
        if mob is None or block is None:
            return mob

        self.unbind_from_block(mob)
        binding = {
            "mob": mob,
            "block": block,
            "center": np.array(block.get_center(), dtype=float),
            "width": max(float(block.width), 1e-6),
            "height": max(float(block.height), 1e-6),
            "updater": None,
        }

        if live:
            NarratedScene._install_binding_updater(
                mob,
                binding,
                NarratedScene._sync_block_binding,
            )

        self._block_bindings.append(binding)
        return mob

    def bind_many_to_block(self, block, *mobs, live=True):
        """Convenience helper for binding many dependent mobjects to one block."""
        for mob in mobs:
            if mob is not None:
                self.bind_to_block(mob, block, live=live)
        return mobs

    def unbind_from_block(self, *mobs):
        self._unbind_bindings("_block_bindings", *mobs)

    def clear_block_bindings(self):
        self._clear_bindings("_block_bindings")

    def sync_bound_blocks(self):
        self._sync_bindings("_block_bindings", NarratedScene._sync_block_binding)

    @staticmethod
    def _resolve_anchor_builder(binding):
        builder = binding.get("builder")
        if callable(builder):
            return builder
        if isinstance(builder, str):
            scene_ref = binding.get("scene_ref")
            scene = scene_ref() if scene_ref is not None else None
            if scene is None:
                return None
            candidate = getattr(scene, builder, None)
            if callable(candidate):
                return candidate
        return None

    @staticmethod
    def _sync_anchor_binding(binding):
        mob = binding.get("mob")
        if mob is None:
            return
        builder = NarratedScene._resolve_anchor_builder(binding)
        if builder is None:
            return
        rebuilt = builder(*binding.get("args", ()), **binding.get("kwargs", {}))
        if rebuilt is None:
            return
        mob.become(rebuilt)

    def bind_to_anchor(
        self,
        mob,
        builder,
        *builder_args,
        live=True,
        sync_now=True,
        **builder_kwargs,
    ):
        """
        Keep an existing dependent mobject synchronized to live anchors by
        rebuilding it from a builder callback.

        `builder` may be a callable or the name of a scene helper method.
        """
        if mob is None:
            return mob

        self.unbind_from_anchor(mob)
        binding = {
            "mob": mob,
            "builder": builder,
            "args": tuple(builder_args),
            "kwargs": dict(builder_kwargs),
            "scene_ref": weakref.ref(self),
            "updater": None,
        }

        if sync_now:
            NarratedScene._sync_anchor_binding(binding)

        if live:
            NarratedScene._install_binding_updater(
                mob,
                binding,
                NarratedScene._sync_anchor_binding,
            )

        self._anchor_bindings.append(binding)
        return mob

    def build_on_anchor(self, builder, *builder_args, live=True, **builder_kwargs):
        """
        Build dependent geometry from live anchors and keep it synchronized.

        Example:
            secant = self.build_on_anchor("secant_segment_on_axes", axes, 2.9, 3.1)
        """
        if callable(builder):
            initial = builder(*builder_args, **builder_kwargs)
        elif isinstance(builder, str):
            candidate = getattr(self, builder, None)
            if not callable(candidate):
                raise ValueError(f"Unknown anchor builder: {builder}")
            initial = candidate(*builder_args, **builder_kwargs)
        else:
            raise TypeError("builder must be a callable or a scene helper method name")

        return self.bind_to_anchor(
            initial,
            builder,
            *builder_args,
            live=live,
            sync_now=False,
            **builder_kwargs,
        )

    def bind_many_to_anchor(self, builder_specs, *, live=True):
        """
        Convenience helper for building/binding several dependent objects.

        Each item in `builder_specs` should be:
            (builder, arg1, arg2, ...)
        or
            (builder, (arg1, arg2, ...), {"kw": value})
        """
        built = []
        for spec in builder_specs:
            if not isinstance(spec, tuple) or not spec:
                continue
            builder = spec[0]
            if len(spec) == 3 and isinstance(spec[1], tuple) and isinstance(spec[2], dict):
                built.append(self.build_on_anchor(builder, *spec[1], live=live, **spec[2]))
            else:
                built.append(self.build_on_anchor(builder, *spec[1:], live=live))
        return built

    def unbind_from_anchor(self, *mobs):
        self._unbind_bindings("_anchor_bindings", *mobs)

    def clear_anchor_bindings(self):
        self._clear_bindings("_anchor_bindings")

    def sync_bound_anchors(self):
        self._sync_bindings("_anchor_bindings", NarratedScene._sync_anchor_binding)

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

    def fit_body(self, body, max_width: float = 12.0, max_height: float | None = None, center=None):
        """Preferred helper: fit a page's single body root into the body band."""
        if center is None:
            center = self.body_band_center()
        return self._fit_to_vertical_band(
            body,
            band_top=self.BODY_BAND_TOP,
            band_bottom=self._content_bottom_limit(),
            max_width=max_width,
            max_height=max_height,
            center=center,
        )

    def make_page_title(self, title, font_size: float = 34, max_width: float = 11.4):
        """Preferred helper: build the long top title for a page."""
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

    def show_section_badge_once(
        self,
        text: str,
        run_time: float = 0.38,
        hold_time: float = 0.08,
        fade_time: float = 0.22,
    ):
        """Show a short section badge once, then clear it before page content begins."""
        badge = self._build_title_chip(text, font_size=32, max_width=8.4)
        self.fit_to_top_band(badge, max_width=8.4, max_height=self.top_band_height() * 0.96)

        if self._section_badge is not None:
            self.play(FadeOut(self._section_badge, shift=UP * 0.12), run_time=min(fade_time, 0.18))

        self._section_badge = badge
        self.play(
            DrawBorderThenFill(badge[0]),
            FadeIn(badge[1], shift=UP * 0.08),
            run_time=run_time,
        )
        if hold_time > 0:
            self.wait(hold_time)
        self.play(FadeOut(badge, shift=UP * 0.08), run_time=fade_time)
        self._section_badge = None
        return badge

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
