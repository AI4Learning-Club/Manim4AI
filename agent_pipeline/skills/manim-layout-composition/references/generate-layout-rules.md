# Generate Layout / Vector / Density Rules

> Migrated verbatim from the former CodeGen prompt contract.

LAYOUT RULES (canvas is 14.2 x 8 units, safe area +/-6.0 x +/-3.3):
- HARD RULES (must follow):
  - Think in terms of pages and blocks:
    1. decide the page regions,
    2. build the stable blocks,
    3. arrange leaf objects inside each block,
    4. clamp the finished block safely.
- Every page uses a fixed `top band + body band + subtitle band` structure.
- The subtitle band is exactly the bottom 10% of the frame on the default
  canvas. Body content may extend all the way down to the top edge of that
  band, but must not enter it.
- Put only title/badge content in the top band.
- Put only subtitle content in the subtitle band.
  - Put all teaching content in the body band. This includes graph/diagram
    blocks, formula blocks, explanation panels, task rows, prompt panels,
    roadmap/promise/takeaway/mechanism/misconception/summary strips,
    queue/stack/table/card blocks, example rows, and persistent local teaching
    text.
  - Any standalone sentence-like teaching text belongs to the body band by
    default, even if it is only one line. Do not classify sentence-like text as
    a local overlay.
- Visual graphics and text blocks must not overlap each other. Body blocks
  must not overlap other body blocks.
- Minimum readable font sizes are hard floors:
    - page titles >= 28
    - body sentence text / prompts / takeaways / roadmap / promise / summary >= 20
    - secondary explanatory text >= 18
    - formulas >= 24
    - symbolic labels >= 16
  - If the current layout would push a text category below its font floor, do
    NOT solve it by shrinking further. Reallocate space, simplify the page, or
    split the teaching point into another page.
  - Use `self.make_page_title(...)` or `self.fit_to_top_band(...)` for
    title-like objects in the top band.
- Use `self.fit_body(bodyN, max_width=..., max_height=..., center=...)`
  only for the page's unique finished `bodyN` in the body band.
- `self.fit_body(...)` is a body-band safety helper, not a layout author.
- Any dependent object whose geometry is computed from another object
    (secant, tangent, line, rectangle, shaded region, dot, icon, label,
    highlight, arrow, brace, connector) must either be inside the same fitted
    visual block, be created only after that parent block reaches final
    position, or be defined as a live follower.
  - If such an object is created after `self.fit_body(bodyN, ...)`, it MUST be
    computed from the same fitted anchor instance already inside `bodyN`.
    Rebuilding a new `Axes`, graph block, panel, or helper-returned layout and
    taking children from that rebuilt copy is forbidden.
  - Bad pattern: precompute a line/rectangle/icon/label from `axes.c2p(...)`,
    `get_center()`, `get_corner(...)`, `get_edge_center(...)`, `next_to(...)`,
    or similar anchor geometry, then fit or move the parent block, then reveal
    that stale dependent object later.
  - Bad pattern: call `build_graph_visual(...)` or `build_secant_visual(...)`
    again after `fit_body(...)` just to get `new_line`, `new_dot`, `new_label`,
    or similar dependent objects. That creates a second layout state.
  - Good pattern: write helpers such as `build_secant_on_axes(axes, x2)` or
    `build_rectangles_on_axes(axes, graph, n)` that consume the fitted anchor
    and return only the dependent geometry for that exact on-screen anchor.
  - If an already fitted block must visually change into another block on the
    same page, keep the original fitted object identity and morph it in place
    with `self.transform_in_place(old_block, target_block)`.
  - Use `self.transform_in_place(...)` when replacing the visual contents of an
    already visible fitted object while keeping the same layout slot. This is
    preferred for "same object, new appearance" transitions.
  - Do NOT use `self.transform_in_place(...)` as a generic workaround for newly
    added detached objects. If the new object is a persistent anchor-dependent
    overlay, it still needs proper structural ownership or a local builder plus
    `self.build_on_anchor(...)`.
  - Default `self.transform_in_place(...)` behavior keeps the new visual in
    the old block's slot by matching size and center. If the new visual truly
    needs a different footprint, that is usually a new page, not a refit of
    the current page.
  - In most pages, call `self.fit_body(bodyN, ...)` once on the page's unique
    `bodyN` before the first reveal, not repeatedly on later small text panels.
  - After calling `self.fit_body(bodyN, ...)`, do NOT call `.move_to()`,
    `.shift()`, or `.to_edge()` on that same whole `bodyN` again.
  - Never use `.to_edge(UP)` on its own for page titles. Put title-like objects
    in the top band.
  - ALWAYS reserve the bottom band for subtitles. Do NOT place formulas,
    diagrams, captions, or explanatory text in the subtitle band.
  - If you are unsure where something belongs, default to the body band unless
    it is literally the page title or the subtitle module.
  - If a page needs roadmap text, promise text, a takeaway, or a summary line
    that should persist on that page, include it in a preplanned body-band block
    instead of attaching it ad hoc after reveal.
  - Only symbolic labels such as `A`, `B`, `C`, `D`, `T`, `x`, `y`, `q1`, or
    similarly short object identifiers may stay as local labels near graphics.
  - Use `next_to(...)` for those symbolic labels and for non-text geometric
    overlays only. Do not use `next_to(...)` to place sentence-like teaching
    text.
  - Bad pattern: `prompt_panel.next_to(body1, DOWN, ...); self.fit_body(prompt_panel, ...)`.
    If the prompt should persist, include it in the preplanned body block. If it
    is local text, it still belongs in a body block unless it is only a
    symbolic label.
  - Bad pattern: `takeaway_panel.move_to(DOWN * ...)`.
    Persistent takeaway text belongs in the planned body layout or on a new page,
    not as late absolute-positioned loose text.
  - BETWEEN CONCEPTS: use `self.clear_scene_keep_bg()` so the persistent
    background stays visible across section transitions.
- SOFT PREFERENCES (follow unless content clearly needs otherwise):
  - Font sizes: titles 28-34, body 20-24, formulas 24-30, labels 16-20.
    If a page would force smaller text, reflow or split it instead of shrinking further.
  - Prefer a clear page structure: long top title in the top band, one fitted
    `bodyN` in the body band, subtitle band reserved below.
  - For side-by-side pages, a good default is
    `Group(left, right).arrange(RIGHT, buff=0.5)` inside `bodyN`, then one
    `self.fit_body(bodyN, ...)` with an explicit center if needed.
  - For top-down pages, a good default is title at top, visual in middle,
    formula or short text below.
  - Keep a dedicated title row above the content so the title does not visually
    collide with the graph or diagram below it.
  - Do NOT default every section to left graphic + right text.
  - For graph + explanation slides, keep the graph fully in one region and the
    explanation in another region. That explanation region may be below, above,
    or beside the graph depending on the scene.
  - If a visual needs extra explanation, prefer a caption BELOW the visual or a
    separate text panel rather than floating paragraph text over the diagram.
  - Across a full lesson, vary layouts naturally: some sections can be top-down,
    some full-width visual, some two-panel, some centered formula focus.
  - Do not repeat the exact same layout pattern for 3 or more consecutive
    sections unless the content truly requires it.
  - If the bottom area starts feeling crowded, make stronger use of the lower
    body band or split the current teaching point into the next slide.

VECTOR DIAGRAM RULES:
- Prefer self-drawn vector diagrams with Manim primitives such as Rectangle,
    RoundedRectangle, Circle, Line, Arrow, Axes, Polygon, and VGroup.
- Build diagrams progressively. Show the core object, axis, path, or shape
    first; add labels, arrows, highlighted regions, comparisons, and formulas
    only when that exact teaching beat is being explained.
- Do NOT reveal a fully annotated finished diagram at the start of the section
    if the explanation will unfold step by step.
- But do NOT add arrows or connector lines by default. Only add them when they
    are essential to the explanation and can be anchored unambiguously.
- Do NOT rely on large text placed inside shapes as the main explanation.
    Draw the object first, then explain it beside or below the object.
- If a diagram has several moving parts, keep the base geometry stable and add
    one explanatory layer at a time instead of redrawing the whole figure.
- Use outline-only shapes (`fill_opacity=0`) unless a filled region is truly
    necessary.  This reduces false overlap detections and keeps the scene clean.
- Avoid dark decorative panels, empty filled boxes, or black blocks that do not
    carry teaching information.
- On graphs, keep only essential short labels near lines and points.  Put long
    explanations, causal arrows with sentences, and conclusions outside the axes.
- For multi-step graphs, reveal them in teaching order: base axes and baseline
    curve first, then the changed curve or marked point, then the annotation or
    takeaway. Do NOT pre-place all graph labels and callouts at once.
- Never draw decorative divider lines in the explanation panel.
- Never draw custom long horizontal or vertical lines that extend from the graph
    into the explanation panel.

AVAILABLE LAYOUT HELPERS (already defined on AI4LearningBaseScene):
- `self.fit_body(...)`, `self.make_page_title(...)`, `self.show_page_title_chip(...)`
- `self.fit_to_top_band(...)`, `self.clear_scene_keep_bg(...)`
- `self.speak_with_subtitle(...)`, `self.set_subtitle(...)`, `self.clear_subtitle()`
- `self.make_panel(...)`, `self.stack_panel(...)`
- `self.connect_side(...)`, `self.connect_vertical(...)`
- `self.load_local_icon(...)`
- `self.get_secondary_text(...)`, `self.get_muted_text(...)`, `self.get_warning_text(...)`, `self.get_success_text(...)`
- `self.get_highlighted_math(...)`, `self.highlight_formula_parts(...)`
- `self.get_warning_color()`, `self.get_success_color()`, `self.get_border_color()`, `self.get_axis_color()`
Preferred pattern: one title helper per page, one `self.fit_body(bodyN, ...)` for that page's unique body root.

SECTION / SUBTITLE / DENSITY RULES:
- Keep section titles short and informative; avoid vague slogans.
- Prefer `self.speak_with_subtitle(...)` for explanation beats and keep subtitle
  wording aligned with the spoken narration.
- Prefer one natural clause per subtitle beat; split long explanations into
  multiple beats instead of forcing dense multi-line subtitles.
- Nothing except subtitles may occupy the subtitle band.
- Do NOT cram explanation text onto one page. If a page loses a clear focal
  structure, split it into another page.
- On one slide, an explanation panel should usually contain at most one short
  heading plus three short body lines.
