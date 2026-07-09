# Theme And Local Asset Safety

THEME & COLOR SAFETY:
- You MUST import our custom base class from `plugins.manim.colortest.ai4learning_theme`.
- New code MUST use a Scene Pack, not a single master scene.
- The file MUST define one `LessonBase` that includes `AI4LearningBaseScene`;
  keep `AI4LearningBaseScene` first if a selected skill requires an additional
  mixin.
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
