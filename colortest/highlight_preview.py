from manim import *

from colortest.ai4learning_theme import (
    AI4LearningBaseScene,
    BLUE_100,
    BLUE_300,
    BLUE_500,
    CYAN_400,
    GREEN_300,
    GREEN_500,
    GREY_200,
    GREY_400,
    ORANGE_500,
    PURPLE_400,
    RED_500,
    YELLOW_300,
)


class HighlightPreviewScene(AI4LearningBaseScene):
    def construct(self):
        title = self.get_text("Highlight Preview", font_size=34, weight=BOLD)
        subtitle = self.get_text(
            "Check green vs yellow vs brighter purple on the current background",
            color=GREY_200,
            font_size=20,
        )
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.18)

        chips = VGroup(
            self._make_chip("Ice Blue", CYAN_400),
            self._make_chip("Bright Green", GREEN_300),
            self._make_chip("Bright Yellow", YELLOW_300),
            self._make_chip("Warm Orange", ORANGE_500),
            self._make_chip("Bright Purple", PURPLE_400),
            self._make_chip("Coral Red", RED_500),
        ).arrange(DOWN, buff=0.2, aligned_edge=LEFT)

        formula = VGroup(
            self.get_text("Example:", font_size=24, color=GREY_200),
            self.get_text("F", font_size=30, color=RED_500, weight=BOLD),
            self.get_text("=", font_size=24, color=BLUE_100),
            self.get_text("m", font_size=30, color=GREEN_300, weight=BOLD),
            self.get_text("a", font_size=30, color=YELLOW_300, weight=BOLD),
            self.get_text("and", font_size=24, color=GREY_200),
            self.get_text("dx", font_size=30, color=CYAN_400, weight=BOLD),
            self.get_text("pull first", font_size=24, color=GREY_200),
        ).arrange(RIGHT, buff=0.12, aligned_edge=DOWN)

        focus_box = RoundedRectangle(
            corner_radius=0.22,
            width=4.8,
            height=2.3,
            stroke_color=CYAN_400,
            stroke_width=3,
            fill_opacity=0,
        )
        focus_title = self.get_text("Focus First", font_size=24, color=YELLOW_300, weight=BOLD)
        focus_text = self.get_text("Green and yellow separate much more cleanly", font_size=18, color=BLUE_100)
        focus_tag = self.get_text("Purple now reads as contrast", font_size=18, color=PURPLE_400)
        focus_group = VGroup(focus_title, focus_text, focus_tag).arrange(DOWN, buff=0.14)
        focus_group.move_to(focus_box.get_center())

        node_left = Dot(color=GREEN_300, radius=0.12)
        node_mid = Dot(color=YELLOW_300, radius=0.12)
        node_right = Dot(color=RED_500, radius=0.12)
        node_left.move_to(LEFT * 1.85 + DOWN * 1.15)
        node_mid.move_to(ORIGIN + DOWN * 1.15)
        node_right.move_to(RIGHT * 1.85 + DOWN * 1.15)
        arrow_a = Arrow(node_left.get_right(), node_mid.get_left(), buff=0.14, stroke_width=6, color=ORANGE_500)
        arrow_b = Arrow(node_mid.get_right(), node_right.get_left(), buff=0.14, stroke_width=6, color=ORANGE_500)
        arrow_caption = self.get_text("Warm accents stay visible without replacing yellow", font_size=17, color=PURPLE_400)
        arrow_caption.next_to(arrow_a, DOWN, buff=0.24)
        arrow_caption.align_to(arrow_b, RIGHT)

        demo_panel = VGroup(
            focus_box,
            focus_group,
            node_left,
            node_mid,
            node_right,
            arrow_a,
            arrow_b,
            arrow_caption,
        )

        body = VGroup(chips, demo_panel).arrange(RIGHT, buff=0.9, aligned_edge=UP)
        page = VGroup(header, formula, body).arrange(DOWN, buff=0.34)
        self.fit_group(page, max_width=11.9, max_height=5.9)

        self.play(FadeIn(header, shift=DOWN * 0.2), run_time=0.6)
        self.play(Write(formula), run_time=1.0)
        self.play(
            LaggedStart(*[FadeIn(chip, shift=RIGHT * 0.15) for chip in chips], lag_ratio=0.1),
            run_time=1.2,
        )
        self.play(
            Create(focus_box),
            FadeIn(focus_group, shift=UP * 0.12),
            FadeIn(node_left),
            FadeIn(node_mid),
            FadeIn(node_right),
            GrowArrow(arrow_a),
            GrowArrow(arrow_b),
            FadeIn(arrow_caption, shift=UP * 0.12),
            run_time=1.2,
        )
        self.wait(1.2)

    def _make_chip(self, text, color):
        label = self.get_text(text, font_size=22, color=color, weight=BOLD)
        desc = self.get_text("keyword / formula highlight", font_size=16, color=GREY_200)
        swatch = RoundedRectangle(
            corner_radius=0.16,
            width=0.72,
            height=0.46,
            stroke_width=0,
            fill_color=color,
            fill_opacity=1,
        )
        content = VGroup(label, desc).arrange(DOWN, buff=0.08, aligned_edge=LEFT)
        row = VGroup(swatch, content).arrange(RIGHT, buff=0.22, aligned_edge=UP)
        frame = RoundedRectangle(
            corner_radius=0.18,
            width=row.width + 0.44,
            height=row.height + 0.3,
            stroke_color=color,
            stroke_width=2,
            fill_opacity=0,
        )
        row.move_to(frame.get_center())
        return VGroup(frame, row)


class BodyColorPreviewScene(AI4LearningBaseScene):
    def construct(self):
        title = self.get_text("Body Color Preview", font_size=34, color=BLUE_100, weight=BOLD)
        subtitle = self.get_text(
            "BLUE_100 for main lines, GREY_200 for normal body, GREY_400 only for notes",
            font_size=18,
            color=GREY_200,
        )
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.16)

        panel = RoundedRectangle(
            corner_radius=0.22,
            width=11.0,
            height=4.7,
            stroke_color=CYAN_400,
            stroke_width=2.5,
            fill_opacity=0,
        )

        line_1 = self.get_text("Main takeaway should read bright and clear.", font_size=29, color=BLUE_100)
        line_2 = self.get_text("Normal body text should still be easy to read.", font_size=24, color=GREY_200)
        line_3 = self.get_text("Secondary note stays softer, but should not disappear.", font_size=22, color=GREY_400)
        line_4 = self.get_text("Question or conclusion line can go back to BLUE_100.", font_size=24, color=BLUE_100)
        accent = self.get_text("Muted note sample", font_size=18, color=GREY_400)

        tag_main = self._make_body_chip("BLUE_100", BLUE_100, "main body / conclusion")
        tag_body = self._make_body_chip("GREY_200", GREY_200, "normal body")
        tag_note = self._make_body_chip("GREY_400", GREY_400, "secondary note only")
        chips = VGroup(tag_main, tag_body, tag_note).arrange(RIGHT, buff=0.24)

        content = VGroup(line_1, line_2, line_3, line_4, accent, chips).arrange(
            DOWN,
            buff=0.22,
            aligned_edge=LEFT,
        )
        content.move_to(panel.get_center())
        content.align_to(panel, LEFT).shift(RIGHT * 0.5)

        page = VGroup(header, VGroup(panel, content)).arrange(DOWN, buff=0.28)
        self.fit_group(page, max_width=12.0, max_height=6.0)

        self.play(FadeIn(header, shift=DOWN * 0.15), run_time=0.6)
        self.play(Create(panel), run_time=0.7)
        self.play(FadeIn(line_1, shift=DOWN * 0.12), run_time=0.45)
        self.play(FadeIn(line_2, shift=DOWN * 0.12), run_time=0.45)
        self.play(FadeIn(line_3, shift=DOWN * 0.12), run_time=0.45)
        self.play(FadeIn(line_4, shift=DOWN * 0.12), FadeIn(accent, shift=DOWN * 0.12), run_time=0.55)
        self.play(LaggedStart(*[FadeIn(chip, shift=UP * 0.1) for chip in chips], lag_ratio=0.14), run_time=0.9)
        self.wait(1.0)

    def _make_body_chip(self, text, color, desc):
        label = self.get_text(text, font_size=20, color=color, weight=BOLD)
        detail = self.get_text(desc, font_size=15, color=GREY_200)
        stack = VGroup(label, detail).arrange(DOWN, buff=0.08, aligned_edge=LEFT)
        frame = RoundedRectangle(
            corner_radius=0.16,
            width=stack.width + 0.42,
            height=stack.height + 0.28,
            stroke_color=color,
            stroke_width=2,
            fill_opacity=0,
        )
        stack.move_to(frame.get_center())
        return VGroup(frame, stack)


class ShapeStructurePreviewScene(AI4LearningBaseScene):
    def construct(self):
        title = self.get_text("Shape Structure Preview", font_size=34, color=BLUE_100, weight=BOLD)
        subtitle = self.get_text(
            "These colors are for borders, arrows, nodes, and diagram structure, not main body text",
            font_size=18,
            color=GREY_200,
        )
        header = VGroup(title, subtitle).arrange(DOWN, buff=0.16)

        chips = VGroup(
            self._make_structure_chip("BLUE_300", BLUE_300, "soft border / panel"),
            self._make_structure_chip("BLUE_500", BLUE_500, "main frame"),
            self._make_structure_chip("CYAN_400", CYAN_400, "flow arrow"),
            self._make_structure_chip("GREEN_500", GREEN_500, "positive branch"),
            self._make_structure_chip("PURPLE_400", PURPLE_400, "secondary node"),
            self._make_structure_chip("ORANGE_500", ORANGE_500, "attention path"),
            self._make_structure_chip("RED_500", RED_500, "warning edge"),
        ).arrange(DOWN, buff=0.16, aligned_edge=LEFT)

        left_box = RoundedRectangle(
            corner_radius=0.18,
            width=2.2,
            height=1.3,
            stroke_color=BLUE_300,
            stroke_width=2.5,
            fill_opacity=0,
        )
        right_box = RoundedRectangle(
            corner_radius=0.18,
            width=2.5,
            height=1.45,
            stroke_color=BLUE_500,
            stroke_width=3,
            fill_opacity=0,
        )
        left_label = self.get_text("Input", font_size=22, color=BLUE_100)
        right_label = self.get_text("Result", font_size=22, color=BLUE_100)
        left_label.move_to(left_box.get_center())
        right_label.move_to(right_box.get_center())
        left_group = VGroup(left_box, left_label).move_to(LEFT * 1.9 + UP * 1.0)
        right_group = VGroup(right_box, right_label).move_to(RIGHT * 1.95 + UP * 1.0)

        node_a = Dot(color=GREEN_500, radius=0.12).move_to(LEFT * 2.1 + DOWN * 0.65)
        node_b = Dot(color=PURPLE_400, radius=0.12).move_to(ORIGIN + DOWN * 0.65)
        node_c = Dot(color=RED_500, radius=0.12).move_to(RIGHT * 2.1 + DOWN * 0.65)

        arrow_top = Arrow(left_group.get_right(), right_group.get_left(), buff=0.16, color=CYAN_400, stroke_width=6)
        arrow_bottom_a = Arrow(node_a.get_right(), node_b.get_left(), buff=0.14, color=ORANGE_500, stroke_width=6)
        arrow_bottom_b = Arrow(node_b.get_right(), node_c.get_left(), buff=0.14, color=RED_500, stroke_width=6)

        tag_a = self.get_text("stable node", font_size=16, color=GREEN_500).next_to(node_a, DOWN, buff=0.14)
        tag_b = self.get_text("choice", font_size=16, color=PURPLE_400).next_to(node_b, DOWN, buff=0.14)
        tag_c = self.get_text("risk", font_size=16, color=RED_500).next_to(node_c, DOWN, buff=0.14)

        demo = VGroup(
            left_group,
            right_group,
            node_a,
            node_b,
            node_c,
            arrow_top,
            arrow_bottom_a,
            arrow_bottom_b,
            tag_a,
            tag_b,
            tag_c,
        )
        panel = RoundedRectangle(
            corner_radius=0.22,
            width=5.9,
            height=4.6,
            stroke_color=CYAN_400,
            stroke_width=2.4,
            fill_opacity=0,
        )
        demo_panel = VGroup(panel, demo)
        demo.move_to(panel.get_center())

        page = VGroup(header, VGroup(chips, demo_panel).arrange(RIGHT, buff=0.7, aligned_edge=UP)).arrange(
            DOWN,
            buff=0.28,
        )
        self.fit_group(page, max_width=12.0, max_height=6.0)

        self.play(FadeIn(header, shift=DOWN * 0.15), run_time=0.6)
        self.play(LaggedStart(*[FadeIn(chip, shift=RIGHT * 0.1) for chip in chips], lag_ratio=0.1), run_time=1.0)
        self.play(Create(panel), run_time=0.7)
        self.play(FadeIn(left_group, shift=DOWN * 0.1), FadeIn(right_group, shift=DOWN * 0.1), run_time=0.6)
        self.play(GrowArrow(arrow_top), run_time=0.55)
        self.play(FadeIn(node_a), FadeIn(node_b), FadeIn(node_c), run_time=0.4)
        self.play(GrowArrow(arrow_bottom_a), GrowArrow(arrow_bottom_b), FadeIn(tag_a), FadeIn(tag_b), FadeIn(tag_c), run_time=0.8)
        self.wait(1.0)

    def _make_structure_chip(self, name, color, desc):
        swatch = RoundedRectangle(
            corner_radius=0.14,
            width=0.7,
            height=0.44,
            stroke_color=color,
            stroke_width=2.5,
            fill_opacity=0,
        )
        label = self.get_text(name, font_size=19, color=color, weight=BOLD)
        detail = self.get_text(desc, font_size=15, color=GREY_200)
        text_block = VGroup(label, detail).arrange(DOWN, buff=0.06, aligned_edge=LEFT)
        row = VGroup(swatch, text_block).arrange(RIGHT, buff=0.18, aligned_edge=UP)
        frame = RoundedRectangle(
            corner_radius=0.16,
            width=row.width + 0.34,
            height=row.height + 0.24,
            stroke_color=color,
            stroke_width=2,
            fill_opacity=0,
        )
        row.move_to(frame.get_center())
        return VGroup(frame, row)
