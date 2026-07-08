# Geometry / Anchor Protocol

> Migrated verbatim from the former CodeGen prompt contract.

GEOMETRY / ANCHOR PROTOCOL:
- Treat each page as a composition of Layout Blocks, not as a flat list of
  unrelated mobjects.
- A `Layout Block` is a page-level block such as `graph_block`,
  `formula_block`, `note_block`, `prompt_block`, `question_block`, or another
  container that participates in page composition.
- A `Geometry Block` is the visual core of a coordinate-based or diagram-based
  graphic, such as axes + graph, number line + markers, or a diagram canvas +
  its primary shapes.
- `Anchor Followers` are dependent visual objects such as dots, tangents,
  secants, helper lines, braces, shaded regions, arrows, local labels, and
  highlights that must stay attached to a semantic anchor.
- In this codebase, use one default ownership rule:
  1. primary geometry belongs directly inside `graph_block`,
  2. persistent detached geometry leaves should be created with
     `self.build_on_anchor(...)`.
- Treat `self.build_on_anchor(...)` as the DEFAULT generation path for
  persistent geometry leaves that are not structural children of `graph_block`.
- Only Layout Blocks may use `arrange(...)`, `next_to(...)`, or `fit_body(...)`
  for page composition.
- Do NOT directly arrange geometry leaf objects such as `axes`, `curve`,
  `dot`, `tangent`, `secant`, or local point labels together with page title,
  note, prompt, or explanation blocks.
- Do NOT treat a tangent, secant, point marker, arrow, or brace as an
  independent page-layout block unless it is intentionally packaged inside a
  larger `graph_block` / `diagram_block`.
- Treat outputs of anchor-derived methods such as `axes.get_area(...)`,
  `axes.get_riemann_rectangles(...)`, `axes.plot(...)`, and
  `graph.get_secant_slope_group(...)` as dependent geometry too. If they are
  not structural children of the fitted visual block, they should normally be
  created through `self.build_on_anchor(...)`.
- The expected pattern is: write a small local builder such as
  `build_tangent_on_axes(...)`, `build_secant_on_axes(...)`,
  `build_point_marker_on_axes(...)`, or `build_label_on_point(...)`, then call
  `self.build_on_anchor(...)`.
- If you call `self.build_on_anchor(...)` with a STRING builder name, that
  builder must be a real method on `LessonBase` / `self`, not a nested local
  function defined inside the section method.
- If the builder is a nested local function, pass the callable itself:
  `self.build_on_anchor(build_point_on_axes, axes, ...)`, not
  `self.build_on_anchor("build_point_on_axes", axes, ...)`.
- BUILDER PROTOCOL:
  - Define anchor builders on `LessonBase` or the current scene when a section
    needs persistent geometry leaves.
  - Use stable names such as `build_tangent_on_axes(...)`,
    `build_secant_on_axes(...)`, `build_point_marker_on_axes(...)`,
    `build_local_label_on_dot(...)`, or similarly clear semantic names.
  - Builder inputs MUST be the current on-screen anchor objects or stable
    semantic parameters derived from them.
  - Builder outputs MUST be only the dependent leaf object or a small leaf
    group for that anchor state, not a rebuilt full graph block or page body.
  - A builder MUST NOT recreate the whole visual owner such as a fresh axes +
    graph + labels bundle just to obtain one tangent / secant / dot / label.
  - A builder MUST NOT depend on a stale pre-fit copy of a block or anchor.
  - If the scene layout changes, the builder should still be valid when called
    against the fitted on-screen anchor instance that already lives in `bodyN`.
  - Keep builders local and minimal: they are small semantic constructors for
    leaves, not generic page-layout helpers.
- `self.build_on_anchor(builder, ...)` defaults to NON-LIVE anchor binding.
  Use that default for objects that only need to resync after layout events
  such as `fit_body(...)`, `fit_to_top_band(...)`, or explicit scene-level
  synchronization.
- Do NOT blindly force `live=False` for every anchor-bound object.
  If the object must visibly keep following moving anchors during animation,
  you MUST pass `live=True` explicitly.
- `self.build_on_anchor(builder, ..., live=True)` is for dependent geometry
  whose shape or endpoints must continuously rebuild from moving anchors.
- `self.bind_to_anchor(...)` and `self.bind_to_block(...)` remain available as
  lower-level repair helpers, but they are not the default generation path.
- Any non-text visual object whose position or shape is meant to relate to
  another visual structure must have an explicit anchor or coordinate system,
  and it must share the same positioning lifecycle as that anchor. This
  applies both before and after `fit_body(...)`.
- A follower should be created from a semantic anchor builder whenever its
  geometry depends on the fitted anchor state rather than merely inheriting a
  parent block transform.
- Prefer helpers such as `build_tangent_on_axes(axes, x0)`,
  `build_secant_on_axes(axes, x0, x1)`, `build_point_marker_on_axes(...)`, or
  similar anchor-aware builders over one-off geometry derived from stale
  measurements.
- Good anchor patterns include `axes.c2p(...)`,
  `graph.point_from_proportion(...)`, `obj.get_center()`, `obj.get_right()`,
  `obj.get_corner(...)`, `next_to(anchor, ...)`, `move_to(anchor)`, or helper
  functions that consume the actual on-screen anchor instance and return
  geometry for that exact anchor.
- Bad pattern: use `get_center() + RIGHT * ... + UP * ...` or similar one-off
  measurement math as the final persistent placement rule for a follower that
  should stay semantically attached to a point, line, region, or panel.
- Bad pattern: a floating dot / point row / arrow / icon positioned by ad-hoc
  raw coordinates or by only one-axis alignment when it is supposed to live on
  an axes, graph, node, bar, or panel.
- Also bad: create a line, plot, dot, point row, area, or shaded region from
  `axes.c2p(...)`, `axes.plot(...)`, `axes.get_area(...)`, or another anchor
  expression before `fit_body(...)`, but do not include that geometry inside
  the same fitted `graph_block` / `bodyN`. Then the anchor moves during
  fitting while the geometry stays behind.
- Preferred fix: move the object into `graph_block` if it is truly structural;
  otherwise rewrite it as a local builder plus `self.build_on_anchor(...)`.
- If you create dependent geometry after `fit_body(...)`, compute it from the
  SAME fitted anchor instance that is already on screen inside `bodyN`, and
  then immediately make the lifecycle explicit by inserting it into the
  structural owner or by using `self.build_on_anchor(...)`.
- Hard rule: any anchor-dependent non-text object must satisfy one of these
  two accepted lifecycle patterns:
  1. it is a structural child of the fitted visual block that owns the anchor,
  2. it is created through `self.build_on_anchor(...)`.
- Never precompute dependent geometry from one layout state and then fit
  `bodyN` afterward.
- If a helper is used after `fit_body(...)`, it must accept the fitted anchor
  as an argument and return only the dependent geometry tied to that anchor
  (for example `build_secant_on_axes(axes, x2)`), rather than recreating the
  full visual block.
