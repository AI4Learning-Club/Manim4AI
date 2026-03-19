from __future__ import annotations

from manim import *

from colortest.ai4learning_theme import AI4LearningBaseScene


class FontPreviewScene(AI4LearningBaseScene):
    theme_id = "deep_space_board"

    def construct(self):
        title = self.get_text("字体预览：中文与英文自动分流", font_size=34, weight=BOLD)
        intro = self.get_secondary_text(
            "中文默认 Noto Serif SC，纯英文默认 Times New Roman",
            font_size=20,
        )

        chinese_block = VGroup(
            self.get_text("中文标题：宏观经济学的基本概念", font_size=30, weight=BOLD),
            self.get_secondary_text("总量、就业、价格、增长之间彼此联动。", font_size=22),
        ).arrange(DOWN, buff=0.12, aligned_edge=LEFT)

        english_block = VGroup(
            self.get_text("English Title: Inflation, GDP, and Employment", font_size=28, weight=BOLD),
            self.get_secondary_text("Pure English labels should now fall back to Times New Roman.", font_size=20),
        ).arrange(DOWN, buff=0.12, aligned_edge=LEFT)

        mixed_block = VGroup(
            self.get_text("混排示例：GDP growth rate 在 2026 年回到 3.2%", font_size=26),
            self.get_warning_text("数字与英文会保留在同一行里，方便看混排效果。", font_size=20),
        ).arrange(DOWN, buff=0.12, aligned_edge=LEFT)

        panel_note = self.make_panel(
            self.get_text("面板内文字也走同样的字体策略。", font_size=22),
            padding=0.22,
        )

        subtitle = self.make_subtitle_panel("字幕预览：这里也会跟随新的字体策略。", font_size=18)

        page = Group(
            VGroup(title, intro).arrange(DOWN, buff=0.14, aligned_edge=LEFT),
            chinese_block,
            english_block,
            mixed_block,
            panel_note,
        ).arrange(DOWN, buff=0.28, aligned_edge=LEFT)

        self.fit_group(page, max_width=12.0, max_height=5.8)

        self.play(FadeIn(page, shift=DOWN * 0.15), run_time=0.8)
        self.play(FadeIn(subtitle, shift=UP * 0.06), run_time=0.4)
        self.wait(1.0)
