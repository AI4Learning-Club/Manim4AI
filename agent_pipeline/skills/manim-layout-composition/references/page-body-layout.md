# Page / Body Authoring Contract

> Migrated verbatim from the former CodeGen prompt contract.

PAGE / BODY AUTHORING CONTRACT:
- A section may contain multiple pages.
- End one page with `self.clear_scene_keep_bg()`, then define the next page
  from scratch.
- Compose each page before its first reveal.
- Each page must have exactly one fitted body root named `body1`, `body2`,
  `body3`, and so on.
- Build every persistent teaching-content object for that page inside that
  page's single `bodyN`.
- Explicit exceptions:
  - the page title system belongs to the top band, not `bodyN`
  - the subtitle module belongs to the subtitle band, not `bodyN`
- `bodyN` may contain internal sub-blocks such as `top_row`, `bottom_row`,
  `left_col`, `right_col`, `graph_block`, `formula_block`, or `note_block`.
- Inner sub-blocks may be arranged locally, but they must NOT be fitted
  independently.
- Direct children of `bodyN` are layout blocks. They must have visible spacing
  between their bounding boxes after arrangement.
- For generated page-level blocks, use `arrange(..., buff>=0.14)` as a hard
  minimum. A smaller buff is allowed only for tiny symbolic labels inside a
  dedicated graph/diagram block, never for paragraph text, formulas, panels,
  or body columns.
- Do NOT attach multiple text/panel objects to the same side of the same anchor
  with repeated `next_to(..., same_side, buff=...)`. Build a small arranged
  label group, choose different anchor sides, or move the text into a body
  block.
- Do NOT place sentence-like text inside dense shapes or graph regions. Put
  the shape/diagram in one block and the explanation in a separate nearby block.
- Call `self.fit_body(bodyN, ...)` exactly once per page, and only on that
  page's unique `bodyN`.
- Do NOT define or use secondary fitted body helpers for page sub-blocks.
- If one page cannot fit while preserving font floors and clarity, start a new
  page instead of fitting multiple body roots on the same screen.
- Do NOT build patterns such as `top_body`, `lower_body`, `main_body`,
  `content_block`, or multiple separately fitted mini-pages on one screen.
- The subtitle band is permanently reserved for subtitles only.
- Hard constraint: the subtitle band is exactly the bottom 10% of the frame
  (0.8 units on the default 8-unit-high canvas). Do NOT reserve a larger
  invisible subtitle-safe zone.
- All actual teaching content belongs in the body band inside `bodyN`. This
  includes graphs, diagrams, formulas, comparisons, prompts, roadmap lines,
  takeaway lines, summary lines, note blocks, example rows, and other
  persistent sentence-like teaching text. The page title system and subtitle
  module are explicit exceptions and must stay outside `bodyN`.
- Sentence-like teaching text must be inside `bodyN`.
- Only symbolic labels or very short object names may stay local near graphics,
  such as `A`, `B`, `x`, `y`, `T`, `q1`, or similarly short identifiers.
- Short coordinate labels such as `(2,4)`, `(-2,4)`, `(0,0)`, or one short
  point name plus a tiny coordinate may stay local near their anchor point.
- Formula cards like `y=f(-x)` or `y=-f(x)` are NOT symbolic point labels.
  They are teaching content and should be planned inside `bodyN`, not attached
  later as loose post-fit cards.
- Use `next_to(...)` primarily for those symbolic labels and for non-text
  geometric overlays such as arrows, braces, rings, and highlights.
- Do NOT use `next_to(...)` to place sentence-like teaching text.
- Ban patterns such as `note.next_to(body1, ...)`, `prompt.next_to(bodyN, ...)`,
  `takeaway.align_to(bodyN, ...)`, or `takeaway.move_to(DOWN * ...)`.
- If a sentence-like object should persist on that page, it must be planned
  inside `bodyN` before the first reveal of that page.
- Respect minimum readable font sizes:
  - page titles: at least 28
  - body sentence text, prompts, takeaways, roadmap/promise/summary text: at least 20
  - secondary explanatory text: at least 18
  - formulas: at least 24
  - symbolic labels: at least 16
- If a layout would force a text category below its font floor, do NOT keep
  shrinking. Reflow the page, allocate more space, simplify the current page,
  or split into another page instead.
- `bodyN` should make strong use of the available body band.
- If a page is dense, do NOT leave a large unused lower-body area while the
  upper half is crowded. Expand downward or split into the next page.
- After a page starts, do NOT refit or reposition the whole page. If a new
  persistent element would change the page structure, start a new page instead.
- `self.fit_body(bodyN, ...)` aggressively separates overlapping top-level
  body blocks by default. Treat this as a final guardrail, not as permission
  to write crowded layouts. If a page only works because this guardrail moves
  blocks apart, simplify the page or split it.

Correct / incorrect examples:

Bad:
```python
top_body = Group(graph_block, formula_block).arrange(DOWN, buff=0.25)
lower_body = Group(note_block, takeaway_block).arrange(DOWN, buff=0.18)
self.fit_body(top_body, max_width=11.2, center=UP * 0.9)
self.fit_body(lower_body, max_width=10.6, center=DOWN * 0.5)
```

Good:
```python
top_row = Group(graph_block, formula_block).arrange(RIGHT, buff=0.5, aligned_edge=UP)
note_block = Group(prompt_panel, takeaway_panel).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
body1 = Group(top_row, note_block).arrange(DOWN, buff=0.24, aligned_edge=LEFT)
self.fit_body(body1, max_width=11.6, center=UP * 0.15)
```

Good for a symbolic local label:
```python
target_label = self.get_secondary_text("T", font_size=18)
target_label.next_to(target_node, RIGHT, buff=0.08).align_to(target_node, UP)
```

Good for dependent geometry:
```python
secant_hint = Line(axes.c2p(x1, y1), axes.c2p(x2, y2))
graph_block = Group(axes, graph, point, secant_hint)
body2 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Bad geometry/layout mixing:
```python
body2 = VGroup(
    axes,
    curve,
    dot,
    tangent,
    note_panel,
    prompt_block,
).arrange(DOWN, buff=0.25)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Good geometry block + anchor-built followers:
```python
graph_block = Group(axes, curve)
dot = self.build_on_anchor("build_point_marker_on_axes", axes, 2.0)
tangent = self.build_on_anchor("build_tangent_on_axes", axes, 2.0)

right_col = Group(note_panel, prompt_block).arrange(DOWN, buff=0.22, aligned_edge=LEFT)
body2 = Group(graph_block, right_col).arrange(RIGHT, buff=0.5, aligned_edge=UP)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Good local-builder pattern:
```python
def build_tangent_on_axes(self, axes, x0):
    y0 = self.func(x0)
    slope = self.derivative(x0)
    return axes.plot(lambda x: y0 + slope * (x - x0), x_range=[x0 - 1.0, x0 + 1.0])

graph_block = Group(axes, curve)
tangent = self.build_on_anchor("build_tangent_on_axes", axes, 2.0)
point_label = self.build_on_anchor("build_point_label_on_axes", axes, 2.0, "P")

side_block = Group(note_panel, question_block).arrange(DOWN, buff=0.22, aligned_edge=LEFT)
body2 = Group(graph_block, side_block).arrange(RIGHT, buff=0.5, aligned_edge=UP)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Good area-builder pattern:
```python
def build_area_on_axes(self, axes, graph, x_range):
    return axes.get_area(graph, x_range=x_range)

graph_block = Group(axes, graph)
area = self.build_on_anchor("build_area_on_axes", axes, graph, [1.0, 2.0])

body2 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5, aligned_edge=UP)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Good label-builder pattern:
```python
def build_local_label_on_dot(self, dot, text):
    label = self.get_secondary_text(text, font_size=18)
    label.next_to(dot, UP + RIGHT, buff=0.08)
    return label

graph_block = Group(axes, graph, dot)
label = self.build_on_anchor("build_local_label_on_dot", dot, "P")

body2 = Group(graph_block, side_block).arrange(RIGHT, buff=0.5, aligned_edge=UP)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Good connector-builder pattern:
```python
def build_connector_on_objects(self, source, target):
    return Arrow(source.get_right(), target.get_left(), buff=0.08, stroke_width=3)

graph_block = Group(axes, graph, point)
connector = self.build_on_anchor("build_connector_on_objects", point, note_panel)

body2 = Group(graph_block, note_panel).arrange(RIGHT, buff=0.5, aligned_edge=UP)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Also good when the object is truly structural:
```python
point = Dot(axes.c2p(x0, y0))
graph_block = Group(axes, graph, point)
body3 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body3, max_width=11.6, center=UP * 0.2)
```

Bad after fit:
```python
graph_block, axes, graph, secant, dot = self.build_secant_visual(x2)
body3 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body3, max_width=11.6, center=UP * 0.2)

new_graph_block, _, _, new_secant, new_dot = self.build_secant_visual(x3)
self.play(ReplacementTransform(secant, new_secant), ReplacementTransform(dot, new_dot))
```

Good after fit:
```python
graph_block = Group(axes, graph, secant, dot)
body3 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body3, max_width=11.6, center=UP * 0.2)

self.play(
    self.transform_in_place(secant, Line(axes.c2p(x1, y1), axes.c2p(x3, y3))),
    self.transform_in_place(dot, Dot(axes.c2p(x3, y3))),
)
```

Good when the structure must change:
```python
self.clear_scene_keep_bg()
title = self.make_page_title("Now we rebuild the idea", font_size=28)
body4 = Group(new_visual_block, new_note_block).arrange(DOWN, buff=0.24)
self.fit_body(body4, max_width=11.6, center=UP * 0.15)
```
