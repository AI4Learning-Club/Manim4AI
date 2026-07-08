---
name: manim-scene-pack-core
description: Scene Pack contract for Manim CodeGen. Use when generating or repairing one-file Scene Pack output with SCENE_MANIFEST, LessonBase, wrapper scenes, stable section methods, and preservation of segment architecture.
---

# Manim Scene Pack Core

Use this skill for the structural contract of every generated or repaired Manim file.

- Generate or preserve one Python Scene Pack file.
- Keep one shared `LessonBase(AI4LearningBaseScene)`.
- Keep top-level `SCENE_MANIFEST` in final playback order.
- Keep wrapper scenes thin: each wrapper `construct()` calls exactly one section method.
- Put shared helpers and section methods on `LessonBase`.
- Do not collapse multi-segment Scene Packs into a single master scene.
- Read `references/generation-contract.md` for new generation.
- Read `references/repair-preservation.md` for fixes and improvements.
