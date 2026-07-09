---
name: manim-camera-movement
description: Camera movement patterns for Manim lessons. Use for MovingCameraScene, camera.frame, zoom, pan, viewport focus, magnification, close-up inspection, and repairing jerky or unsafe camera moves.
---

# Manim Camera Movement

Use this skill when the lesson needs a deliberate camera move rather than a static page.

- Use `MovingCameraScene` only when zoom, pan, or close-up focus teaches the idea.
- Keep the normal Scene Pack structure, wrappers, TTS, theme helpers, and `fit_body(...)` discipline.
- Use `class LessonBase(AI4LearningBaseScene, MovingCameraScene):` when camera movement is actually used.
- Keep `AI4LearningBaseScene` first in the inheritance list so project setup, theme, TTS, subtitle, and layout helpers run correctly.
- Load `references/camera-movement.md` for camera-frame idioms and safety rules.
