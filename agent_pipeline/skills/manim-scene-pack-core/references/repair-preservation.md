# Scene Pack Repair / Preservation Contract

> Migrated verbatim from the former CodeGen prompt contract.

SCENE PACK REPAIR / PRESERVATION CONTRACT:
- You MUST preserve the top-level `SCENE_MANIFEST`.
- You MUST preserve the shared `LessonBase` class.
- You MUST preserve the wrapper scenes referenced by `SCENE_MANIFEST`.
- NEVER collapse a multi-scene Scene Pack back into a single master scene.
- NEVER delete `SCENE_MANIFEST`, `LessonBase`, or the wrapper scene layer.
- If you add or split pages, do that INSIDE the existing segment method on
  `LessonBase`; do not create ad-hoc extra renderable scenes for page splits.
- Do NOT change the semantic order of `SCENE_MANIFEST` unless the user
  explicitly asks to reorder sections.
- Do NOT rename manifest ids, wrapper scenes, or section methods unless a
  broken reference absolutely requires it. If you must repair such a reference,
  update `SCENE_MANIFEST`, `LessonBase`, and the wrapper scene call consistently.
- Keep the stable naming scheme:
  - base class: `LessonBase`
  - wrapper scenes: `Segment00...Scene`, `Segment01...Scene`, ...
  - opening method: `opening_page()`
  - internal section methods: `section_one_xxx()`, `section_two_xxx()`, ...
  - closing method: `closing_page()`
  - manifest ids: stable snake_case
