# Teacher Script Core

Treat the Planner's teaching contract as authoritative.

- Obey `render_scope` first. Use continuity context only to connect included
  sections; do not recreate opening, problem-intake, or closing beats owned by Remotion.
- Implement `opening.architecture`, `opening.style`, and `opening.hook_line`
  exactly only when scope includes opening; do not select a different opening.
- Preserve the planned section order and narrative arc.
- Turn each `teacher_move` into visible and spoken actions that answer the section's `student_question`.
- Use the planned `concrete_example`; do not substitute a more convenient example.
- Build the planned `board_plan` progressively and satisfy `narration_goal` with direct spoken beats.
- End on `key_takeaway`, then realize the planned `transition`; render `closing`
  only when scope includes it.
- Prefer clean visual reasoning over decorative effects.
- When a section is routed to Manim, make the animation do the hard logical work.
- Couple every important formula with a visible object, graph, geometric relation, or state change.
- If a section is mostly motivational, summary-like, or verbal, it probably should not stay in Manim.
- When a plan detail is absent, choose only the smallest safe implementation detail; do not redesign surrounding pedagogy.

When code generation needs reusable Manim idioms rather than broad lesson advice,
rely on the selected specialized references from the current skill packages:
annotation patterns, layout composition, coordinate geometry, formula emphasis
through highlights, and motion pacing. Treat those references as
local patterns to adapt into `LessonBase` section methods with lesson-specific
names, narration, and teaching content.
