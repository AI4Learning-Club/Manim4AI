from __future__ import annotations

from manim import *

from colortest.ai4learning_theme import AI4LearningBaseScene


class _ThemePackPreviewBase(AI4LearningBaseScene):
    preview_title = "Theme Preview"

    def construct(self):
        title = self.get_text(self.preview_title, font_size=34, weight=BOLD)
        subtitle = self.get_text(
            f"{self.theme.display_name}  |  {self.theme.theme_id}",
            color=self.theme_token("text_secondary"),
            font_size=20,
        )
        page_title = self.make_page_title(title)

        text_column = VGroup(
            self._make_text_chip("Text Main", self.theme_token("text_main"), "primary title / body"),
            self._make_text_chip("Text Secondary", self.theme_token("text_secondary"), "supporting explanation"),
            self._make_text_chip("Text Muted", self.theme_token("text_muted"), "notes / low emphasis"),
            self._make_text_chip("Formula Base", self.theme_token("formula_base"), "default formula color"),
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)

        accent_column = VGroup(
            self._make_text_chip("Accent Primary", self.theme_token("accent_primary"), "main highlight"),
            self._make_text_chip("Accent Secondary", self.theme_token("accent_secondary"), "secondary highlight"),
            self._make_text_chip("Success", self.theme_token("success_color"), "correct / positive"),
            self._make_text_chip("Warning", self.theme_token("warning_color"), "error / caution"),
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)

        structure_column = VGroup(
            self._make_text_chip("Panel Fill", self.theme_token("panel_fill_color"), "panel background"),
            self._make_text_chip("Panel Stroke", self.theme_token("panel_stroke"), "panel edge color"),
            self._make_text_chip("Border Color", self.theme_token("border_color"), "general frame / shape"),
            self._make_text_chip("Axis / Grid", self.theme_token("grid_or_axis_color"), "graph support lines"),
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)

        top_row = VGroup(text_column, accent_column, structure_column).arrange(RIGHT, buff=0.42, aligned_edge=UP)

        panel_demo = self._make_panel_demo()
        graph_demo = self._make_graph_demo()
        bottom_row = VGroup(panel_demo, graph_demo).arrange(RIGHT, buff=0.58, aligned_edge=UP)

        body1 = VGroup(subtitle, top_row, bottom_row).arrange(DOWN, buff=0.26, aligned_edge=LEFT)
        self.fit_body(body1, max_width=12.0, max_height=6.0)

        self.play(FadeIn(page_title, shift=DOWN * 0.12), run_time=0.45)
        self.play(FadeIn(subtitle, shift=DOWN * 0.08), run_time=0.35)
        self.play(
            LaggedStart(*[FadeIn(chip, shift=RIGHT * 0.08) for chip in text_column], lag_ratio=0.08),
            LaggedStart(*[FadeIn(chip, shift=LEFT * 0.08) for chip in accent_column], lag_ratio=0.08),
            LaggedStart(*[FadeIn(chip, shift=UP * 0.08) for chip in structure_column], lag_ratio=0.08),
            run_time=0.9,
        )
        self.play(FadeIn(panel_demo, shift=UP * 0.08), run_time=0.75)
        self.play(FadeIn(graph_demo, shift=UP * 0.08), run_time=0.75)
        self.wait(0.8)

    def _make_text_chip(self, label_text, color, detail_text):
        swatch = Line(LEFT * 0.42, RIGHT * 0.42, color=color, stroke_width=10, stroke_opacity=1)
        swatch.set_cap_style(CapStyleType.ROUND)
        label = self.get_text(label_text, font_size=19, color=color, weight=BOLD)
        detail = self.get_text(detail_text, font_size=14, color=self.theme_token("text_secondary"))
        text_block = VGroup(label, detail).arrange(DOWN, buff=0.05, aligned_edge=LEFT)
        row = VGroup(swatch, text_block).arrange(RIGHT, buff=0.18, aligned_edge=UP)
        return row

    def _make_panel_demo(self):
        panel = RoundedRectangle(
            corner_radius=0.22,
            width=5.25,
            height=2.55,
            stroke_width=3,
            stroke_color=self.theme_token("panel_stroke"),
            fill_color=self.theme_token("panel_fill_color"),
            fill_opacity=min(float(self.theme_token("panel_fill_opacity", 0.0)) + 0.08, 0.94),
        )
        title = self.get_text("Panel + Formula", font_size=24, color=self.theme_token("text_main"), weight=BOLD)
        line = self.get_text("This is how text and formulas sit on the panel.", font_size=16, color=self.theme_token("text_secondary"))
        formula = VGroup(
            self.get_text("y", font_size=28, color=self.theme_token("formula_base"), weight=BOLD),
            self.get_text("=", font_size=24, color=self.theme_token("formula_base")),
            self.get_text("a x^2", font_size=28, color=self.theme_token("accent_primary"), weight=BOLD),
            self.get_text("+", font_size=24, color=self.theme_token("formula_base")),
            self.get_text("b x", font_size=28, color=self.theme_token("accent_secondary"), weight=BOLD),
            self.get_text("+", font_size=24, color=self.theme_token("formula_base")),
            self.get_text("c", font_size=28, color=self.theme_token("warning_color"), weight=BOLD),
        ).arrange(RIGHT, buff=0.1, aligned_edge=DOWN)
        foot = self.get_text("axis / border / panel all remain theme-bound", font_size=14, color=self.theme_token("text_muted"))
        content = VGroup(title, line, formula, foot).arrange(DOWN, buff=0.12, aligned_edge=LEFT)
        content.move_to(panel.get_center())
        content.align_to(panel, LEFT).shift(RIGHT * 0.34)
        border_probe = RoundedRectangle(
            corner_radius=0.16,
            width=0.72,
            height=0.42,
            stroke_width=3,
            stroke_color=self.theme_token("border_color"),
            fill_opacity=0,
        )
        border_probe.next_to(panel, DOWN, buff=0.16).align_to(panel, LEFT)
        border_text = self.get_text("border", font_size=14, color=self.theme_token("border_color"), weight=BOLD)
        border_text.next_to(border_probe, RIGHT, buff=0.14)
        return VGroup(panel, content, border_probe, border_text)

    def _make_graph_demo(self):
        axes = Axes(
            x_range=[0, 4, 1],
            y_range=[0, 4, 1],
            x_length=3.1,
            y_length=1.65,
            axis_config={
                "color": self.theme_token("grid_or_axis_color"),
                "stroke_width": 2.2,
                "include_tip": False,
            },
        )
        axes.move_to(ORIGIN).shift(LEFT * 0.82 + DOWN * 0.22)
        graph = axes.plot(
            lambda x: 0.38 * (x - 1.2) ** 2 + 0.55,
            x_range=[0.4, 3.6],
            color=self.theme_token("accent_primary"),
            stroke_width=5,
        )
        point = Dot(axes.c2p(2.2, 0.38 * (2.2 - 1.2) ** 2 + 0.55), color=self.theme_token("accent_secondary"), radius=0.08)
        success_tag = self.get_text("success", font_size=14, color=self.theme_token("success_color"), weight=BOLD)
        warning_tag = self.get_text("warning", font_size=14, color=self.theme_token("warning_color"), weight=BOLD)
        success_tag.move_to(axes.get_center()).shift(RIGHT * 1.75 + UP * 0.6)
        warning_tag.move_to(axes.get_center()).shift(RIGHT * 1.78 + DOWN * 0.04)
        graph_title = self.get_text("Graph / Axis Colors", font_size=22, color=self.theme_token("text_main"), weight=BOLD)
        graph_title.move_to(axes.get_top()).shift(UP * 0.5 + RIGHT * 0.58)
        accent_probe = Line(LEFT * 0.28, RIGHT * 0.36, color=self.theme_token("accent_secondary"), stroke_width=6)
        accent_probe.set_cap_style(CapStyleType.ROUND)
        accent_probe.next_to(warning_tag, DOWN, buff=0.24).align_to(warning_tag, LEFT)
        accent_text = self.get_text("secondary", font_size=14, color=self.theme_token("accent_secondary"), weight=BOLD)
        accent_text.next_to(accent_probe, RIGHT, buff=0.12)
        return VGroup(axes, graph, point, graph_title, success_tag, warning_tag, accent_probe, accent_text)


class MistBlueFocusPreview(_ThemePackPreviewBase):
    theme_id = "mist_blue_focus"
    preview_title = "Mist Blue Focus"


class CharcoalBoardPreview(_ThemePackPreviewBase):
    theme_id = "charcoal_board"
    preview_title = "Charcoal Board"


class SoftGlassWhitePreview(_ThemePackPreviewBase):
    theme_id = "soft_glass_white"
    preview_title = "Soft Glass White"


class DeepSpaceBoardPreview(_ThemePackPreviewBase):
    theme_id = "deep_space_board"
    preview_title = "Deep Space Board"
