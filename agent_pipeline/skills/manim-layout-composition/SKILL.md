---
name: manim-layout-composition
description: Page and body layout composition for Manim lessons. Use for top/body/subtitle bands, fit_body discipline, title systems, density, overlap, cards, panels, boxes, crowded layout repair, and readable font floors.
---

# Manim Layout Composition

Use this skill when page organization, block spacing, text readability, panels, or overlap risk matters.

- Treat each page as top band, body band, and subtitle band.
- Put teaching content inside one fitted `bodyN` root per page.
- Use page titles outside `bodyN`; reserve the bottom subtitle band for subtitles only.
- Reflow or split instead of shrinking below readable font floors.
- Prefer block composition over ad hoc `.shift()` and loose `next_to()` for sentence-like text.
- Load:
  - `references/page-body-layout.md` for the page/body contract.
  - `references/density-overlap.md` for crowded layout and validation repair.
  - `references/cards-boxes.md` for problem cards, panels, and boxed explanations.
