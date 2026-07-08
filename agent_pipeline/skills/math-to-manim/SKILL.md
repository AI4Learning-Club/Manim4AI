---
name: math-to-manim
description: Use for mathematically dense educational animation generation. Applies when the agent needs to create or repair Manim lesson segments involving equations, graphs, geometry, derivations, coordinate systems, or precise visual reasoning.
---

# Math-To-Manim

Use a reverse-knowledge-tree mindset.

- Choose the opening beat that fits the concept: a real question, concrete example, visual phenomenon, result preview, direct explanation, or task read-in.
- Do not force every lesson to start with learner confusion or a chain of rhetorical questions.
- Ask what prerequisite intuition must be clear before the target concept.
- Build the explanation from the chosen opening structure -> mathematical mechanism -> takeaway.
- Prefer clean visual reasoning over decorative effects.
- When a section is routed to Manim, make the animation do the hard logical work.
- Couple every important formula with a visible object, graph, geometric relation, or state change.
- If a section is mostly motivational, summary-like, or verbal, it probably should not stay in Manim.

Use the visual-pattern references when code generation needs reusable Manim idioms rather than broad lesson advice:

- `references/visual-patterns/arrows.md`
- `references/visual-patterns/labels.md`
- `references/visual-patterns/highlights.md`
- `references/visual-patterns/cards-boxes.md`
- `references/visual-patterns/equation-focus.md`
- `references/visual-patterns/coordinate-systems.md`
- `references/visual-patterns/motion-transitions.md`
- `references/visual-patterns/problem-intake-marking.md`
- `references/visual-patterns/graph-dynamics.md`

Treat those references as local patterns to migrate into `LessonBase(AI4LearningBaseScene)`, then adapt names, narration, and teaching content to the current Scene Pack.
