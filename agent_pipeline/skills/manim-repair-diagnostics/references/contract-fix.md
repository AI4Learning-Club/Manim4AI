# Full-File Contract Fix

Repair only the deterministic Scene Pack contract mismatches reported for a
newly generated file.

## Scope

- Return the complete corrected Python file according to the system output contract.
- Preserve correct teaching content, narration, visuals, helpers, section order,
  selected theme, and selected assets.
- Change only the full-file structures named by the mismatch report. Allowed
  targets include `SCENE_MANIFEST`, `LessonBase` bases or class attributes,
  wrapper classes, and broken manifest-to-method references.
- Keep unrelated section-method bodies unchanged.
- Do not redesign the lesson or apply general visual improvements.
- Treat `render_scope` as exact: remove extra manifest entries, wrappers, and
  `opening_page`/`closing_page` methods owned by another backend.

## Capability Contracts

- Treat a compiled `LessonBase` signature as exact. If moving-camera capability
  is required, use `class LessonBase(AI4LearningBaseScene, MovingCameraScene):`.
- If Planner-selected 3D capability is required, use
  `class LessonBase(AI4LearningBaseScene, ThreeDScene):` and replace incompatible
  `self.camera.frame` mechanics with native `ThreeDScene` camera APIs.
- Do not remove a required capability merely to make a validation message disappear.
- Follow the selected runtime and Scene Pack references for API and structure safety.

## Completion

Fix every reported mismatch. If a mismatch cannot be repaired without changing
the lesson's meaning, preserve the original content and leave that mismatch
unresolved rather than rewriting unrelated code.
