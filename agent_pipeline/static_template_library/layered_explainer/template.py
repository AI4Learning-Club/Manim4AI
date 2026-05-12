from manim import *
from plugins.manim.colortest.ai4learning_theme import AI4LearningBaseScene

SCENE_MANIFEST = [
    {"id": "opening", "scene": "Segment00OpeningScene", "method": "opening_page"},
    {"id": "section_one_position", "scene": "Segment01SectionOnePositionScene", "method": "section_one_position"},
    {"id": "section_two_structure", "scene": "Segment02SectionTwoStructureScene", "method": "section_two_structure"},
    {"id": "section_three_execution", "scene": "Segment03SectionThreeExecutionScene", "method": "section_three_execution"},
    {"id": "section_four_feedback", "scene": "Segment04SectionFourFeedbackScene", "method": "section_four_feedback"},
    {"id": "section_five_transfer", "scene": "Segment05SectionFiveTransferScene", "method": "section_five_transfer"},
    {"id": "closing", "scene": "Segment06ClosingScene", "method": "closing_page"},
]

class LessonBase(AI4LearningBaseScene):
    theme_id = "mist_blue_focus"

    def opening_page(self):
        title = self.get_text("分层递进讲解", font_size=30)
        hook = self.get_warning_text("如果内容没变，只是讲法换了，理解体验会差多少？", font_size=22)
        roadmap = VGroup(
            *[
                self.get_secondary_text(text, font_size=18)
                for text in [
                    "1. 明确讲法目标",
                    "2. 按这种节奏推进",
                    "3. 最后做迁移收束",
                ]
            ]
        ).arrange(DOWN, buff=0.12, aligned_edge=LEFT)
        body1 = VGroup(title, hook, roadmap).arrange(DOWN, buff=0.22, aligned_edge=LEFT)
        panel = self.make_panel(body1, padding=0.2)
        body2 = VGroup(panel)
        self.fit_body(body2, max_width=11.6, center=ORIGIN)
        self.add(body2)
        self.wait(0.1)

    def section_one_position(self):
        title = self.get_text("先给出讲法定位", font_size=28)
        narrative = self.get_text("先告诉学生这次为什么采用这种讲法。", font_size=20)
        visual = self.get_secondary_text("视觉重点：风格标签、目标、适用场景", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "先给出讲法定位",
                    "风格标签、目标、适用场景",
                    "关键句",
                    "迁移提醒",
                ]
            ]
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)
        card = VGroup(title, narrative, visual, bullets).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(card, padding=0.18)
        body1 = VGroup(panel)
        self.fit_body(body1, max_width=11.6, center=ORIGIN)
        self.add(body1)
        self.wait(0.1)

    def section_two_structure(self):
        title = self.get_text("再固定讲解结构", font_size=28)
        narrative = self.get_text("��这种讲法的结构骨架明确下来。", font_size=20)
        visual = self.get_secondary_text("视觉重点：结构框架、节奏分层", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "再固定讲解结构",
                    "结构框架、节奏分层",
                    "关键句",
                    "迁移提醒",
                ]
            ]
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)
        card = VGroup(title, narrative, visual, bullets).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(card, padding=0.18)
        body1 = VGroup(panel)
        self.fit_body(body1, max_width=11.6, center=ORIGIN)
        self.add(body1)
        self.wait(0.1)

    def section_three_execution(self):
        title = self.get_text("执行这套讲法", font_size=28)
        narrative = self.get_text("展示真正推进内容时，这种讲法如何发力。", font_size=20)
        visual = self.get_secondary_text("视觉重点：步骤推进、重点切换", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "执行这套讲法",
                    "步骤推进、重点切换",
                    "关键句",
                    "迁移提醒",
                ]
            ]
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)
        card = VGroup(title, narrative, visual, bullets).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(card, padding=0.18)
        body1 = VGroup(panel)
        self.fit_body(body1, max_width=11.6, center=ORIGIN)
        self.add(body1)
        self.wait(0.1)

    def section_four_feedback(self):
        title = self.get_text("用反馈校正", font_size=28)
        narrative = self.get_text("强调这种讲法如何处理学生常见卡点。", font_size=20)
        visual = self.get_secondary_text("视觉重点：误区提醒、反馈回路", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "用反馈校正",
                    "误区提醒、反馈回路",
                    "关键句",
                    "迁移提醒",
                ]
            ]
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)
        card = VGroup(title, narrative, visual, bullets).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(card, padding=0.18)
        body1 = VGroup(panel)
        self.fit_body(body1, max_width=11.6, center=ORIGIN)
        self.add(body1)
        self.wait(0.1)

    def section_five_transfer(self):
        title = self.get_text("最后做迁移", font_size=28)
        narrative = self.get_text("把这种讲法迁移到别的内容场景。", font_size=20)
        visual = self.get_secondary_text("视觉重点：迁移问题、复用提醒", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "最后做迁移",
                    "迁移问题、复用提醒",
                    "关键句",
                    "迁移提醒",
                ]
            ]
        ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)
        card = VGroup(title, narrative, visual, bullets).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(card, padding=0.18)
        body1 = VGroup(panel)
        self.fit_body(body1, max_width=11.6, center=ORIGIN)
        self.add(body1)
        self.wait(0.1)

    def closing_page(self):
        summary = self.get_text("分层递进讲解的核心不是多讲内容，而是用合适的讲法把理解路径变短。", font_size=22)
        transfer = self.get_secondary_text("迁移问题：如果换一道新题或新概念，你还会沿用“分层递进讲解”这套理解路径吗？", font_size=18)
        after_class = self.get_secondary_text("课后练习：课后请回忆“分层递进讲解”最有效的两个讲解动作，并尝试迁移到别的主题。", font_size=18)
        body1 = VGroup(summary, transfer, after_class).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(body1, padding=0.18)
        body2 = VGroup(panel)
        self.fit_body(body2, max_width=11.6, center=ORIGIN)
        self.add(body2)
        self.wait(0.1)

class Segment00OpeningScene(LessonBase):
    def construct(self):
        self.opening_page()

class Segment01SectionOnePositionScene(LessonBase):
    def construct(self):
        self.section_one_position()

class Segment02SectionTwoStructureScene(LessonBase):
    def construct(self):
        self.section_two_structure()

class Segment03SectionThreeExecutionScene(LessonBase):
    def construct(self):
        self.section_three_execution()

class Segment04SectionFourFeedbackScene(LessonBase):
    def construct(self):
        self.section_four_feedback()

class Segment05SectionFiveTransferScene(LessonBase):
    def construct(self):
        self.section_five_transfer()

class Segment06ClosingScene(LessonBase):
    def construct(self):
        self.closing_page()
