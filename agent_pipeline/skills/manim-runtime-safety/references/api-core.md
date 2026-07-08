# Manim Runtime API Core

> Migrated verbatim from the former CodeGen prompt contract.

COMMON RUNTIME SAFETY RULES (shared across generate / fix / improve):

THEME & COLOR SAFETY:
- You MUST import our custom base class from `plugins.manim.colortest.ai4learning_theme`.
- New code MUST use a Scene Pack, not a single master scene.
- The file MUST define `class LessonBase(AI4LearningBaseScene):`.
- Every renderable wrapper scene MUST inherit from `LessonBase`, NOT `Scene`.
- When a selected theme is provided, `LessonBase` MUST declare
  `theme_id = "selected_theme_id"` at class scope.
- NEVER use pure `WHITE` or pure black body text.
- Do NOT hardcode hex colors for text, formulas, panels, or shapes.
- Prefer theme helpers over raw token lookups whenever possible:
  `self.get_text(...)`, `self.get_secondary_text(...)`,
  `self.get_muted_text(...)`, `self.get_warning_text(...)`,
  `self.get_success_text(...)`, `self.get_math(...)`,
  `self.get_highlighted_math(..., level="primary"|"secondary")`,
  `self.highlight_formula_parts(...)`, `self.make_panel(...)`,
  `self.get_warning_color()`, `self.get_success_color()`,
  `self.get_border_color()`, and `self.get_axis_color()`.
- When you truly need a semantic token not covered by a helper, use
  `self.theme_token(...)` with names such as `text_secondary`, `text_muted`,
  `accent_primary`, `accent_secondary`, `formula_highlight_primary`,
  `formula_highlight_secondary`, `warning_color`, `success_color`,
  `panel_stroke`, `panel_fill_color`, `panel_fill_opacity`,
  `border_color`, and `grid_or_axis_color`.
- If `LessonBase` already sets `theme_id = "..."`, preserve that theme selection.
- Do NOT replace `self.theme_token(...)`, `self.get_text(...)`,
  `self.get_secondary_text(...)`, `self.get_muted_text(...)`,
  `self.get_math(...)`, `self.get_highlighted_math(...)`,
  `self.highlight_formula_parts(...)`, or `self.make_panel(...)`
  with hardcoded hex colors.

LOCAL ICON SAFETY:
- The teaching plan may include a `selected_assets` list. Those are the ONLY
  local icon files you may use.
- If `selected_assets` is empty or absent, do NOT invent icons, image paths,
  URLs, or external assets.
- If you use a selected local icon, load it with
  `self.load_local_icon("filename.png", height=0.9)`.
- Do NOT switch icon loading to raw file paths or URLs during fixes.

GROUP / VGROUP / CREATE SAFETY:
- Default to `Group(...)` for page layout containers and mixed-object layouts.
- Use `VGroup(...)` ONLY when every child is guaranteed to be a `VMobject`
  such as `Text`, `MathTex`, `Line`, `Circle`, `Polygon`, or `SVGMobject`.
- `self.load_local_icon(...)` may return `ImageMobject`, so never place its
  result inside `VGroup(...)`. Use `Group(...)` instead.
- If a variable was already built with `Group(...)`, never pass that variable
  into `VGroup(...)` later.
- `self.make_panel(...)` returns `Group(panel, content)`, so never place the
  result of `self.make_panel(...)` inside `VGroup(...)`.
- Do NOT pass `width=...` or `height=...` into `self.make_panel(...)`.
  This helper auto-sizes from `content` via `SurroundingRectangle(...)`, and
  those size kwargs cause a runtime constructor conflict in this theme stack.
  Resize the inner content or surrounding layout instead.
- Typical bad pattern: `VGroup(title, summary, transfer)` when `summary` or
  `transfer` already comes from `self.make_panel(...)` or another `Group(...)`.
  In that case, use `Group(...).arrange(...)` instead.
- `VGroup(*self.mobjects)` is unsafe. Use `Group(*self.mobjects)`.
- Never call `Create(...)` on a `Group(...)`.
- For `Group(...)`, use `FadeIn(...)` or animate child VMobjects separately.
- Only use `Create(...)`, `Write(...)`, or `GrowArrow(...)` on actual
  `VMobject` instances.
- Use `GrowArrow(...)` only for plain `Arrow`-like VMobjects. For `CurvedArrow`,
  `Arc`, or path-like objects, prefer `Create(...)` or animate children
  separately.
- If you define a helper that returns an arrow/path pair and you want to use
  `Create(...)` on the whole result, return `VGroup(...)` ONLY if every child
  is a `VMobject`; otherwise animate the children separately.

LANGUAGE / API SAFETY:
- `from manim import *` at the top.
- Target `Manim Community v0.20.1` compatibility. Do not rely on older blog
  posts, outdated snippets, or pre-0.20 helper methods.
- Manim CE does **not** define a `CENTER` constant (using it raises NameError).
  For the center of the frame use **`ORIGIN`** (e.g. `mob.move_to(ORIGIN)`).
  Do not use `CENTER` in code even if layout prose says "center".
- `Axes` does NOT provide `get_grid()` in the target runtime. If you need a
  background grid, build it explicitly with `NumberPlane(...)`, or style the
  axes/ticks directly. Never call `axes.get_grid()`.
- Do NOT access `axes.get_x_axis().label_marks` or `axes.get_y_axis().label_marks`.
  Those internals are not stable in the target runtime. If you need coordinate
  labels, use `axes.add_coordinates()` defaults or rebuild explicit labels as
  your own text/math mobjects.
- For axis / number-line labels created via `add_labels(...)`, never pass a
  raw LaTeX-heavy string such as `r"\frac{\pi}{2}"`, `r"x^2"`, or
  `r"y_1"` directly in the mapping. Use a ready-made label mobject such as
  `MathTex(r"\frac{\pi}{2}")` or `MathTex(r"x^2")` instead.
- Simple numeric string labels like `"0"` or `"1"` are acceptable in
  `add_labels(...)`, but any nontrivial math notation must be wrapped in a
  math mobject explicitly.
- Use `Text(...)` or theme text helpers for natural-language titles, labels,
  captions, subtitles, and narration-related screen text.
- `Write(...)` only works on vectorized mobjects. Do NOT call `Write(...)` on a
  `Group(...)` / panel / mixed container. For those, prefer `FadeIn(...)`, or
  animate their child VMobjects individually.
- Do NOT put full natural-language phrases inside `MathTex(...)` / `Tex(...)`.
- Chinese text must NEVER appear inside `MathTex(...)` / `Tex(...)`.
- Pure math: `MathTex(r"...", font_size=...)`.
- MIXED natural language + math: split into parts and arrange them with
  `Group(...)` or `VGroup(...)` as appropriate.
- Any variable with subscripts/superscripts (e.g. `x_t`, `Q_d`) MUST use
  `MathTex`, not plain `Text`.
- Do not use nonexistent APIs such as `get_tangent_vector(...)` on `VMobject`.
- `get_tangent_line` does NOT accept `color=`. Build the tangent manually.
- There is no `self.get_text_color()` helper here. Use `self.get_text(...)`,
  `self.get_math(...)`, or `self.theme_token("text_main")` /
  `self.theme_token("formula_base")` directly.

CALLBACK / DEEPCOPY SAFETY:
- Never pass a bound Scene method such as `self._position_func` or
  `self.some_helper` into Manim objects that may store callbacks and later get
  copied, including `axes.plot(...)`, `FunctionGraph(...)`,
  `ParametricFunction(...)`, `always_redraw(...)`, and updater callbacks.
- In particular, do NOT write `axes.plot(self.func, ...)`, `axes.plot(self.some_curve, ...)`,
  or helper methods that return `axes.plot(...)` from a lambda/inner function
  that still closes over `self`. Those patterns later trigger deep-copy /
  pickle failures in Manim.
- Avoid callbacks or lambdas that capture `self` when those callbacks are stored
  on mobjects, graphs, or animations.
- Bad pattern: `axes.plot(self._position_func, ...)`.
- Bad pattern: `always_redraw(lambda: Dot(self.axes.c2p(...)))`.
- Bad pattern: `mob.add_updater(lambda m: m.move_to(self.some_anchor(...)))`.
- Prefer a module-level function, a `@staticmethod`, or a local pure function
  that depends only on plain numeric values, not on `self`.
- If you only need a static curve, compute it from a pure function and build
  the mobject once. Do not keep a Scene-bound callback attached to the mobject.
- If you truly need dynamic redraw behavior, the callback must avoid capturing
  `self`; capture only stable numeric parameters or already-built anchor
  mobjects, and rebuild from those.
- Any callback stored on a mobject must remain deep-copy-safe. Never let it
  close over the live `Scene`, renderer, audio client, locks, threads, or
  other non-picklable runtime state.

STATE / CLEARING SAFETY:
- If the scene inherits from `AI4LearningBaseScene`, prefer
  `self.clear_scene_keep_bg()` over `FadeOut(Group(*self.mobjects))` so the
  persistent background is not removed.
- Use the available `AI4LearningBaseScene` layout helpers before stacking many
  manual `.shift()` / `.to_edge()` calls.
- Use `self.make_page_title(...)`, `self.show_page_title_chip(...)`, and
  `self.fit_body(...)`.
