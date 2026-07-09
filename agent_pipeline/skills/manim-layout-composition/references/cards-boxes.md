# Card / Box Skill

> Restored verbatim from the former math-to-manim visual-pattern reference.

# Card / Box Skill

Use inside `LessonBase` section methods for concept cards, formula boxes, step boxes, definition boxes, conclusion strips, and compact comparison panels.

## When to use

- Use cards to group a small teaching unit: title, formula, one explanation line.
- Use step boxes for ordered procedures.
- Use conclusion strips for a final takeaway that remains visible.
- Use comparison panels only when the two sides are genuinely compared.

## Aesthetic rule

Cards should feel like quiet teaching surfaces, not decoration. Keep each card to one job. Use `make_panel`, theme text helpers, and no hardcoded colors.

## Safety rule

Do not nest cards inside cards. Compose card content first, wrap it once with `make_panel`, include the panel in the page's single `bodyN`, then call `fit_body` once.

## Anti-patterns

- Do not make a screen of empty boxes.
- Do not put long paragraphs inside small cards.
- Do not pass `width` or `height` into `make_panel`.
- Do not build late standalone cards after `fit_body`.

## Snippet

```python
def build_step_card(self, index_text, title_text, body_text):
    badge = self.get_highlighted_math(index_text, font_size=24, level="primary")
    title = self.get_text(title_text, font_size=22, weight=BOLD)
    body = self.get_secondary_text(body_text, font_size=20)
    content = Group(badge, Group(title, body).arrange(DOWN, buff=0.08, aligned_edge=LEFT))
    content.arrange(RIGHT, buff=0.22, aligned_edge=UP)
    return self.make_panel(content, padding=0.18)


def section_card_box_example(self):
    cards = Group(
        self.build_step_card("1", "Name the object", "Point to the thing that changes."),
        self.build_step_card("2", "Attach the rule", "Connect the formula to that object."),
        self.build_step_card("3", "Read the result", "State what the change means."),
    ).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
    summary = self.make_panel(
        self.get_success_text("A good box compresses one teaching move.", font_size=22),
        padding=0.18,
    )
    body1 = Group(cards, summary).arrange(DOWN, buff=0.28, aligned_edge=LEFT)
    self.fit_body(body1, max_width=10.8, center=UP * 0.1)
    for card in cards:
        self.play(FadeIn(card, shift=DOWN * 0.12), run_time=0.35)
    self.play(FadeIn(summary), run_time=0.35)
```
