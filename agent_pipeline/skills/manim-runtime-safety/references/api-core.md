# Manim Runtime API Core

Core runtime rules for every generated or repaired Manim Scene Pack.

## Core Scene Contract

- Import Manim with `from manim import *`.
- Import `AI4LearningBaseScene` from
  `plugins.manim.colortest.ai4learning_theme`.
- Target `Manim Community v0.20.1` compatibility.
- Generate or preserve a Scene Pack, not a single master scene.
- Define exactly one shared `LessonBase` that includes
  `AI4LearningBaseScene`.
- If a selected skill requires an additional mixin, keep
  `AI4LearningBaseScene` first and follow that selected skill's exact class
  signature.
- Every renderable wrapper scene inherits from `LessonBase`, not `Scene`.

## API Prohibitions

- Do not use `CENTER`; use `ORIGIN`.
- Do not call `axes.get_grid()`.
- Do not access `axes.get_x_axis().label_marks` or
  `axes.get_y_axis().label_marks`.
- For `add_labels(...)`, simple numeric string labels such as `"0"` or `"1"`
  are acceptable, but nontrivial math labels must be wrapped in `MathTex(...)`.
- Do not use nonexistent APIs such as `get_tangent_vector(...)` on `VMobject`.
- `get_tangent_line` does not accept `color=`.
- There is no `self.get_text_color()` helper; use theme helpers or
  `self.theme_token(...)` through the selected theme reference.

## Delegated Safety References

- Theme, colors, local assets, and theme helpers belong to
  `runtime-theme-assets`.
- `Group`, `VGroup`, `Create`, `Write`, panels, and mixed mobject animation
  safety belong to `runtime-object-safety`.
- `Text`, `MathTex`, `Tex`, Chinese text, formula labels, and label API safety
  belong to `runtime-language-api`.
- callbacks, updaters, `always_redraw`, `deepcopy`, pickle/thread-lock errors,
  and state-clearing details belong to `runtime-callback-safety`.
