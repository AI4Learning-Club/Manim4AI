# Label Skill

Use inside `LessonBase` section methods when points, variables, graph parts, formula terms, or diagram states need readable names.

## When to use

- Use symbolic local labels for points, axes, vectors, states, and tiny identifiers.
- Put explanatory labels and full sentences in a body-band block or panel.
- Use label builders when a label must stay attached to a dot, node, axis point, or moving anchor.

## Aesthetic rule

Keep local labels sparse. Prefer two or three high-value labels over a fully annotated diagram. Use `get_secondary_text`, `get_math`, or `get_muted_text` and no hardcoded colors.

## Safety rule

Only symbolic labels may use `next_to` near graphics. Persistent explanatory text must be planned inside the page's fitted `bodyN` before reveal.

## Anti-patterns

- Do not put paragraph labels inside graph regions.
- Do not stack multiple labels on the same side of a small anchor.
- Do not create a takeaway label after `fit_body` and attach it to `bodyN`.
- Do not use `Text` for variables with subscripts; use `get_math`.

## Snippet

```python
def build_local_label_on_dot(self, dot, label_text, direction=UP + RIGHT):
    label = self.get_secondary_text(label_text, font_size=18)
    label.next_to(dot, direction, buff=0.08)
    return label


def section_label_example(self):
    dot_a = Dot(LEFT * 1.6, color=self.get_accent_color("primary"))
    dot_b = Dot(RIGHT * 1.6, color=self.get_accent_color("secondary"))
    segment = Line(dot_a.get_center(), dot_b.get_center(), color=self.get_axis_color())
    diagram = Group(segment, dot_a, dot_b)
    label_a = self.build_on_anchor("build_local_label_on_dot", dot_a, "A", UP + LEFT)
    label_b = self.build_on_anchor("build_local_label_on_dot", dot_b, "B", UP + RIGHT)
    note = self.make_panel(
        self.get_text("The distance is read from endpoints A and B.", font_size=22),
        padding=0.18,
    )
    body1 = Group(Group(diagram, label_a, label_b), note).arrange(DOWN, buff=0.35)
    self.fit_body(body1, max_width=10.8, center=UP * 0.1)
    self.play(Create(segment), FadeIn(dot_a), FadeIn(dot_b), run_time=0.5)
    self.play(FadeIn(label_a), FadeIn(label_b), FadeIn(note), run_time=0.5)
```
