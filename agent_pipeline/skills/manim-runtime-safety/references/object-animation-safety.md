# Group / VGroup / Create Safety

> Migrated verbatim from the former CodeGen prompt contract.

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
