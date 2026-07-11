---
name: manim-camera-movement
description: Camera movement and viewport choreography for Manim lessons. Use for MovingCameraScene, camera.frame, zoom, pan, follow/track shots, viewport focus, magnification, close-up inspection, and repairing jerky or unsafe camera moves.
---

# Manim Camera Movement

Use this skill when the lesson needs a deliberate camera move rather than a static page.

- Use `MovingCameraScene` only when zoom, pan, follow/track, or close-up focus teaches the idea.
- Implement only Planner-selected camera intents: inspect, pan, follow/track, and restore.
- Use follow/track when a moving point, path, trajectory, graph region, or process should guide attention.
- Do not satisfy a moving-path lesson with only zoom-in/restore.
- Keep the normal Scene Pack structure, wrappers, TTS, theme helpers, and `fit_body(...)` discipline.
- Use the exact `LessonBase` signature emitted by the capability compiler.
- Keep `AI4LearningBaseScene` first in the inheritance list so project setup, theme, TTS, subtitle, and layout helpers run correctly.
- Load `references/camera-movement.md` for camera-frame idioms and safety rules.
