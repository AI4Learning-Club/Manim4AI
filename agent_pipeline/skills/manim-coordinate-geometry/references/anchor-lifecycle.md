# Geometry / Anchor Protocol

GEOMETRY / ANCHOR PROTOCOL:

This file owns the lifecycle of anchor-dependent geometry. Page/body composition is owned by `page-body-layout.md`.

## Terms

- A `Layout Block` is a page-level container such as `graph_block`, `formula_block`, `note_block`, `prompt_block`, or `question_block`.
- A `Geometry Block` is the coordinate/diagram core, such as axes + graph, number line + markers, or canvas + primary shapes.
- `Anchor Followers` are dependent objects such as dots, tangents, secants, helper lines, braces, shaded regions, arrows, local labels, and highlights.

## Ownership Rule

- Primary geometry belongs directly inside the fitted visual owner, usually `graph_block` or `diagram_block`.
- Treat `self.build_on_anchor(...)` as the DEFAULT generation path for persistent geometry leaves that are not structural children of `graph_block`.
- Hard rule: any anchor-dependent non-text object must satisfy one accepted lifecycle:
  1. it is a structural child of the fitted visual block that owns the anchor, or
  2. it is created through `self.build_on_anchor(...)`.
- Never precompute dependent geometry from one layout state and then fit `bodyN` afterward.
- If dependent geometry is created after `fit_body(...)`, compute it from the same fitted anchor instance already on screen inside `bodyN`.

## What Counts As Dependent Geometry

- Tangents, secants, point markers, arrows, braces, highlights, shaded regions, helper lines, local labels, and small leaf groups.
- Outputs of anchor-derived methods such as `axes.c2p(...)`, `axes.get_area(...)`, `axes.get_riemann_rectangles(...)`, `axes.plot(...)`, `graph.point_from_proportion(...)`, and `graph.get_secant_slope_group(...)`.
- Objects positioned by `obj.get_center()`, `obj.get_right()`, `obj.get_corner(...)`, `next_to(anchor, ...)`, or `move_to(anchor)` when the object must remain semantically attached.

## Builder Protocol

- Write small semantic builders such as `build_tangent_on_axes(...)`, `build_secant_on_axes(...)`, `build_point_marker_on_axes(...)`, or `build_local_label_on_dot(...)`.
- Builder inputs must be the current on-screen anchor objects or stable semantic parameters derived from them.
- Builder outputs must be only the dependent leaf object or a small leaf group, not a rebuilt full graph block or page body.
- A builder must not recreate a fresh axes + graph + labels bundle just to obtain one tangent, secant, dot, or label.
- A builder must not depend on a stale pre-fit copy of a block or anchor.
- If called with a string builder name, the builder must be a real method on `LessonBase` / `self`.
- If the builder is nested inside a section method, pass the callable itself:
```python
self.build_on_anchor(build_point_on_axes, axes, ...)
```

## Live Binding

- `self.build_on_anchor(builder, ...)` defaults to non-live binding. Use that for leaves that only need to resync after layout events such as `fit_body(...)`, `fit_to_top_band(...)`, or explicit synchronization.
- If the follower must visibly track a moving anchor during animation, pass `live=True`.
- `self.bind_to_anchor(...)` and `self.bind_to_block(...)` are lower-level repair helpers, not the default generation path.

## Good Patterns

Structural child:
```python
point = Dot(axes.c2p(x0, y0))
graph_block = Group(axes, graph, point)
body1 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body1, max_width=11.6, center=UP * 0.2)
```

Anchor follower:
```python
graph_block = Group(axes, graph)
dot = self.build_on_anchor("build_point_marker_on_axes", axes, x0)
tangent = self.build_on_anchor("build_tangent_on_axes", axes, x0)
label = self.build_on_anchor("build_local_label_on_dot", dot, "P")
body2 = Group(graph_block, note_block).arrange(RIGHT, buff=0.5)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Local callable builder:
```python
def build_connector_on_objects(source, target):
    return Arrow(source.get_right(), target.get_left(), buff=0.08)

connector = self.build_on_anchor(build_connector_on_objects, point, note_panel)
```

## Bad Patterns

- Directly arrange geometry leaves such as `axes`, `curve`, `dot`, `tangent`, `secant`, and point labels together with page titles, notes, prompts, or explanation blocks.
- Treat a tangent, secant, point marker, arrow, or brace as an independent page-layout block unless packaged inside a larger visual owner.
- Use ad-hoc raw coordinates or one-axis alignment as the final placement rule for a follower that should stay attached to an axes, graph, node, bar, or panel.
- Create a line, plot, dot, point row, area, or shaded region from an anchor before `fit_body(...)`, leave it outside the same fitted owner, and reveal it later.
- Call a full visual helper again after `fit_body(...)` to extract `new_line`, `new_dot`, `new_label`, or similar dependent leaves.

Preferred fix for bad patterns: move the object into the structural `graph_block` when it is truly part of the visual core; otherwise rewrite it as a builder plus `self.build_on_anchor(...)`.
