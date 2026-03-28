"""Deterministic AST parsing and validation for Scene Pack code."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Dict, List, Optional


SCENE_MANIFEST_NAME = "SCENE_MANIFEST"
LESSON_BASE_NAME = "LessonBase"
ALLOWED_WRAPPER_BASES = {"LessonBase", "AI4LearningBaseScene"}


@dataclass(frozen=True)
class SegmentSpec:
    segment_id: str
    scene_name: str
    method_name: str
    order: int
    lineno: Optional[int] = None


@dataclass(frozen=True)
class WrapperSceneSpec:
    scene_name: str
    bases: tuple[str, ...]
    method_name: str
    lineno: int
    construct_lineno: int


@dataclass
class ScenePackSpec:
    manifest: List[SegmentSpec]
    wrapper_scenes: Dict[str, WrapperSceneSpec]
    lesson_base_name: Optional[str]
    section_method_owners: Dict[str, str]


def extract_manifest_order(code: str) -> List[SegmentSpec]:
    """Return manifest entries in playback order.

    Raises:
        ValueError: If the code does not contain a valid literal SCENE_MANIFEST.
    """
    module = _parse_module(code)
    segments, errors = _extract_manifest_segments(module)
    if errors:
        raise ValueError(_format_errors(errors))
    return segments


def validate_scene_pack(code: str) -> List[str]:
    """Return deterministic Scene Pack validation errors."""
    try:
        module = _parse_module(code)
    except ValueError as exc:
        return [str(exc)]

    _, errors = _build_scene_pack_spec(module)
    return errors


def parse_scene_pack(code: str) -> ScenePackSpec:
    """Parse Scene Pack code into a structured spec.

    Raises:
        ValueError: If the code fails any deterministic Scene Pack validation.
    """
    module = _parse_module(code)
    spec, errors = _build_scene_pack_spec(module)
    if errors or spec is None:
        raise ValueError(_format_errors(errors))
    return spec


def _parse_module(code: str) -> ast.Module:
    try:
        return ast.parse(code)
    except SyntaxError as exc:
        line_info = f"line {exc.lineno}, column {exc.offset}" if exc.lineno else "unknown location"
        raise ValueError(f"Python syntax invalid: {exc.msg} ({line_info})") from exc


def _build_scene_pack_spec(module: ast.Module) -> tuple[Optional[ScenePackSpec], List[str]]:
    errors: List[str] = []
    class_map, duplicate_class_errors = _collect_class_defs(module)
    errors.extend(duplicate_class_errors)

    segments, manifest_errors = _extract_manifest_segments(module)
    errors.extend(manifest_errors)

    method_map, duplicate_method_errors = _collect_class_methods(class_map)
    errors.extend(duplicate_method_errors)

    wrapper_scenes: Dict[str, WrapperSceneSpec] = {}
    section_method_owners: Dict[str, str] = {}
    lesson_base_name = LESSON_BASE_NAME if LESSON_BASE_NAME in class_map else None

    for owner_name, methods in method_map.items():
        for method_name in methods:
            if method_name == "construct":
                continue
            section_method_owners.setdefault(method_name, owner_name)

    for segment in segments:
        class_def = class_map.get(segment.scene_name)
        if class_def is None:
            errors.append(
                f"Manifest entry #{segment.order} references scene `{segment.scene_name}`, "
                "but that class does not exist."
            )
            continue

        base_names = tuple(_expr_name(base) for base in class_def.bases)
        if not any(_is_allowed_wrapper_base(base_name) for base_name in base_names):
            rendered_bases = ", ".join(base_names) if base_names else "<none>"
            errors.append(
                f"Wrapper scene `{segment.scene_name}` must inherit `LessonBase` or "
                f"`AI4LearningBaseScene`; found bases: {rendered_bases}."
            )

        construct_def = method_map.get(segment.scene_name, {}).get("construct")
        if construct_def is None:
            errors.append(
                f"Wrapper scene `{segment.scene_name}` is missing `construct()`."
            )
            continue

        called_method, construct_errors = _validate_wrapper_construct(
            scene_name=segment.scene_name,
            construct_def=construct_def,
            expected_method=segment.method_name,
        )
        errors.extend(construct_errors)

        if called_method:
            wrapper_scenes[segment.scene_name] = WrapperSceneSpec(
                scene_name=segment.scene_name,
                bases=base_names,
                method_name=called_method,
                lineno=class_def.lineno,
                construct_lineno=construct_def.lineno,
            )

        if not _segment_method_exists(
            segment=segment,
            lesson_base_name=lesson_base_name,
            method_map=method_map,
        ):
            errors.append(
                f"Manifest entry #{segment.order} references method `{segment.method_name}`, "
                f"but that method does not exist on `{segment.scene_name}` or `{LESSON_BASE_NAME}`."
            )

    manifest_scene_names = {segment.scene_name for segment in segments}
    for class_name, class_def in class_map.items():
        if class_name in manifest_scene_names:
            continue
        if not _class_looks_renderable(class_def):
            continue
        construct_def = method_map.get(class_name, {}).get("construct")
        if construct_def is None:
            continue
        allowed_method_names = {
            name for name in method_map.get(class_name, {}) if name != "construct"
        }
        if lesson_base_name:
            allowed_method_names.update(
                name for name in method_map.get(lesson_base_name, {}) if name != "construct"
            )
        serial_calls = [
            call_name
            for call_name in _top_level_self_calls(construct_def)
            if call_name in allowed_method_names
        ]
        if len(serial_calls) > 1:
            pretty_calls = ", ".join(serial_calls)
            errors.append(
                f"Forbidden master serial construct in `{class_name}.construct()` on line "
                f"{construct_def.lineno}: multiple section-style self-calls found "
                f"({pretty_calls})."
            )

    spec = ScenePackSpec(
        manifest=segments,
        wrapper_scenes=wrapper_scenes,
        lesson_base_name=lesson_base_name,
        section_method_owners=section_method_owners,
    )
    return spec, errors


def _collect_class_defs(module: ast.Module) -> tuple[Dict[str, ast.ClassDef], List[str]]:
    class_map: Dict[str, ast.ClassDef] = {}
    errors: List[str] = []
    for node in module.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name in class_map:
            errors.append(
                f"Duplicate class definition for `{node.name}` (line {node.lineno})."
            )
            continue
        class_map[node.name] = node
    return class_map, errors


def _collect_class_methods(
    class_map: Dict[str, ast.ClassDef],
) -> tuple[Dict[str, Dict[str, ast.FunctionDef]], List[str]]:
    method_map: Dict[str, Dict[str, ast.FunctionDef]] = {}
    errors: List[str] = []
    for class_name, class_def in class_map.items():
        methods: Dict[str, ast.FunctionDef] = {}
        for stmt in class_def.body:
            if not isinstance(stmt, ast.FunctionDef):
                continue
            if stmt.name in methods:
                errors.append(
                    f"Duplicate method `{class_name}.{stmt.name}` (line {stmt.lineno})."
                )
                continue
            methods[stmt.name] = stmt
        method_map[class_name] = methods
    return method_map, errors


def _extract_manifest_segments(module: ast.Module) -> tuple[List[SegmentSpec], List[str]]:
    manifest_nodes: List[ast.AST] = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == SCENE_MANIFEST_NAME for target in node.targets):
                manifest_nodes.append(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == SCENE_MANIFEST_NAME:
                manifest_nodes.append(node.value)

    if not manifest_nodes:
        return [], [f"`{SCENE_MANIFEST_NAME}` is missing."]
    if len(manifest_nodes) > 1:
        return [], [f"`{SCENE_MANIFEST_NAME}` must be defined exactly once."]

    manifest_value = manifest_nodes[0]
    if not isinstance(manifest_value, ast.List):
        return [], [f"`{SCENE_MANIFEST_NAME}` must be a literal list so segment order is explicit."]
    if not manifest_value.elts:
        return [], [f"`{SCENE_MANIFEST_NAME}` must not be empty."]

    segments: List[SegmentSpec] = []
    errors: List[str] = []
    seen_ids: set[str] = set()
    seen_scenes: set[str] = set()
    seen_methods: set[str] = set()

    for index, entry in enumerate(manifest_value.elts):
        if not isinstance(entry, ast.Dict):
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} must be a literal dict."
            )
            continue

        segment_id = _dict_string_value(entry, "id")
        scene_name = _dict_string_value(entry, "scene")
        method_name = _dict_string_value(entry, "method")

        if segment_id is None:
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} is missing a non-empty string `id`."
            )
        if scene_name is None:
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} is missing a non-empty string `scene`."
            )
        if method_name is None:
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} is missing a non-empty string `method`."
            )
        if segment_id is None or scene_name is None or method_name is None:
            continue

        if segment_id in seen_ids:
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} reuses duplicate id `{segment_id}`."
            )
        if scene_name in seen_scenes:
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} reuses duplicate scene `{scene_name}`."
            )
        if method_name in seen_methods:
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} reuses duplicate method `{method_name}`."
            )
        seen_ids.add(segment_id)
        seen_scenes.add(scene_name)
        seen_methods.add(method_name)

        segments.append(
            SegmentSpec(
                segment_id=segment_id,
                scene_name=scene_name,
                method_name=method_name,
                order=index,
                lineno=getattr(entry, "lineno", None),
            )
        )

    return segments, errors


def _dict_string_value(node: ast.Dict, key_name: str) -> Optional[str]:
    for key_node, value_node in zip(node.keys, node.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            continue
        if key_node.value != key_name:
            continue
        if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
            value = value_node.value.strip()
            return value or None
        return None
    return None


def _validate_wrapper_construct(
    *,
    scene_name: str,
    construct_def: ast.FunctionDef,
    expected_method: str,
) -> tuple[Optional[str], List[str]]:
    errors: List[str] = []
    body = list(construct_def.body)
    if body and _is_docstring_stmt(body[0]):
        body = body[1:]

    if len(body) != 1:
        errors.append(
            f"`{scene_name}.construct()` must contain exactly one direct `self.<section_method>()` "
            f"call; found {len(body)} executable statement(s)."
        )
        return None, errors

    stmt = body[0]
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        errors.append(
            f"`{scene_name}.construct()` must be a single expression statement calling "
            f"`self.{expected_method}()`."
        )
        return None, errors

    call = stmt.value
    called_method = _self_call_name(call.func)
    if called_method is None:
        errors.append(
            f"`{scene_name}.construct()` must directly call `self.{expected_method}()`."
        )
        return None, errors

    if call.args or call.keywords:
        errors.append(
            f"`{scene_name}.construct()` must call `self.{expected_method}()` without arguments."
        )

    if called_method != expected_method:
        errors.append(
            f"`{scene_name}.construct()` calls `self.{called_method}()`, but the manifest "
            f"expects `self.{expected_method}()`."
        )
    return called_method, errors


def _segment_method_exists(
    *,
    segment: SegmentSpec,
    lesson_base_name: Optional[str],
    method_map: Dict[str, Dict[str, ast.FunctionDef]],
) -> bool:
    if segment.method_name in method_map.get(segment.scene_name, {}):
        return True
    if lesson_base_name and segment.method_name in method_map.get(lesson_base_name, {}):
        return True
    return False


def _class_looks_renderable(class_def: ast.ClassDef) -> bool:
    return any(_is_allowed_wrapper_base(_expr_name(base)) for base in class_def.bases)


def _is_allowed_wrapper_base(base_name: str) -> bool:
    terminal_name = base_name.rsplit(".", 1)[-1]
    return terminal_name in ALLOWED_WRAPPER_BASES


def _expr_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _expr_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _self_call_name(node: ast.AST) -> Optional[str]:
    if not isinstance(node, ast.Attribute):
        return None
    if not isinstance(node.value, ast.Name) or node.value.id != "self":
        return None
    return node.attr


def _top_level_self_calls(function_def: ast.FunctionDef) -> List[str]:
    call_names: List[str] = []
    body = list(function_def.body)
    if body and _is_docstring_stmt(body[0]):
        body = body[1:]
    for stmt in body:
        if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
            continue
        call_name = _self_call_name(stmt.value.func)
        if call_name:
            call_names.append(call_name)
    return call_names


def _is_docstring_stmt(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _format_errors(errors: List[str]) -> str:
    if not errors:
        return "Invalid Scene Pack."
    return "Invalid Scene Pack:\n- " + "\n- ".join(errors)


__all__ = [
    "SegmentSpec",
    "WrapperSceneSpec",
    "ScenePackSpec",
    "extract_manifest_order",
    "validate_scene_pack",
    "parse_scene_pack",
]
