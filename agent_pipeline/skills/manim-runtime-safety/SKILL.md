---
name: manim-runtime-safety
description: Manim Community v0.20.1 runtime safety for CodeGen. Use for API compatibility, theme/assets contracts, Group/VGroup/Create/Write safety, MathTex/Text boundaries, callback/deepcopy/pickle failures, and local icon handling.
---

# Manim Runtime Safety

Use this skill to prevent render crashes and unsafe helper usage.

- Target Manim Community v0.20.1.
- Prefer AI4Learning theme helpers and `AI4LearningBaseScene`.
- Use only selected local assets; never invent paths or URLs.
- Use `Group` for mixed containers; reserve `VGroup` for VMobjects.
- Do not call `Create` or `Write` on mixed `Group`/panel containers.
- Keep callbacks deepcopy-safe; do not capture `self` inside graph callbacks, updaters, or stored lambdas.
- Load detailed runtime guards only as needed:
  - `references/api-core.md`
  - `references/theme-assets.md`
  - `references/object-animation-safety.md`
  - `references/callback-safety.md`
