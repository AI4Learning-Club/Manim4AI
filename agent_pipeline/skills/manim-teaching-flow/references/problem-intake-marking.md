# Problem Intake / Marking Skill

> Restored verbatim from the former math-to-manim visual-pattern reference.

# Problem Intake / Marking Skill

Use inside `LessonBase` section methods for problem-solving openings: restate the task, separate givens from target, mark key variables or diagram relations, then move into the solution.

## When to use

- Use for exercises, proofs, calculations, geometry questions, and image-based problems.
- Use a compact reconstructed problem card instead of copying a long prompt.
- Mark givens, target, variables, and required relation before any derivation.
- Turn `problem_intake.visual_marking_plan` into circles, underlines, boxes, arrows, or color emphasis.

## Aesthetic rule

The opening should feel like a teacher reading the problem with a pen in hand. Keep markings sequential and purposeful. Use theme helpers and no hardcoded colors.

## Safety rule

The problem card, mark objects, and short labels belong inside the same fitted `bodyN`. Do not add late sentence-like labels outside `bodyN` after `fit_body`.

## Anti-patterns

- Do not begin with a strategy slogan before the problem is restated.
- Do not paste the entire long prompt into one tiny card.
- Do not mark everything at once.
- Do not put derivation formulas on the intake card.

## Snippet

```python
def build_mark_box_on_object(self, target):
    return SurroundingRectangle(
        target,
        buff=0.06,
        corner_radius=0.06,
        stroke_width=3,
        color=self.get_formula_highlight_color("primary"),
        fill_opacity=0,
    )


def section_problem_intake_example(self):
    title = self.make_page_title("先读题：找已知、目标和变量", font_size=30)
    given = self.get_text("已知：函数图像经过点 P", font_size=22)
    target = self.get_text("目标：求 P 点的瞬时变化率", font_size=22)
    variable = self.get_math(r"x_0,\quad f'(x_0)", font_size=28)
    card_content = Group(given, target, variable).arrange(DOWN, buff=0.16, aligned_edge=LEFT)
    card = self.make_panel(card_content, padding=0.22)
    given_box = self.build_on_anchor("build_mark_box_on_object", given)
    target_box = self.build_on_anchor("build_mark_box_on_object", target)
    variable_box = self.build_on_anchor("build_mark_box_on_object", variable)
    prompt = self.make_panel(
        self.get_secondary_text("先标清楚，再进入图像推理。", font_size=20),
        padding=0.16,
    )
    body1 = Group(Group(card, given_box, target_box, variable_box), prompt).arrange(DOWN, buff=0.28)
    self.fit_body(body1, max_width=10.8, center=UP * 0.1)
    self.add(title)
    self.speak_with_subtitle("先把题目压缩成三件事。", FadeIn(card), run_time=0.7)
    self.speak_with_subtitle("这一行是已知条件。", Create(given_box), run_time=0.55)
    self.speak_with_subtitle("这一行是目标问题。", Create(target_box), run_time=0.55)
    self.speak_with_subtitle("最后圈出关键符号。", Create(variable_box), FadeIn(prompt), run_time=0.7)
```
