from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from manim import *

from colortest.narrated_scene import NarratedScene

from .registry import DEFAULT_THEME_ID, get_theme


class AI4LearningBaseScene(NarratedScene):
    """Shared base scene for AI4Learning videos with pluggable theme packs."""

    default_font = "SimSun"  # 默认中文正文字体：再试更经典的宋体风格
    latin_font = "Times New Roman"  # 默认英文字体：标题、数字、英文标签统一走衬线体
    local_icon_dir = Path(__file__).resolve().parents[2] / "icon"  # 本地图标目录
    theme_id = DEFAULT_THEME_ID  # 默认主题：未显式指定时使用注册表默认项

    # --- 主题解析 / token 读取 ---
    def resolve_theme(self):
        return get_theme(getattr(self, "theme_id", DEFAULT_THEME_ID))

    def theme_token(self, name, fallback=None):
        return self.theme.token(name, fallback)

    # --- 语义颜色 helper：优先给 LLM 和场景代码直接调用 ---
    def get_accent_color(self, level: str = "primary"):
        level = str(level).lower()
        if level == "secondary":
            return self.theme_token(
                "accent_secondary",
                self.theme_token("accent_primary", "#3B82F6"),
            )
        return self.theme_token("accent_primary", "#3B82F6")

    def get_formula_highlight_color(self, level: str = "primary"):
        level = str(level).lower()
        if level == "secondary":
            return self.theme_token(
                "formula_highlight_secondary",
                self.get_accent_color("secondary"),
            )
        return self.theme_token(
            "formula_highlight_primary",
            self.get_accent_color("primary"),
        )

    def get_warning_color(self):
        return self.theme_token("warning_color", "#EF4444")

    def get_success_color(self):
        return self.theme_token("success_color", "#10B981")

    def get_border_color(self):
        return self.theme_token(
            "border_color",
            self.theme_token("panel_stroke", self.get_accent_color("primary")),
        )

    def get_axis_color(self):
        return self.theme_token("grid_or_axis_color", self.get_border_color())

    def get_panel_fill_color(self):
        return self.theme_token(
            "panel_fill_color",
            self.theme_token("canvas_bg", "#18263C"),
        )

    def get_panel_fill_opacity(self):
        return float(self.theme_token("panel_fill_opacity", 0.0))

    def get_panel_stroke_color(self):
        return self.theme_token("panel_stroke", self.get_border_color())

    # --- 面板样式 helper：统一面板填充色 / 描边色 / 透明度 ---
    def make_panel_style(
        self,
        stroke_width: float = 2,
        fill_opacity: float | None = None,
        fill_color=None,
        stroke_color=None,
    ):
        return {
            "stroke_color": stroke_color or self.get_panel_stroke_color(),
            "stroke_width": stroke_width,
            "fill_color": fill_color or self.get_panel_fill_color(),
            "fill_opacity": (
                self.get_panel_fill_opacity()
                if fill_opacity is None
                else float(fill_opacity)
            ),
        }

    # --- 面板构造 helper：给任意内容快速包一层主题化 panel ---
    def make_panel(
        self,
        content,
        padding: float = 0.25,
        corner_radius: float = 0.2,
        stroke_width: float = 2,
        fill_opacity: float | None = None,
        fill_color=None,
        stroke_color=None,
        **kwargs,
    ):
        style = self.make_panel_style(
            stroke_width=stroke_width,
            fill_opacity=fill_opacity,
            fill_color=fill_color,
            stroke_color=stroke_color,
        )
        style.update(kwargs)
        panel = SurroundingRectangle(
            content,
            buff=padding,
            corner_radius=corner_radius,
            **style,
        )
        panel.set_z_index(getattr(content, "z_index", 0) - 1)
        return Group(panel, content)

    # --- 公式高亮 helper：支持按索引 / slice / tex 文本批量染色 ---
    def _resolve_formula_targets(self, formula, selectors):
        targets = []
        for selector in selectors:
            if isinstance(selector, (list, tuple, set)):
                targets.extend(self._resolve_formula_targets(formula, selector))
                continue
            if isinstance(selector, int):
                try:
                    targets.append(formula[selector])
                except Exception:
                    continue
                continue
            if isinstance(selector, slice):
                try:
                    targets.extend(list(formula[selector]))
                except Exception:
                    continue
                continue
            if isinstance(selector, str):
                try:
                    matches = formula.get_parts_by_tex(selector)
                except Exception:
                    matches = ()
                targets.extend(list(matches))
                continue
            if hasattr(selector, "set_color"):
                targets.append(selector)
        return targets

    def highlight_formula_parts(self, formula, primary=(), secondary=()):
        # 主高亮：通常用于当前讲解重点
        for target in self._resolve_formula_targets(formula, primary):
            target.set_color(self.get_formula_highlight_color("primary"))
        # 次高亮：通常用于对照项 / 第二重点
        for target in self._resolve_formula_targets(formula, secondary):
            target.set_color(self.get_formula_highlight_color("secondary"))
        return formula

    # --- 背景构造：背景图 + tint 叠层 ---
    def _build_background(self):
        background = self.theme.background
        bg_layers = []

        if background.asset_path and background.asset_path.exists():
            # 统一转成 RGBA：避免 Manim / Cairo 读取不同图片模式时不稳定
            with Image.open(background.asset_path) as image:
                rgba_pixels = np.array(image.convert("RGBA"))

            bg_image = ImageMobject(rgba_pixels)
            bg_image.scale_to_fit_width(config.frame_width)
            if bg_image.height < config.frame_height:
                bg_image.scale_to_fit_height(config.frame_height)
            bg_image.move_to(ORIGIN)
            bg_image.set_z_index(-100)
            bg_layers.append(bg_image)

        tint_color = background.tint_color or self.theme_token("canvas_bg")
        tint_opacity = float(background.tint_opacity or 0.0)
        if tint_color and tint_opacity > 0:
            bg_tint = Rectangle(
                width=config.frame_width,
                height=config.frame_height,
                stroke_width=0,
                fill_color=tint_color,
                fill_opacity=tint_opacity,
            )
            bg_tint.move_to(ORIGIN)
            bg_tint.set_z_index(-90)
            bg_layers.append(bg_tint)

        if not bg_layers:
            return None
        if len(bg_layers) == 1:
            return bg_layers[0]
        return Group(*bg_layers)

    # --- 场景初始化：应用主题到字幕、相机背景和持久背景层 ---
    def setup(self):
        self.theme = self.resolve_theme()
        self.SUBTITLE_TEXT_COLOR = self.theme_token("subtitle_text", "#EDF5FF")
        super().setup()
        self.camera.background_color = self.theme_token("canvas_bg", config.background_color)
        self._bg_image = self._build_background()
        if self._bg_image is not None:
            self.add(self._bg_image)

    # --- title chip：跟随主题取文字、描边、底色 ---
    def _make_title_chip_label(self, text: str, font_size: float):
        return self.get_text(
            text,
            color=self.theme_token("section_badge_text", self.theme_token("text_main", "#F8FAFC")),
            font_size=font_size,
            weight=BOLD,
        )

    def _title_chip_box_style(self) -> dict:
        return {
            "stroke_color": self.theme_token(
                "section_badge_stroke",
                self.theme_token("accent_primary", YELLOW),
            ),
            "stroke_width": 2,
            "fill_color": self.theme_token(
                "section_badge_fill",
                self.theme_token("panel_fill_color", "#18263C"),
            ),
            "fill_opacity": float(
                self.theme_token("section_badge_fill_opacity", 0.92)
            ),
        }

    # --- 持久层控制：清场时保留背景 ---
    def get_persistent_mobjects(self):
        bg_image = getattr(self, "_bg_image", None)
        return [bg_image] if bg_image is not None else []

    def clear_scene_keep_bg(self, run_time=0.7, wait_time=0.3):
        persistent = self.get_persistent_mobjects()
        to_fade = [
            mob
            for mob in self.mobjects
            if all(mob is not keep for keep in persistent)
        ]
        if to_fade:
            self.play(FadeOut(Group(*to_fade)), run_time=run_time)
        if hasattr(self, "clear_block_bindings"):
            self.clear_block_bindings()
        if hasattr(self, "clear_anchor_bindings"):
            self.clear_anchor_bindings()
        self._subtitle_mob = None
        self._page_title_mob = None
        self.wait(wait_time)

    # --- 本地图标加载：只从项目 icon 目录取资源 ---
    def get_local_icon_path(self, filename):
        requested = Path(str(filename)).name.strip()
        if not requested:
            raise ValueError("Local icon filename is empty")

        icon_dir = Path(self.local_icon_dir)
        exact_path = icon_dir / requested
        if exact_path.exists():
            return exact_path

        requested_lower = requested.lower()
        requested_stem = Path(requested).stem.lower()
        stem_matches = []
        for candidate in icon_dir.iterdir():
            if not candidate.is_file():
                continue
            candidate_name = candidate.name.lower()
            if candidate_name == requested_lower:
                return candidate
            if candidate.stem.lower() == requested_stem:
                stem_matches.append(candidate)

        if len(stem_matches) == 1:
            return stem_matches[0]

        raise FileNotFoundError(f"Local icon not found: {requested}")

    def load_local_icon(self, filename, height=0.9, width=None, **kwargs):
        # 自动区分 SVG 和位图：外部场景不需要自己判断
        path = self.get_local_icon_path(filename)
        if path.suffix.lower() == ".svg":
            icon = SVGMobject(str(path), **kwargs)
        else:
            icon = ImageMobject(str(path), **kwargs)
        if height is not None:
            icon.scale_to_fit_height(height)
        if width is not None:
            icon.scale_to_fit_width(width)
        return icon

    # --- 文本 / 公式 helper：把常用语义颜色封装成稳定接口 ---
    def resolve_text_font(self, string, explicit_font=None):
        if explicit_font:
            return explicit_font
        text = str(string or "")
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in text)
        has_non_ascii = any(ord(ch) > 127 for ch in text if not ch.isspace())
        if has_cjk or has_non_ascii:
            return getattr(self, "default_font", "Noto Serif SC")
        return getattr(self, "latin_font", "Times New Roman")

    def _make_subtitle_label(self, text: str, font_size: float):
        font = self.resolve_text_font(text)
        return Text(
            text,
            font=font,
            font_size=font_size,
            weight=MEDIUM,
            color=self.SUBTITLE_TEXT_COLOR,
        )

    def get_text(self, string, color=None, font_size=36, **kwargs):
        font = self.resolve_text_font(string, explicit_font=kwargs.pop("font", None))
        color = color or self.theme_token("text_main", "#EDF5FF")
        return Text(string, color=color, font=font, font_size=font_size, **kwargs)

    def get_secondary_text(self, string, font_size=32, **kwargs):
        color = kwargs.pop("color", None) or self.theme_token(
            "text_secondary",
            self.theme_token("text_main", "#EDF5FF"),
        )
        return self.get_text(string, color=color, font_size=font_size, **kwargs)

    def get_muted_text(self, string, font_size=28, **kwargs):
        color = kwargs.pop("color", None) or self.theme_token(
            "text_muted",
            self.theme_token("text_secondary", self.theme_token("text_main", "#EDF5FF")),
        )
        return self.get_text(string, color=color, font_size=font_size, **kwargs)

    def get_warning_text(self, string, font_size=32, **kwargs):
        color = kwargs.pop("color", None) or self.get_warning_color()
        return self.get_text(string, color=color, font_size=font_size, **kwargs)

    def get_success_text(self, string, font_size=32, **kwargs):
        color = kwargs.pop("color", None) or self.get_success_color()
        return self.get_text(string, color=color, font_size=font_size, **kwargs)

    def get_math(self, string, color=None, font_size=48, **kwargs):
        color = color or self.theme_token("formula_base", self.theme_token("text_main", "#EDF5FF"))
        return MathTex(string, color=color, font_size=font_size, **kwargs)

    def get_highlighted_math(self, string, color=None, font_size=48, level: str = "primary", **kwargs):
        # level 支持 primary / secondary：直接接公式高亮 token
        color = color or self.get_formula_highlight_color(level)
        return MathTex(string, color=color, font_size=font_size, **kwargs)
