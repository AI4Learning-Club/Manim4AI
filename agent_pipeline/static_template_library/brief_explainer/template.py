from manim import *
from plugins.manim.colortest.ai4learning_theme import AI4LearningBaseScene

SCENE_MANIFEST = [
    {"id": "opening", "scene": "Segment00OpeningScene", "method": "opening_page"},
    {"id": "section_one_big_picture", "scene": "Segment01SectionOneBigPictureScene", "method": "section_one_big_picture"},
    {"id": "section_two_key_terms", "scene": "Segment02SectionTwoKeyTermsScene", "method": "section_two_key_terms"},
    {"id": "section_three_main_example", "scene": "Segment03SectionThreeMainExampleScene", "method": "section_three_main_example"},
    {"id": "section_four_common_confusion", "scene": "Segment04SectionFourCommonConfusionScene", "method": "section_four_common_confusion"},
    {"id": "section_five_one_sentence", "scene": "Segment05SectionFiveOneSentenceScene", "method": "section_five_one_sentence"},
    {"id": "closing", "scene": "Segment06ClosingScene", "method": "closing_page"},
]

class LessonBase(AI4LearningBaseScene):
    theme_id = "mist_blue_focus"

    def opening_page(self):
        title = self.get_text("简略讲解", font_size=30)
        hook = self.get_warning_text("如果只剩几分钟，怎样讲才能让学生先抓住大意？", font_size=22)
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

    def section_one_big_picture(self):
        title = self.get_text("先给全景", font_size=28)
        narrative = self.get_text("先把整件事的大轮廓抛出来。", font_size=20)
        visual = self.get_secondary_text("视觉重点：总览图、主线、结论先行", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "先给全景",
                    "总览图、主线、结论先行",
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

    def section_two_key_terms(self):
        title = self.get_text("只保留关键词", font_size=28)
        narrative = self.get_text("只讲最关键的词和关系，不展开旁枝。", font_size=20)
        visual = self.get_secondary_text("视觉重点：关键词、箭头关系", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "只保留关键词",
                    "关键词、箭头关系",
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

    def section_three_main_example(self):
        title = self.get_text("给一个代表性例子", font_size=28)
        narrative = self.get_text("用一个极小例子承接整体印象。", font_size=20)
        visual = self.get_secondary_text("视觉重点：一个例子、一条主线", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "给一个代表性例子",
                    "一个例子、一条主线",
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

    def section_four_common_confusion(self):
        title = self.get_text("顺手排一个误区", font_size=28)
        narrative = self.get_text("快速指出最常见误解，避免学生带错印象离开。", font_size=20)
        visual = self.get_secondary_text("视觉重点：误区对比", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "顺手排一个误区",
                    "误区对比",
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

    def section_five_one_sentence(self):
        title = self.get_text("一句话收口", font_size=28)
        narrative = self.get_text("最后收成一句能带走的话。", font_size=20)
        visual = self.get_secondary_text("视觉重点：一句话总结", font_size=18)
        bullets = VGroup(
            *[
                self.get_text(text, font_size=18)
                for text in [
                    "一句话收口",
                    "一句话总结",
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
        summary = self.get_text("简略讲解不是少讲，而是只保留最能支撑理解的骨架。", font_size=22)
        transfer = self.get_secondary_text("迁移问题：如果换一道新题或新概念，你还会沿用“简略讲解”这套理解路径吗？", font_size=18)
        after_class = self.get_secondary_text("课后练习：课后请回忆“简略讲解”最有效的两个讲解动作，并尝试迁移到别的主题。", font_size=18)
        body1 = VGroup(summary, transfer, after_class).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
        panel = self.make_panel(body1, padding=0.18)
        body2 = VGroup(panel)
        self.fit_body(body2, max_width=11.6, center=ORIGIN)
        self.add(body2)
        self.wait(0.1)

class Segment00OpeningScene(LessonBase):
    def construct(self):
        self.opening_page()

class Segment01SectionOneBigPictureScene(LessonBase):
    def construct(self):
        self.section_one_big_picture()

class Segment02SectionTwoKeyTermsScene(LessonBase):
    def construct(self):
        self.section_two_key_terms()

class Segment03SectionThreeMainExampleScene(LessonBase):
    def construct(self):
        self.section_three_main_example()

class Segment04SectionFourCommonConfusionScene(LessonBase):
    def construct(self):
        self.section_four_common_confusion()

class Segment05SectionFiveOneSentenceScene(LessonBase):
    def construct(self):
        self.section_five_one_sentence()

class Segment06ClosingScene(LessonBase):
    def construct(self):
        self.closing_page()
