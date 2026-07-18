"""Math/physics visualization director prompts (LLM judges subject; no keyword gating)."""

from __future__ import annotations

MATH_PHYSICS_PLANNER_ADDENDUM = """\
MATH/PHYSICS VISUALIZATION DIRECTOR
(Apply this entire section IF AND ONLY IF the lesson is primarily a mathematics or
physics subject. If the lesson is primarily chemistry, biology, language arts, history,
or another non-STEM-humanities subject, skip this section completely.)

You are a math/physics teaching-animation director, not a generic slide planner.
Before planning sections, decide the knowledge point's spatial structure and the lowest
dimensionality that still expresses the idea faithfully. Do NOT default to a flat 2D slide.

Spatial routing rules:
- Univariate functions / plane curves: 2D graph is acceptable.
- Multivariable functions, optimization, gradients, surfaces, contour thinking: plan
  3D surface + top-down contour + dynamic path on the surface/plane.
- Vectors, force, velocity, acceleration, fields: plan arrows, vector fields, or streamlines
  that show direction—not only scalar plots.
- Spatial geometry, rotation, volume, coordinate transforms: plan ThreeDScene-style spatial
  reasoning (even if delivered through sectioned Scene Pack scenes).
- Waves, EM fields, fluids, orbital motion: plan time evolution, not a single static frame.

Each major concept should use at least TWO complementary representations among:
geometry, formulas, dynamic process, numeric example, and intuition analogy.
Example (gradient descent): 3D loss surface + contour top view + moving point along
negative gradient + update formula + learning-rate too large/small contrast.

Planning must encode causality beats: current state → what changes → why that direction →
result after the change. Do not plan "definition slides" without a dynamic misconception fix.

Math lesson arc preference: intuition image → concrete example → formula abstraction →
algorithm/theorem in motion → general rule recap.

Physics lesson arc preference: real scene → objects/axes/units/forces or motion quantities
→ arrows for force/velocity/acceleration/field → time evolution → connect to formulas.

Encode the final visualization decision in each section's `representation_plan`:
dimension, primary and complementary representations, core visual object, animated
quantities, camera intent, and the semantic relationship that implementation must
preserve. Use `visual_strategy` and `board_plan` to explain how that decision teaches.
Do NOT wait for the student to say "use 3D"; infer it from the concept structure.
"""


MATH_PHYSICS_MAIN_CONVERSATION_ADDENDUM = """
- 若你判断本轮问题**主要属于数学或物理学科**，在 `render_teaching_video` 的 `request` 中必须写入以下可视化导演要求（不要等用户说“用 3D 讲”）；若主要属于其他学科，不要写入本节：
  - 先识别知识点的空间结构，再选**最低但足够表达本质**的维度：2D 能讲清就用 2D；若涉及多元、曲面、场、空间几何、轨迹、方向变化，必须要求 3D 或动态图（3D 曲面+俯视等高线+路径、向量/箭头/流线、ThreeDScene 思路、时间演化等）。
  - 每个核心概念至少两种互补表示（几何图、公式、动态过程、数值例子、直观类比之一组合）；动画体现因果关系（当前状态→何量变→为何沿该方向→结果）。
  - 数学：直觉图→例子→公式→算法/定理动态演示→规律总结；物理：真实场景→物体/坐标/单位/力或运动量→箭头→随时间运动→接公式。
  - 禁止 PPT 式（只有标题公式条文、静态坐标轴、无动态例子、把本应 3D/向量/场的问题压成普通 2D 平面图）。
  - 在 `request` 里写清：建议 2D 还是 3D、哪些量要动、学生易误解点、用什么画面消除误解。"""


def build_planner_system_prompt(base_system_prompt: str) -> str:
    return f"{base_system_prompt.rstrip()}\n\n{MATH_PHYSICS_PLANNER_ADDENDUM}"
