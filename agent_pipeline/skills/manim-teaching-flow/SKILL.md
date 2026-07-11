---
name: manim-teaching-flow
description: Execute an upstream teaching plan as teacher-like Manim animation. Use for faithful opening, section moves, examples, board plans, reveal order, narration/subtitle alignment, misconceptions, transitions, and closing without redesigning pedagogy.
---

# Manim Teaching Flow

Use this skill when CodeGen must compile a Planner-owned teaching contract into
classroom-like animation behavior.

- Obey `render_scope` first; never render semantic roles owned by Remotion.
- Treat included opening, section order, examples, representation choices,
  narration goals, transitions, and closing as final decisions.
- Implement the selected opening beat only when scope includes it; do not choose another one.
- Realize each section's teacher move, student question, concrete example,
  board plan, narration goal, takeaway, and transition.
- Couple important formulas with visible objects, graphs, relations, state changes, or examples.
- Make the animation do the logical work when a section is routed to Manim.
- Choose only unspecified Manim implementation details, using the simplest safe
  option that preserves the plan.
- Use references only when their condition matches this run:
  - `references/teaching-plan-execution.md` for generation-time plan execution.
  - `references/teacher-script.md` for broad teaching-plan execution.
  - `references/reveal-narration.md` for progressive reveal and subtitle pacing.
