# Language / API Safety

> Migrated verbatim from the former CodeGen prompt contract.

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
