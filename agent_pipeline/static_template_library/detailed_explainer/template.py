from manim import *

from plugins.manim.colortest.ai4learning_theme import AI4LearningBaseScene

SCENE_MANIFEST = [
    {"id": "opening", "scene": "Segment00OpeningScene", "method": "opening_page"},
    {"id": "section_one_context", "scene": "Segment01SectionOneContextScene", "method": "section_one_context"},
    {"id": "section_two_define", "scene": "Segment02SectionTwoDefineScene", "method": "section_two_define"},
    {"id": "section_three_reasoning", "scene": "Segment03SectionThreeReasoningScene", "method": "section_three_reasoning"},
    {"id": "section_four_edge_cases", "scene": "Segment04SectionFourEdgeCasesScene", "method": "section_four_edge_cases"},
    {"id": "section_five_reconstruct", "scene": "Segment05SectionFiveReconstructScene", "method": "section_five_reconstruct"},
    {"id": "closing", "scene": "Segment06ClosingScene", "method": "closing_page"},
]

class LessonBase(AI4LearningBaseScene):
    theme_id = "mist_blue_focus"

    def opening_page(self):
        title = self.get_text("详细讲解", font_size=30)
        hook = self.get_warning_text("为什么有些内容必须讲细，学生才不会只记住表面？", font_size=22)
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

    def section_one_context(self):
        title = self.get_text("补背景", font_size=28)
        narrative = self.get_text("先把学生缺失的上下文补足。", font_size=20)
        visual = self.get_secondary_text("视觉重点：前置知识、边界条件", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "补背景",
                    "前置知识、边界条件",
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

    def section_two_define(self):
        title = self.get_text("把定义讲透", font_size=28)
        narrative = self.get_text("概念、条件、对象都逐一定清楚。", font_size=20)
        visual = self.get_secondary_text("视觉重点：定义卡、限制条件", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "把定义讲透",
                    "定义卡、限制条件",
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

    def section_three_reasoning(self):
        title = self.get_text("展示推理链", font_size=28)
        narrative = self.get_text("不给跳步，把为什么成立一路说清。", font_size=20)
        visual = self.get_secondary_text("视觉重点：推理链、阶段节点", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "展示推理链",
                    "推理链、阶段节点",
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

    def section_four_edge_cases(self):
        title = self.get_text("补边界与反例", font_size=28)
        narrative = self.get_text("专门处理细节、反例、易错点。", font_size=20)
        visual = self.get_secondary_text("视觉重点：反例、边界情况", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "补边界与反例",
                    "反例、边界情况",
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

    def section_five_reconstruct(self):
        title = self.get_text("让学生重建一遍", font_size=28)
        narrative = self.get_text("收尾时让学生能自己再说一遍。", font_size=20)
        visual = self.get_secondary_text("视觉重点：重构图、复述框架", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "让学生重建一遍",
                    "重构图、复述框架",
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
        summary = self.get_text("详细讲解的价值在于把隐含台阶全部点亮，让学生不靠猜。", font_size=22)
        transfer = self.get_secondary_text("迁移问题：如果换一道新题或新概念，你还会沿用“详细讲解”这套理解路径吗？", font_size=18)
        after_class = self.get_secondary_text("课后练习：课后请回忆“详细讲解”最有效的两个讲解动作，并尝试迁移到别的主题。", font_size=18)
        body1 = VGroup(summary, transfer, after_class).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(body1, padding=0.18)
        body2 = VGroup(panel)
        self.fit_body(body2, max_width=11.6, center=ORIGIN)
        self.add(body2)
        self.wait(0.1)

class Segment00OpeningScene(LessonBase):
    def construct(self):
        self.opening_page()

class Segment01SectionOneContextScene(LessonBase):
    def construct(self):
        self.section_one_context()

class Segment02SectionTwoDefineScene(LessonBase):
    def construct(self):
        self.section_two_define()

class Segment03SectionThreeReasoningScene(LessonBase):
    def construct(self):
        self.section_three_reasoning()

class Segment04SectionFourEdgeCasesScene(LessonBase):
    def construct(self):
        self.section_four_edge_cases()

class Segment05SectionFiveReconstructScene(LessonBase):
    def construct(self):
        self.section_five_reconstruct()

class Segment06ClosingScene(LessonBase):
    def construct(self):
        self.closing_page()
