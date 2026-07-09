# Generate Layout / Vector / Density Rules

This reference owns generation-time layout choices. For the full page/body contract, use `page-body-layout.md`; for dependent geometry, use `anchor-lifecycle.md`.

LAYOUT RULES (canvas is 14.2 x 8 units, safe area +/-6.0 x +/-3.3):

## Hard Rules

- Compose every page as `top band + body band + subtitle band`.
- Put only title/badge content in the top band, only subtitles in the subtitle band, and all persistent teaching content in the body band.
- The subtitle band is exactly the bottom 10% of the default frame. Body content may reach its top edge but must not enter it.
- Build one finished body root per page named `body1`, `body2`, `body3`, and so on.
- Put graphs, diagrams, formulas, panels, prompts, roadmap/promise/takeaway/summary lines, examples, and sentence-like local teaching text inside that page's `bodyN`.
- Fit only the finished `bodyN`: `self.fit_body(bodyN, max_width=..., max_height=..., center=...)`.
- Call `self.fit_body(...)` once per page before the first reveal; after fitting, do not call `.move_to()`, `.shift()`, or `.to_edge()` on the whole `bodyN`.
- Do not fit page sub-blocks or create multiple fitted mini-pages such as `top_body`, `lower_body`, `main_body`, or `content_block`.
- Visual graphics and text blocks must not overlap. Top-level body blocks need visible spacing after arrangement.
- Use `self.make_page_title(...)` or `self.fit_to_top_band(...)` for title-like objects; never rely on bare `.to_edge(UP)` for page titles.
- Between distinct concepts, use `self.clear_scene_keep_bg()` and build the next page from scratch.

## Font Floors

- Page titles: >= 28.
- Body sentence text, prompts, takeaways, roadmap/promise/summary text: >= 20.
- Secondary explanatory text: >= 18.
- Formulas: >= 24.
- Symbolic labels: >= 16.
- If a layout violates a font floor, reallocate space, simplify the page, or split the idea into another page instead of shrinking further.

## Local Labels And Dependent Geometry

- Only short symbolic labels such as `A`, `B`, `x`, `y`, `T`, `q1`, or tiny coordinate labels may stay local near graphics.
- Do not use `next_to(...)` to place sentence-like teaching text. Persistent sentence-like text belongs inside `bodyN`.
- Anchor-dependent objects such as secants, tangents, dots, arrows, braces, highlights, shaded regions, and local labels must follow one accepted lifecycle:
  1. be structural children of the fitted visual owner, or
  2. be created through `self.build_on_anchor(...)` from the fitted on-screen anchor.
- Do not rebuild a fresh axes, graph block, panel, or helper-returned layout after `fit_body(...)` just to extract a new dependent leaf.
- Use `self.transform_in_place(...)` only when an already visible fitted object should morph within the same layout slot. It is not a workaround for detached anchor-dependent overlays.

## Soft Preferences

- Preferred font ranges: titles 28-34, body text 20-24, formulas 24-30, labels 16-20.
- For side-by-side pages, arrange internal blocks first, then fit one `bodyN`, for example `Group(left, right).arrange(RIGHT, buff=0.5)`.
- For top-down pages, keep a clear title, central visual/formula focus, and short supporting text.
- Do not default every section to left graphic + right text; vary layouts naturally across the lesson.
- If the lower body area is unused while the upper half is crowded, expand downward or split the teaching point.
- Explanation panels should usually contain at most one short heading plus three short body lines.

VECTOR DIAGRAM RULES:

- Prefer self-drawn Manim diagrams with primitives such as `Rectangle`, `RoundedRectangle`, `Circle`, `Line`, `Arrow`, `Axes`, `Polygon`, and `VGroup`.
- Build diagrams progressively: base object first, then labels, arrows, highlighted regions, comparisons, and formulas only when the narration reaches that beat.
- Do not reveal a fully annotated finished diagram at the start of a section.
- Add arrows or connectors only when they are essential and can be anchored unambiguously.
- Do not use large text inside shapes as the main explanation; draw the object, then explain beside or below it.
- Keep base geometry stable and add one explanatory layer at a time.
- Prefer outline-only shapes (`fill_opacity=0`) unless a filled region carries teaching meaning.
- Avoid decorative dark panels, empty filled boxes, divider lines, and long custom lines between graph and explanation panels.
- On graphs, keep only essential short labels near lines and points; move sentence-level explanations outside the axes.
- For multi-step graphs, reveal axes/baseline first, then the changed curve or marked point, then annotation or takeaway.

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

Preferred page pattern: one title helper, one finished `bodyN`, one `self.fit_body(bodyN, ...)`, subtitle band reserved below.

SECTION / SUBTITLE / DENSITY RULES:

- Keep section titles short and informative.
- Prefer `self.speak_with_subtitle(...)` for explanation beats.
- TTS calls must be direct and extractable: use `self.speak_with_subtitle("literal text", ...)` or `self.speak("literal text", ...)` in the section method; do not hide TTS behind `narrate`/`say` helpers or variable first arguments.
- Use one natural clause per subtitle beat; split long explanations instead of forcing dense multi-line subtitles.
- Nothing except subtitles may occupy the subtitle band.
- Do not cram explanation text onto one page. If the page loses a clear focal structure, split it.
