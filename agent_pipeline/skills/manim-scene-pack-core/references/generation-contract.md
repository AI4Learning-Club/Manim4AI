# Scene Pack Contract

SCENE PACK CONTRACT:
- Output a Scene Pack in ONE Python file, not a single master scene.
- The file MUST define a top-level `SCENE_MANIFEST` list in final playback order.
- Every manifest `id` MUST be a stable snake_case identifier such as
  `opening`, `task_difference`, `linear_regression`, or `closing`.
- The file MUST define exactly one shared base class named `LessonBase`.
- Normal scenes should use `class LessonBase(AI4LearningBaseScene):`.
- If a selected skill requires an additional mixin, keep `AI4LearningBaseScene`
  first in the inheritance list and follow that selected skill's exact class signature.
- Put shared helpers and section methods on `LessonBase`.
- The file MUST define renderable wrapper scenes named
  `Segment00...Scene`, `Segment01...Scene`, and so on through the final segment.
- Wrapper scene names MUST follow this stable pattern:
  `Segment00OpeningScene`, `Segment01TaskDifferenceScene`,
  `Segment02LinearRegressionScene`, and so on.
- Every wrapper scene MUST inherit from `LessonBase`.
- Every wrapper scene's `construct()` MUST contain exactly ONE direct call to
  ONE section method on `self`, with no extra animation logic there.
- Use stable section method names such as `opening_page()`,
  `section_one_xxx()`, `section_two_xxx()`, and `closing_page()`.
- Do NOT output a single master scene whose `construct()` calls multiple
  section methods in sequence.
- Every `SCENE_MANIFEST` entry MUST be a dictionary with keys:
  `id`, `scene`, and `method`.
- Section methods MUST live on `LessonBase`.
- Use `opening_page()` for the opening segment and `closing_page()` for the
  closing segment.
- Use numbered section names such as `section_one_task_difference()`,
  `section_two_linear_regression()`, `section_three_...()` for interior segments.
- Required wrapper pattern:
  ```python
  class Segment00OpeningScene(LessonBase):
      def construct(self):
          self.opening_page()
  ```
- The `scene` value in each manifest entry MUST match a real wrapper class name.
- The `method` value in each manifest entry MUST match a real section method on
  `LessonBase`.
- Manifest order MUST match final playback order and the wrapper numbering.
- Do NOT hide the full lesson flow inside one mega `construct()`.
