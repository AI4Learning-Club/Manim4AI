from manim import *

from plugins.manim.colortest.ai4learning_theme import AI4LearningBaseScene

SCENE_MANIFEST = [
    {"id": "opening", "scene": "Segment00OpeningScene", "method": "opening_page"},
    {"id": "section_one_object_goal", "scene": "Segment01SectionOneObjectGoalScene", "method": "section_one_object_goal"},
    {"id": "section_two_core_relation", "scene": "Segment02SectionTwoCoreRelationScene", "method": "section_two_core_relation"},
    {"id": "section_three_dynamic_walkthrough", "scene": "Segment03SectionThreeDynamicWalkthroughScene", "method": "section_three_dynamic_walkthrough"},
    {"id": "section_four_misconception_check", "scene": "Segment04SectionFourMisconceptionCheckScene", "method": "section_four_misconception_check"},
    {"id": "section_five_transfer_summary", "scene": "Segment05SectionFiveTransferSummaryScene", "method": "section_five_transfer_summary"},
    {"id": "closing", "scene": "Segment06ClosingScene", "method": "closing_page"},
]

class LessonBase(AI4LearningBaseScene):
    theme_id = "mist_blue_focus"

    def opening_page(self):
        title = self.get_text("时间线讲解", font_size=30)
        hook = self.get_warning_text("如果内容没变，只是讲法换了，理解体验会差多少？", font_size=22)
        roadmap = VGroup(
            *[
                self.get_secondary_text(text, font_size=18)
                for text in [
                    "1. 看到对象和目标",
                    "2. 绑定图像与关系",
                    "3. 走完一次关键过程",
                ]
            ]
        ).arrange(DOWN, buff=0.12, aligned_edge=LEFT)
        body1 = VGroup(title, hook, roadmap).arrange(DOWN, buff=0.22, aligned_edge=LEFT)
        panel = self.make_panel(body1, padding=0.2)
        body2 = VGroup(panel)
        self.fit_body(body2, max_width=11.6, center=ORIGIN)
        self.add(body2)
        self.wait(0.1)

    def section_one_object_goal(self):
        title = self.get_text("对象和目标先入场", font_size=28)
        narrative = self.get_text("先把本节真正要理解的对象、起点和目标放到同一张图里。", font_size=20)
        visual = self.get_secondary_text("视觉重点：对象标注、起始状态、目标箭头", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "对象和目标先入场",
                    "对象标注、起始状态、目标箭头",
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

    def section_two_core_relation(self):
        title = self.get_text("核心关系绑到图像", font_size=28)
        narrative = self.get_text("把关键量之间的关系和画面对象一一对应起来。", font_size=20)
        visual = self.get_secondary_text("视觉重点：图像-公式绑定、颜色高亮", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "核心关系绑到图像",
                    "图像-公式绑定、颜色高亮",
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

    def section_three_dynamic_walkthrough(self):
        title = self.get_text("走完一次关键过程", font_size=28)
        narrative = self.get_text("用一次完整动态演示串起状态变化和因果链。", font_size=20)
        visual = self.get_secondary_text("视觉重点：步骤推进、状态更新、轨迹变化", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "走完一次关键过程",
                    "步骤推进、状态更新、轨迹变化",
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

    def section_four_misconception_check(self):
        title = self.get_text("校正常见误解", font_size=28)
        narrative = self.get_text("把最容易混淆的方向、条件或边界并排对比。", font_size=20)
        visual = self.get_secondary_text("视觉重点：正误对照、边界提醒、反馈回路", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "校正常见误解",
                    "正误对照、边界提醒、反馈回路",
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

    def section_five_transfer_summary(self):
        title = self.get_text("迁移成可复用方法", font_size=28)
        narrative = self.get_text("把本节具体画面压缩成学生能带走的通用步骤。", font_size=20)
        visual = self.get_secondary_text("视觉重点：方法卡、迁移例子、收束句", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "迁移成可复用方法",
                    "方法卡、迁移例子、收束句",
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
        summary = self.get_text("时间线讲解的核心不是多讲内容，而是用合适的讲法把理解路径变短。", font_size=22)
        transfer = self.get_secondary_text("迁移问题：如果换一道新题或新概念，你还会沿用“时间线讲解”这套理解路径吗？", font_size=18)
        after_class = self.get_secondary_text("课后练习：课后请回忆“时间线讲解”最有效的两个讲解动作，并尝试迁移到别的主题。", font_size=18)
        body1 = VGroup(summary, transfer, after_class).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(body1, padding=0.18)
        body2 = VGroup(panel)
        self.fit_body(body2, max_width=11.6, center=ORIGIN)
        self.add(body2)
        self.wait(0.1)

class Segment00OpeningScene(LessonBase):
    def construct(self):
        self.opening_page()

class Segment01SectionOneObjectGoalScene(LessonBase):
    def construct(self):
        self.section_one_object_goal()

class Segment02SectionTwoCoreRelationScene(LessonBase):
    def construct(self):
        self.section_two_core_relation()

class Segment03SectionThreeDynamicWalkthroughScene(LessonBase):
    def construct(self):
        self.section_three_dynamic_walkthrough()

class Segment04SectionFourMisconceptionCheckScene(LessonBase):
    def construct(self):
        self.section_four_misconception_check()

class Segment05SectionFiveTransferSummaryScene(LessonBase):
    def construct(self):
        self.section_five_transfer_summary()

class Segment06ClosingScene(LessonBase):
    def construct(self):
        self.closing_page()
