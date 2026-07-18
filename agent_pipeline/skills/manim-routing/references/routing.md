# Manim Hybrid Routing Skill

# Routing Heuristics

Use Manim when at least one of these is central to the section:

- equation-driven reasoning
- geometric construction
- axes, curves, coordinate transforms, vectors
- parameter changes that need frame-by-frame visual proof
- derivations or symbolic structure that benefit from synchronized motion

Use the Manim section to resolve the "hard part" of the lesson:

- make invisible structure visible
- animate causal or mathematical dependence
- show why a formula matches the picture
- turn a static diagram into stepwise reasoning

Avoid spending Manim time on:

- general motivation
- broad concept framing
- misconception headlines without a needed mathematical animation
- end summaries or transition bridges

## Render scope contract

When a teaching plan contains `render_scope`, treat it as the hard backend
ownership contract. Generate exactly `allowed_section_ids` in order and only
the semantic roles whose `include_*` flag is true. `continuity_context` may
shape a local transition, but it must not create Scene Pack entries, methods,
visuals, or narration for content owned by Remotion.

For legacy plans without `render_scope`, use `hybrid_routes.manim_section_ids`
as the section whitelist.
