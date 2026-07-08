# Arrow Skill

> Restored verbatim from the former math-to-manim visual-pattern reference.

# Arrow Skill

Use inside `LessonBase(AI4LearningBaseScene)` when a student must see direction, dependency, flow, feedback, or a formula-to-object connection.

## When to use

- Use straight arrows for local cause/effect and left-to-right flow.
- Use curved arrows only when a straight arrow would cross important content.
- Use feedback arrows for loops, correction, or "try again with the new state".
- Use formula-explanation arrows only from a visible formula term to its visible object.

## Aesthetic rule

Keep arrows short, anchored, and semantic. One arrow should answer one visual question. Prefer theme helpers and no hardcoded colors.

## Safety rule

Build persistent arrows inside the same fitted `bodyN` as their anchors or through `build_on_anchor`. Do not place a sentence label with `next_to` after `fit_body`.

## Anti-patterns

- Do not use arrows as decoration.
- Do not draw long cross-page arrows over graphs or text.
- Do not `GrowArrow` a `CurvedArrow`; use `Create` for curved/path arrows.
- Do not rebuild a second off-screen layout after `fit_body` just to get arrow endpoints.

## Snippet

```python
def build_connector_on_objects(self, source, target):
    return Arrow(
        source.get_right(),
        target.get_left(),
        buff=0.08,
        stroke_width=3,
        color=self.get_accent_color("primary"),
        max_tip_length_to_length_ratio=0.16,
    )


def section_arrow_flow_example(self):
    start = self.make_panel(self.get_text("Known", font_size=22), padding=0.18)
    rule = self.make_panel(self.get_math(r"f'(x)>0", font_size=28), padding=0.18)
    result = self.make_panel(self.get_success_text("Increasing", font_size=22), padding=0.18)
    row = Group(start, rule, result).arrange(RIGHT, buff=0.7)
    arrow_a = self.build_on_anchor("build_connector_on_objects", start, rule)
    arrow_b = self.build_on_anchor("build_connector_on_objects", rule, result)
    body1 = Group(row, arrow_a, arrow_b)
    self.fit_body(body1, max_width=11.6, center=UP * 0.1)
    self.play(FadeIn(start), run_time=0.3)
    self.play(GrowArrow(arrow_a), FadeIn(rule), run_time=0.5)
    self.play(GrowArrow(arrow_b), FadeIn(result), run_time=0.5)
```
