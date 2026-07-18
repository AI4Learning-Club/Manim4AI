# Page / Body Authoring Contract

PAGE / BODY AUTHORING CONTRACT:

This file is the authoritative page/body contract. Other references may point here, but should not restate these rules with new wording.

## Page Structure

- A section may contain multiple pages. End one page with `self.clear_scene_keep_bg()`, then define the next page from scratch.
- Compose each page before its first reveal.
- Each page must have exactly one fitted body root named `body1`, `body2`, `body3`, and so on.
- Build every persistent teaching-content object for that page inside that page's single `bodyN`.
- Explicit exceptions: the page title system belongs to the top band, and the subtitle module belongs to the subtitle band.
- `bodyN` may contain internal sub-blocks such as `top_row`, `bottom_row`, `left_col`, `right_col`, `graph_block`, `formula_block`, or `note_block`.
- Inner sub-blocks may be arranged locally, but they must not be fitted independently.
- Direct children of `bodyN` are layout blocks and must have visible spacing after arrangement.
- Use `arrange(..., buff>=0.14)` as the hard minimum for generated page-level blocks. Smaller spacing is only for tiny symbolic labels inside a dedicated graph/diagram block.

## Fit Contract

- Call `self.fit_body(bodyN, ...)` exactly once per page, and only on that page's unique `bodyN`.
- Do not define or use secondary fitted body helpers for page sub-blocks.
- Do not build patterns such as `top_body`, `lower_body`, `main_body`, `content_block`, or multiple separately fitted mini-pages on one screen.
- If one page cannot fit while preserving font floors and clarity, start a new page.
- After a page starts, do not refit or reposition the whole page. If a new persistent element would change the page structure, start a new page.
- Treat `self.fit_body(...)` as a final guardrail, not as permission to write crowded layouts.

## Body Content

- The subtitle band is exactly the bottom 10% of the default 8-unit frame and is reserved for subtitles only. Do not reserve a larger invisible subtitle zone.
- All actual teaching content belongs in the body band inside `bodyN`: graphs, diagrams, formulas, comparisons, prompts, roadmap lines, takeaway lines, summary lines, note blocks, example rows, and persistent sentence-like teaching text.
- Sentence-like teaching text must be inside `bodyN`, even if it is only one line.
- Do not attach sentence-like text after fit with patterns such as `note.next_to(body1, ...)`, `prompt.next_to(bodyN, ...)`, `takeaway.align_to(bodyN, ...)`, or `takeaway.move_to(DOWN * ...)`.
- Only symbolic labels or very short object names may stay local near graphics, such as `A`, `B`, `x`, `y`, `T`, `q1`, or short coordinate labels like `(2,4)`.
- Formula cards such as `y=f(-x)` or `y=-f(x)` are teaching content, not symbolic point labels.
- Use `next_to(...)` mainly for symbolic labels and non-text geometric overlays such as arrows, braces, rings, and highlights.
- Do not place sentence-like text inside dense shapes or graph regions. Put the diagram in one block and the explanation in another block.

## Font And Density

- Page titles: at least 28.
- Body sentence text, prompts, takeaways, roadmap/promise/summary text: at least 20.
- Secondary explanatory text: at least 18.
- Formulas: at least 24.
- Symbolic labels: at least 16.
- If a layout would force a category below its floor, reflow, allocate more space, simplify, or split into another page.
- `bodyN` should use the available body band. If the upper half is crowded while the lower body is empty, expand downward or split the page.
- Do not attach multiple text/panel objects to the same side of the same anchor with repeated `next_to(..., same_side, buff=...)`; arrange a small group, choose different sides, or move the text into a body block.

Correct / incorrect examples:

Bad multiple fitted bodies:
```python
top_body = Group(graph_block, formula_block).arrange(DOWN, buff=0.25)
lower_body = Group(note_block, takeaway_block).arrange(DOWN, buff=0.18)
self.fit_body(top_body, max_width=11.2, center=UP * 0.9)
self.fit_body(lower_body, max_width=10.6, center=DOWN * 0.5)
```

Good single page body:
```python
top_row = Group(graph_block, formula_block).arrange(RIGHT, buff=0.5, aligned_edge=UP)
note_block = Group(prompt_panel, takeaway_panel).arrange(DOWN, buff=0.18, aligned_edge=LEFT)
body1 = Group(top_row, note_block).arrange(DOWN, buff=0.24, aligned_edge=LEFT)
self.fit_body(body1, max_width=11.6, center=UP * 0.15)
```

Good symbolic local label:
```python
target_label = self.get_secondary_text("T", font_size=18)
target_label.next_to(target_node, RIGHT, buff=0.08)
```

Good dependent geometry as structural content:
```python
secant_hint = Line(axes.c2p(x1, y1), axes.c2p(x2, y2))
graph_block = Group(axes, graph, point, secant_hint)
body2 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Good dependent geometry as an anchor follower:
```python
graph_block = Group(axes, curve)
dot = self.build_on_anchor("build_point_marker_on_axes", axes, 2.0)
tangent = self.build_on_anchor("build_tangent_on_axes", axes, 2.0)
body2 = Group(graph_block, note_panel).arrange(RIGHT, buff=0.5, aligned_edge=UP)
self.fit_body(body2, max_width=11.6, center=UP * 0.2)
```

Bad stale dependent geometry:
```python
graph_block, axes, graph, secant, dot = self.build_secant_visual(x2)
body3 = Group(graph_block, text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body3, max_width=11.6, center=UP * 0.2)
_, _, _, new_secant, new_dot = self.build_secant_visual(x3)
self.play(ReplacementTransform(secant, new_secant), ReplacementTransform(dot, new_dot))
```

Good same-slot visual change:
```python
body3 = Group(Group(axes, graph, secant, dot), text_block).arrange(RIGHT, buff=0.5)
self.fit_body(body3, max_width=11.6, center=UP * 0.2)
self.play(
    self.transform_in_place(secant, Line(axes.c2p(x1, y1), axes.c2p(x3, y3))),
    self.transform_in_place(dot, Dot(axes.c2p(x3, y3))),
)
```
