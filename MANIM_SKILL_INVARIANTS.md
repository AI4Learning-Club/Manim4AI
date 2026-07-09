# Manim Skill Invariants

This file is the canonical checklist for slimming Manim CodeGen skill references. A slimmed reference must preserve the behavior below while removing repeated wording, stale migration context, and long illustrative prose.

## Ownership Map

- Scene Pack structure, wrapper scenes, and generated file shape are owned by `manim-scene-pack-core`.
- Theme, asset loading, Manim API safety, and default runtime helpers are owned by `manim-runtime-safety`.
- Page/body/title/subtitle placement is owned by `manim-layout-composition/references/page-body-layout.md`.
- Generation-time layout heuristics, vector diagram choices, density limits, and helper inventory are owned by `manim-layout-composition/references/generate-layout-rules.md`.
- Anchor lifecycle for dependent geometry is owned by `manim-coordinate-geometry/references/anchor-lifecycle.md`.
- Coordinate-system and graph-specific construction details are owned by the coordinate geometry references.
- Formula API safety is owned by runtime safety; formula highlighting is owned by annotation highlights; equation staging stays in teaching-flow guidance.
- Reveal order, narration chunks, and subtitle synchronization are owned by `manim-teaching-flow/references/reveal-narration.md`.
- Motion timing, transitions, graph annotation pointers, and pacing are owned by `manim-motion-pacing/references/motion-transitions.md`.
- Pedagogical structure, examples, misconceptions, and teacher-script choices are owned by the teaching-flow pedagogy references.
- Camera movement is owned by the camera reference and must not be restated in layout or reveal references.

## Cross-Reference Rules

- A non-owner file may mention another invariant only as a short bridge, such as "follow `anchor-lifecycle.md` for dependent geometry."
- Do not restate an owned rule with different wording in another reference.
- Keep hard rules separate from soft preferences.
- Prefer one precise term over aliases. Use `bodyN` for the page body root, `subtitle band` for the bottom subtitle area, and `anchor follower` for dependent geometry.
- Keep examples only when they disambiguate a rule that prose alone fails to protect.

## Non-Negotiable Behavior

- Each page has exactly one fitted body root named `body1`, `body2`, `body3`, and so on.
- Persistent teaching content belongs inside that page's `bodyN`; page titles and subtitles are the explicit exceptions.
- The subtitle band is exactly the bottom 10% of the default frame and is reserved for subtitles only.
- Call `self.fit_body(bodyN, ...)` once per page on the finished body root, then do not reposition that whole root.
- Preserve font floors: titles >= 28, body sentence text >= 20, secondary explanatory text >= 18, formulas >= 24, symbolic labels >= 16.
- Sentence-like teaching text is body content. Only short symbolic labels and tiny coordinate labels may stay local near graphics.
- Anchor-dependent non-text geometry must either be a structural child of the fitted visual owner or be created with `self.build_on_anchor(...)`.
- Do not compute dependent geometry from one layout state and reveal it after fitting or moving a different state.
- Reveal stable page content beat by beat in sync with narration; do not reveal whole page containers.
- Use `self.speak_with_subtitle(...)` for explanation beats unless raw `self.speak(...)` is specifically needed.
- Let narration duration drive animation timing; keep non-teaching transitions short and mostly silent.

## Slimming Checklist

- Preserve all test sentinel phrases already used by prompt-contract tests.
- Remove migration history, duplicated examples, and broad Manim knowledge the base model already has.
- Keep enough local context for the file to work when selected alone.
- Replace repeated paragraphs with a pointer to the owning reference.
- Ensure the remaining rules have one interpretation and no competing synonyms.
- Run the relevant skill prompt-contract tests after editing.
