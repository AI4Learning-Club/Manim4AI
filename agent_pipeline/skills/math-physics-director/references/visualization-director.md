# Math / Physics Representation Execution

The Planner owns the visualization design. Do not independently decide the
lesson's dimensionality, representation set, teaching arc, or examples.

## Compile `representation_plan`

- `dimension=2d`: use plane geometry, `Axes`, curves, diagrams, or other flat
  objects that faithfully implement the named primary representation.
- `dimension=3d`: use the compiled 3D scene capability and appropriate objects
  such as `ThreeDAxes`, `Surface`, or spatial geometry.
- `dimension=mixed`: implement each explicitly named view in separate stable
  beats or sections; preserve the planned correspondence between them.
- Realize every named `animated_quantities` value as an actual changing object,
  tracker, path, vector, field, state, or parameter.
- Preserve every `must_preserve` relationship visibly. For example, keep a
  surface point synchronized with its contour projection when that mapping is
  part of the plan.
- Implement `camera_intent` with the compiled scene capability. Use fixed-frame
  labels for important explanatory text when a 3D camera moves.

## Safe implementation

- Use `Text` for natural language and `MathTex` for formulas only.
- Couple formulas to the planned visible object or changing quantity.
- Reveal complementary representations in the Planner's order and make their
  relationship explicit through aligned color, labels, guides, or transforms.
- Prefer debuggable section-local objects and deterministic trackers.
- Keep causality visible: current state, changed quantity, reason or direction,
  and resulting state.

Do not infer an additional 3D view, analogy, numeric example, or alternate
teaching flow merely because it might be interesting. Skills provide Manim
implementation knowledge; they do not override Planner decisions.
