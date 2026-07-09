# Teacher Script Core

Use a reverse-knowledge-tree mindset.

- Choose the opening beat that fits the concept: a real question, concrete example, visual phenomenon, result preview, direct explanation, or task read-in.
- Do not force every lesson to start with learner confusion or a chain of rhetorical questions.
- Ask what prerequisite intuition must be clear before the target concept.
- Build the explanation from the chosen opening structure -> mathematical mechanism -> takeaway.
- Prefer clean visual reasoning over decorative effects.
- When a section is routed to Manim, make the animation do the hard logical work.
- Couple every important formula with a visible object, graph, geometric relation, or state change.
- If a section is mostly motivational, summary-like, or verbal, it probably should not stay in Manim.

When code generation needs reusable Manim idioms rather than broad lesson advice,
rely on the selected specialized references from the current skill packages:
annotation patterns, layout composition, coordinate geometry, equation
derivation, and motion pacing. Treat those references as
local patterns to adapt into `LessonBase` section methods with lesson-specific
names, narration, and teaching content.
