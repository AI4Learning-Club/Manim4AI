"""Compile and validate structural Scene capabilities selected for CodeGen."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

MOVING_CAMERA_REFERENCE_ID = "camera-movement"
DEFAULT_LESSON_BASES = ("AI4LearningBaseScene",)
MOVING_CAMERA_LESSON_BASES = ("AI4LearningBaseScene", "MovingCameraScene")
THREE_D_LESSON_BASES = ("AI4LearningBaseScene", "ThreeDScene")
THREE_D_SCENE_ONLY_APIS = frozenset(
    {
        "set_camera_orientation",
        "begin_ambient_camera_rotation",
        "stop_ambient_camera_rotation",
        "begin_3dillusion_camera_rotation",
        "stop_3dillusion_camera_rotation",
        "move_camera",
        "add_fixed_orientation_mobjects",
        "add_fixed_in_frame_mobjects",
        "remove_fixed_orientation_mobjects",
        "remove_fixed_in_frame_mobjects",
        "set_to_default_angled_camera_orientation",
    }
)
THREE_D_MOBJECT_NAMES = frozenset(
    {
        "Arrow3D",
        "Cone",
        "Cube",
        "Cylinder",
        "Dodecahedron",
        "Dot3D",
        "Icosahedron",
        "Line3D",
        "Octahedron",
        "Prism",
        "Sphere",
        "Surface",
        "Tetrahedron",
        "ThreeDAxes",
        "ThreeDVMobject",
        "Torus",
    }
)


@dataclass(frozen=True)
class SceneCapabilityContract:
    """Deterministic structural requirements derived from selected references."""

    reference_ids: tuple[str, ...]
    lesson_base_bases: tuple[str, ...]
    requires_moving_camera: bool = False
    requires_3d: bool = False

    @property
    def lesson_base_signature(self) -> str:
        return f"class LessonBase({', '.join(self.lesson_base_bases)}):"

    def prompt_text(self) -> str:
        lines = [
            "## Compiled scene capability contract",
            f"- `LessonBase` MUST use this exact signature: `{self.lesson_base_signature}`",
            "- This signature is a structural requirement, not an illustrative example.",
        ]
        if self.requires_3d:
            lines.extend(
                [
                    "- Planner-selected 3D representation capability is enabled.",
                    "- Use ThreeDScene camera APIs; do not use `self.camera.frame`.",
                ]
            )
        elif self.requires_moving_camera:
            lines.extend(
                [
                    "- Moving-camera capability is enabled for this run.",
                    "- `self.camera.frame` is valid only because `MovingCameraScene` is in the compiled base list.",
                ]
            )
        else:
            lines.append(
                "- Moving-camera capability is not enabled; do not use `self.camera.frame`."
            )
        return "\n".join(lines)


def compile_scene_capability_contract(
    reference_ids: Iterable[str],
    teaching_plan: Mapping[str, object] | None = None,
) -> SceneCapabilityContract:
    ordered_ids = tuple(
        dict.fromkeys(
            str(item).strip() for item in reference_ids if str(item).strip()
        )
    )
    representation_plans = _representation_plans(teaching_plan)
    requires_3d = any(
        str(plan.get("dimension", "")).strip().lower() in {"3d", "mixed"}
        for plan in representation_plans
    )
    planned_camera_intents = {
        str(plan.get("camera_intent", "")).strip().lower()
        for plan in representation_plans
    }
    requires_moving_camera = not requires_3d and (
        bool(planned_camera_intents & {"inspect", "pan", "follow"})
        or (
            not representation_plans
            and MOVING_CAMERA_REFERENCE_ID in ordered_ids
        )
    )
    return SceneCapabilityContract(
        reference_ids=ordered_ids,
        lesson_base_bases=(
            THREE_D_LESSON_BASES
            if requires_3d
            else MOVING_CAMERA_LESSON_BASES
            if requires_moving_camera
            else DEFAULT_LESSON_BASES
        ),
        requires_moving_camera=requires_moving_camera,
        requires_3d=requires_3d,
    )


def validate_scene_capability_contract(
    code: str,
    contract: SceneCapabilityContract,
) -> list[str]:
    """Return structural capability mismatches found through Python AST."""

    try:
        module = ast.parse(code)
    except SyntaxError:
        return []

    lesson_base = next(
        (
            node
            for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "LessonBase"
        ),
        None,
    )
    uses_camera_frame = _uses_self_camera_frame(module)
    uses_3d = _uses_3d_constructs(module)
    expected_bases = contract.lesson_base_bases

    if lesson_base is None:
        return ["Compiled scene capabilities require a `LessonBase` class."]

    issues: list[str] = []
    if uses_3d and not contract.requires_3d:
        issues.append(
            "3D capability was not selected; remove ThreeDScene-only constructs and APIs."
        )
    if uses_camera_frame and not contract.requires_moving_camera:
        issues.append(
            "Moving-camera capability was not selected; remove `self.camera.frame` usage."
        )
    if contract.requires_3d and uses_camera_frame:
        issues.append(
            "3D capability uses `ThreeDScene` camera APIs; do not use `self.camera.frame`."
        )

    actual_bases = tuple(_expr_terminal_name(base) for base in lesson_base.bases)
    if actual_bases != expected_bases:
        reason = (
            "Planner selected a 3D representation"
            if contract.requires_3d
            else "moving-camera capability was selected"
            if contract.requires_moving_camera
            else "the compiled default capability contract applies"
        )
        expected_signature = f"class LessonBase({', '.join(expected_bases)}):"
        actual_signature = (
            f"class LessonBase({', '.join(actual_bases)}):"
            if actual_bases
            else "class LessonBase:"
        )
        issues.append(
            f"{reason}; use exact signature `{expected_signature}`, got `{actual_signature}`."
        )
    return issues


def _representation_plans(
    teaching_plan: Mapping[str, object] | None,
) -> list[Mapping[str, object]]:
    if not isinstance(teaching_plan, Mapping):
        return []
    sections = teaching_plan.get("sections")
    if not isinstance(sections, list):
        return []
    plans: list[Mapping[str, object]] = []
    for section in sections:
        if not isinstance(section, Mapping):
            continue
        plan = section.get("representation_plan")
        if isinstance(plan, Mapping):
            plans.append(plan)
    return plans


def _uses_self_camera_frame(module: ast.Module) -> bool:
    self_aliases, camera_aliases = _scene_and_camera_aliases(module)
    for node in ast.walk(module):
        if not isinstance(node, ast.Attribute) or node.attr != "frame":
            continue
        camera = node.value
        if isinstance(camera, ast.Name) and camera.id in camera_aliases:
            return True
        if (
            isinstance(camera, ast.Attribute)
            and camera.attr == "camera"
            and isinstance(camera.value, ast.Name)
            and camera.value.id in self_aliases
        ):
            return True
    return False


def _scene_and_camera_aliases(module: ast.Module) -> tuple[set[str], set[str]]:
    self_aliases = {"self"}
    camera_aliases: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(module):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Name) and value.id in self_aliases:
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in self_aliases:
                        self_aliases.add(target.id)
                        changed = True
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "camera"
                and isinstance(value.value, ast.Name)
                and value.value.id in self_aliases
            ):
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in camera_aliases:
                        camera_aliases.add(target.id)
                        changed = True
    return self_aliases, camera_aliases


def _uses_3d_constructs(module: ast.Module) -> bool:
    module_aliases = {"manim"}
    imported_3d_aliases = set(THREE_D_MOBJECT_NAMES) | {"ThreeDScene"}
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "manim":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "manim":
            for alias in node.names:
                if alias.name in THREE_D_MOBJECT_NAMES or alias.name == "ThreeDScene":
                    imported_3d_aliases.add(alias.asname or alias.name)

    for node in ast.walk(module):
        if isinstance(node, ast.Name) and node.id in imported_3d_aliases:
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr in (THREE_D_MOBJECT_NAMES | {"ThreeDScene"})
            and isinstance(node.value, ast.Name)
            and node.value.id in module_aliases
        ):
            return True

    self_aliases, _ = _scene_and_camera_aliases(module)
    for node in ast.walk(module):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in THREE_D_SCENE_ONLY_APIS:
            continue
        receiver = node.func.value
        if isinstance(receiver, ast.Name) and receiver.id in self_aliases:
            return True
        if (
            isinstance(receiver, ast.Call)
            and isinstance(receiver.func, ast.Name)
            and receiver.func.id == "super"
        ):
            return True
    return False


def _expr_terminal_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


__all__ = [
    "SceneCapabilityContract",
    "compile_scene_capability_contract",
    "validate_scene_capability_contract",
]
