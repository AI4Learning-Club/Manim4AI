# Motion / Transition Skill

Use inside `LessonBase(AI4LearningBaseScene)` when the lesson needs clean pacing, progressive reveal, same-slot replacement, formula morphing, or a transition between visual states.

## When to use

- Use progressive reveal when a page has several teaching beats.
- Use `transform_in_place` when one fitted object changes meaning but should keep the same layout slot.
- Use `TransformMatchingTex` when formulas share symbolic structure.
- Use fast silent clears between concepts; narrate the teaching step, not the housekeeping.

## Aesthetic rule

Motion should explain what changed. Keep the page stable and move only the object under discussion. Use theme helpers and no hardcoded colors.

## Safety rule

Compose all persistent page objects inside the current `bodyN` before the first reveal. After `fit_body`, do not refit, shift, or rebuild the page just to animate a later state.

## Anti-patterns

- Do not fade in an entire finished page before explaining it.
- Do not drag a whole body group around after reveal.
- Do not use flashy transitions between unrelated ideas.
- Do not create a second off-screen layout after `fit_body` just to get a transform target.

## Snippet

```python
def section_motion_reveal_example(self):
    title = self.make_page_title("Reveal only the current idea", font_size=30)
    formula = self.get_math(r"v=\frac{\Delta s}{\Delta t}", font_size=34)
    next_formula = self.get_math(r"v(t)=s'(t)", font_size=34)
    note_a = self.make_panel(self.get_text("Average rate uses an interval.", font_size=22), padding=0.18)
    note_b = self.make_panel(self.get_success_text("Instant rate uses one moment.", font_size=22), padding=0.18)
    body1 = Group(formula, note_a, note_b).arrange(DOWN, buff=0.28)
    self.fit_body(body1, max_width=10.8, center=UP * 0.1)
    next_formula.move_to(formula.get_center())
    self.add(title)
    self.speak_with_subtitle("先只看平均变化率。", Write(formula), run_time=0.8)
    self.speak_with_subtitle("它对应一小段区间。", FadeIn(note_a), run_time=0.7)
    self.speak_with_subtitle(
        "当区间缩到一个点，就得到瞬时变化率。",
        self.transform_in_place(formula, next_formula),
        FadeIn(note_b),
        run_time=1.0,
    )
```

```python
def section_transition_between_pages(self):
    first = self.make_panel(self.get_text("Page one: build the intuition.", font_size=22), padding=0.18)
    body1 = Group(first)
    self.fit_body(body1, max_width=9.6, center=UP * 0.1)
    self.play(FadeIn(first), run_time=0.4)
    self.clear_scene_keep_bg(run_time=0.45, wait_time=0.1)
    title = self.make_page_title("Now formalize it", font_size=30)
    formula = self.get_math(r"f'(x_0)=\lim_{h\to0}\frac{f(x_0+h)-f(x_0)}{h}", font_size=28)
    body2 = Group(formula)
    self.fit_body(body2, max_width=11.0, center=UP * 0.1)
    self.add(title)
    self.play(Write(formula), run_time=0.8)
```

