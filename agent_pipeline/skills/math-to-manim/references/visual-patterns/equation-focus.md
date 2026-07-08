# Equation Focus Skill

Use inside `LessonBase(AI4LearningBaseScene)` when formulas need local explanation, term-by-term reveal, transformation, or a link back to a visible diagram.

## When to use

- Use progressive reveal when a formula has a logical build-up.
- Use `TransformMatchingTex` when two formulas share visible structure.
- Use braces for one local term explanation, not for every symbol.
- Use arrows only from a formula term to a visible object or note.

## Aesthetic rule

Make one mathematical idea visible at a time. The best formula focus feels like a teacher pointing to the exact term now being explained. Use theme helpers and no hardcoded colors.

## Safety rule

Keep formula blocks inside `bodyN`. Any arrow, brace, or label tied to a formula term must share that formula block's lifecycle or use `build_on_anchor`.

## Anti-patterns

- Do not reveal the final formula before the intuition or derivation has happened.
- Do not put natural-language sentences inside `MathTex`.
- Do not attach long labels to formula terms with `next_to` after `fit_body`.
- Do not transform a fitted formula into a larger layout footprint on the same page.

## Snippet

```python
def section_equation_focus_example(self):
    start = self.get_math(r"y = mx + b", font_size=34)
    next_formula = self.get_math(r"\Delta y = m\Delta x", font_size=34)
    self.highlight_formula_parts(start, primary=("m",), secondary=("b",))
    explanation = self.make_panel(
        self.get_text("m tells how much y changes per unit x.", font_size=22),
        padding=0.18,
    )
    body1 = Group(start, explanation).arrange(DOWN, buff=0.35)
    self.fit_body(body1, max_width=10.8, center=UP * 0.15)
    self.play(Write(start), run_time=0.55)
    self.play(Circumscribe(start), FadeIn(explanation), run_time=0.65)
    self.play(
        self.transform_in_place(start, next_formula, match_size=True, match_position=True),
        run_time=0.8,
    )
```

```python
def build_brace_note_for_formula(self, formula, note_text):
    brace = Brace(formula, DOWN, color=self.get_formula_highlight_color("secondary"))
    label = self.get_secondary_text(note_text, font_size=18)
    label.next_to(brace, DOWN, buff=0.08)
    return VGroup(brace, label)


def section_equation_brace_example(self):
    formula = self.get_math(r"A=\pi r^2", font_size=36)
    brace_note = self.build_on_anchor("build_brace_note_for_formula", formula, "area grows with r squared")
    body1 = Group(formula, brace_note).arrange(DOWN, buff=0.22)
    self.fit_body(body1, max_width=9.6, center=UP * 0.1)
    self.play(Write(formula), run_time=0.5)
    self.play(Create(brace_note[0]), FadeIn(brace_note[1]), run_time=0.5)
```

