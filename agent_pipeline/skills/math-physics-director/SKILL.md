---
name: math-physics-director
description: Execute Planner-selected math and physics representations safely in Manim. Use when a teaching plan specifies 2D/3D views, graphs, vectors, fields, surfaces, complementary representations, animated quantities, or causality beats.
---

# Math / Physics Representation Execution

Use this skill only when the lesson is primarily mathematics or physics.

- Treat every section's `representation_plan` as final.
- Preserve its dimension, primary and complementary representations, core
  visual object, animated quantities, camera intent, and semantic invariants.
- Implement causal changes as visible state transitions rather than static slides.
- Do not downgrade planned spatial, vector, field, surface, or time-evolution
  reasoning to generic 2D exposition.
- Do not add 3D or extra representations that the Planner did not select.
- Load `references/visualization-director.md` for implementation guidance.
