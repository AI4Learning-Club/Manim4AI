# Teaching Plan Execution Contract

The supplied teaching plan contains final pedagogical decisions. Compile those
decisions into runnable Manim; do not perform a second round of lesson design.

## Decision ownership

- Obey `render_scope` before all teaching fields. Render only its allowed
  section ids and included semantic roles. Treat `continuity_context` as
  read-only context; never turn excluded opening or closing content into code.
- Preserve `student_profile`, `teaching_promise`, `teacher_voice`, and
  `narrative_arc` in tone, pacing, and emphasis.
- Follow `opening.architecture`, `opening.style`, `opening.hook_line`, and
  `opening.roadmap_style`. Do not replace them with a stock opening.
- Preserve every section id and its order.
- Do not replace a planned example, visual representation, misconception,
  narration goal, transition, check for understanding, or closing.

## Section compilation

For every section:

1. Make `student_question` the learner-facing focus.
2. Realize `teacher_move` as concrete visible and spoken actions.
3. Use `concrete_example` and implement `visual_strategy`.
4. Treat `representation_plan` as binding: preserve its dimension, primary and
   complementary representations, animated quantities, camera intent, and
   `must_preserve` relationships.
5. Build `board_plan` progressively rather than showing a finished slide.
6. Turn `narration_goal` into direct literal TTS calls synchronized with the
   relevant reveal.
7. Land on `key_takeaway`, use `check_for_understanding` when planned, then
   execute `transition` into the next section.

## Limited implementation freedom

Choose only unspecified Manim details such as exact mobjects, coordinates,
animation primitives, spacing, and safe timing. Prefer the simplest runnable
implementation that preserves the teaching contract. A runtime limitation may
change the mechanism, but not the intended lesson meaning or representation.

Implement `closing.summary`, `closing.transfer_question`, and
`closing.after_class_prompt` only when `render_scope.include_closing` is true or
when no render scope is supplied.
