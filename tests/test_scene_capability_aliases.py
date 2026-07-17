from __future__ import annotations

from agent_pipeline.scene_capabilities import (
    compile_scene_capability_contract,
    validate_scene_capability_contract,
)


def _pack(method_body: str) -> str:
    return f"""\
from manim import *
from plugins.manim.colortest.ai4learning_theme import AI4LearningBaseScene

SCENE_MANIFEST = [
    {{"id": "demo", "scene": "Segment00DemoScene", "method": "section_demo"}},
]

class LessonBase(AI4LearningBaseScene):
    def section_demo(self):
{method_body}

class Segment00DemoScene(LessonBase):
    def construct(self):
        self.section_demo()
"""


def test_camera_alias_does_not_leak_across_methods() -> None:
    code = _pack("        cam = object()\n        frame = cam.frame").replace(
        "    def section_demo(self):",
        "    def helper(self):\n"
        "        cam = self.camera\n"
        "        return cam\n\n"
        "    def section_demo(self):",
    )

    assert validate_scene_capability_contract(
        code,
        compile_scene_capability_contract(()),
    ) == []


def test_reassigned_scene_alias_is_not_treated_as_three_d_scene() -> None:
    code = _pack(
        "        scene_alias = self\n"
        "        scene_alias = object()\n"
        "        scene_alias.move_camera()"
    )

    assert validate_scene_capability_contract(
        code,
        compile_scene_capability_contract(()),
    ) == []


def test_same_scope_scene_alias_still_requires_three_d_capability() -> None:
    code = _pack("        scene_alias = self\n        scene_alias.move_camera()")

    issues = validate_scene_capability_contract(
        code,
        compile_scene_capability_contract(()),
    )

    assert any("3D capability was not selected" in issue for issue in issues)
