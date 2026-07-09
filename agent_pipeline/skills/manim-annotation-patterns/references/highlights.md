# Highlight Skill

Use inside `LessonBase` section methods when attention must move to a current term, region, step, object, or misconception.

## When to use

- Use `Circumscribe` for a brief focus pulse on an existing object.
- Use `Indicate` for a quick current-step emphasis.
- Use `SurroundingRectangle` or underline only when the highlight should persist.
- Use `highlight_formula_parts` for term-level formula coloring.

## Aesthetic rule

Highlight less than the viewer expects. One active focus at a time feels polished. Use theme helpers and no hardcoded colors.

## Safety rule

Persistent highlight boxes, underlines, and masks belong inside the same fitted `bodyN` as the object they emphasize, or must be rebuilt from an anchor with `build_on_anchor`.

## Anti-patterns

- Do not leave many glowing boxes on screen.
- Do not highlight an object before narration reaches it.
- Do not use filled rectangles that hide graph or text details.
- Do not hardcode yellow, white, or black.

## Snippet

```python
def build_focus_box_on_object(self, target):
    return SurroundingRectangle(
        target,
        buff=0.08,
        corner_radius=0.08,
        stroke_width=3,
        color=self.get_formula_highlight_color("primary"),
        fill_opacity=0,
    )


def section_highlight_example(self):
    formula = self.get_math(r"a^2+b^2=c^2", font_size=36)
    self.highlight_formula_parts(formula, primary=("c^2",), secondary=("a^2", "b^2"))
    note = self.make_panel(self.get_text("The result term is the target.", font_size=22), padding=0.18)
    body1 = Group(formula, note).arrange(DOWN, buff=0.35)
    focus = self.build_on_anchor("build_focus_box_on_object", formula)
    body1.add(focus)
    self.fit_body(body1, max_width=10.6, center=UP * 0.1)
    self.play(Write(formula), run_time=0.5)
    self.play(Create(focus), Circumscribe(formula), FadeIn(note), run_time=0.8)
```
