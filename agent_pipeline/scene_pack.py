"""Deterministic AST parsing and validation for Scene Pack code."""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass

SCENE_MANIFEST_NAME = "SCENE_MANIFEST"
LESSON_BASE_NAME = "LessonBase"
ALLOWED_WRAPPER_BASES = {"LessonBase", "AI4LearningBaseScene"}
SAFE_SEGMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
_FORBIDDEN_IMPORT_ROOTS = {
    "asyncio",
    "builtins",
    "ctypes",
    "ftplib",
    "glob",
    "importlib",
    "marshal",
    "multiprocessing",
    "os",
    "paramiko",
    "pathlib",
    "pickle",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "sys",
    "threading",
    "urllib",
}
_FORBIDDEN_CALL_NAMES = {
    "__import__",
    "compile",
    "eval",
    "exec",
    "input",
    "open",
}
_FORBIDDEN_ATTRIBUTE_CALL_ROOTS = {
    "os",
    "pathlib",
    "subprocess",
}


@dataclass(frozen=True)
class SegmentSpec:
    segment_id: str
    scene_name: str
    method_name: str
    order: int
    lineno: int | None = None


@dataclass(frozen=True)
class WrapperSceneSpec:
    scene_name: str
    bases: tuple[str, ...]
    method_name: str
    lineno: int
    construct_lineno: int


@dataclass
class ScenePackSpec:
    manifest: list[SegmentSpec]
    wrapper_scenes: dict[str, WrapperSceneSpec]
    lesson_base_name: str | None
    section_method_owners: dict[str, str]


@dataclass(frozen=True)
class SectionReadinessSpec:
    segment: SegmentSpec
    owner_class: str | None
    wrapper_scene_present: bool
    wrapper_construct_valid: bool
    section_method_present: bool
    section_method_complete: bool
    section_method_sealed: bool
    probe_source_syntax_ok: bool
    ready: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class MethodSnippet:
    owner_class: str
    method_name: str
    source: str
    lineno: int
    end_lineno: int


@dataclass(frozen=True)
class SegmentRepairContext:
    segment: SegmentSpec
    lesson_base_name: str | None
    wrapper_scene_name: str
    section_owner_class: str
    manifest_source: str
    wrapper_scene_source: str
    section_method: MethodSnippet
    helper_methods: list[MethodSnippet]


def extract_manifest_order(code: str) -> list[SegmentSpec]:
    """Return manifest entries in playback order.

    Raises:
        ValueError: If the code does not contain a valid literal SCENE_MANIFEST.
    """
    module = _parse_module(code)
    segments, errors = _extract_manifest_segments(module)
    if errors:
        raise ValueError(_format_errors(errors))
    return segments


def validate_scene_pack(code: str) -> list[str]:
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


def inspect_section_readiness(code: str) -> list[SectionReadinessSpec]:
    """Return per-section readiness details for a syntactically valid Scene Pack module.

    A section is considered ready when:
    - its manifest entry exists,
    - its wrapper scene exists,
    - the wrapper `construct()` directly calls the expected section method,
    - the section method exists on the wrapper scene or `LessonBase`,
    - and an in-memory probe module built from the relevant snippets parses.
    """
    module = _parse_module(code)
    class_map, _ = _collect_class_defs(module)
    method_map, _ = _collect_class_methods(class_map)
    segments, _ = _extract_manifest_segments(module)
    manifest_source = _manifest_source(code, module)
    lesson_base_name = LESSON_BASE_NAME if LESSON_BASE_NAME in class_map else None

    reports: list[SectionReadinessSpec] = []
    for segment in segments:
        reasons: list[str] = []
        wrapper_present = segment.scene_name in class_map
        if not wrapper_present:
            reasons.append("wrapper_scene_missing")

        owner_class = _resolve_segment_owner(
            segment=segment,
            lesson_base_name=lesson_base_name,
            method_map=method_map,
        )
        section_method_present = owner_class is not None
        section_method_complete = False
        section_method_sealed = False
        if not section_method_present:
            reasons.append("section_method_missing")
        else:
            section_def = method_map.get(owner_class, {}).get(segment.method_name)
            if section_def is not None:
                section_method_complete = _section_method_looks_complete(section_def)
                section_method_sealed = _section_method_is_sealed(
                    segment=segment,
                    owner_class=owner_class,
                    lesson_base_name=lesson_base_name,
                    method_map=method_map,
                    class_map=class_map,
                    manifest=segments,
                )
            if not section_method_complete:
                reasons.append("section_method_incomplete")
            if not section_method_sealed:
                reasons.append("section_method_unsealed")

        wrapper_construct_valid = False
        class_def = class_map.get(segment.scene_name)
        construct_def = method_map.get(segment.scene_name, {}).get("construct")
        if class_def is not None and construct_def is not None:
            called_method, construct_errors = _validate_wrapper_construct(
                scene_name=segment.scene_name,
                construct_def=construct_def,
                expected_method=segment.method_name,
            )
            wrapper_construct_valid = (
                called_method == segment.method_name and not construct_errors
            )
            if not wrapper_construct_valid:
                reasons.append("wrapper_construct_invalid")
        elif class_def is not None:
            reasons.append("wrapper_construct_missing")
        elif owner_class is not None and lesson_base_name is not None:
            # Wrapper classes are commonly emitted after all LessonBase methods.
            # If manifest + method are already available, we can synthesize the
            # trivial wrapper later instead of blocking readiness here.
            wrapper_construct_valid = True
            reasons.append("wrapper_scene_synthesized")

        probe_source_syntax_ok = False
        if (
            manifest_source
            and owner_class is not None
        ):
            probe_source = _build_section_probe_source(
                code=code,
                manifest_source=manifest_source,
                class_map=class_map,
                method_map=method_map,
                lesson_base_name=lesson_base_name,
                segment=segment,
                owner_class=owner_class,
            )
            probe_source_syntax_ok = _module_syntax_ok(probe_source)
            if not probe_source_syntax_ok:
                reasons.append("probe_source_syntax_invalid")

        ready = (
            section_method_present
            and section_method_complete
            and section_method_sealed
            and wrapper_construct_valid
            and probe_source_syntax_ok
        )
        reports.append(
            SectionReadinessSpec(
                segment=segment,
                owner_class=owner_class,
                wrapper_scene_present=wrapper_present,
                wrapper_construct_valid=wrapper_construct_valid,
                section_method_present=section_method_present,
                section_method_complete=section_method_complete,
                section_method_sealed=section_method_sealed,
                probe_source_syntax_ok=probe_source_syntax_ok,
                ready=ready,
                reasons=tuple(reasons),
            )
        )
    return reports


def build_segment_repair_context(code: str, segment_id: str) -> SegmentRepairContext:
    module = _parse_module(code)
    spec, errors = _build_scene_pack_spec(module)
    if errors or spec is None:
        raise ValueError(_format_errors(errors))

    segment = next((item for item in spec.manifest if item.segment_id == segment_id), None)
    if segment is None:
        raise ValueError(f"Unknown segment id `{segment_id}`.")

    class_map, _ = _collect_class_defs(module)
    method_map, _ = _collect_class_methods(class_map)
    owner_class = spec.section_method_owners.get(segment.method_name)
    if not owner_class:
        raise ValueError(
            f"Could not resolve owner for segment method `{segment.method_name}`."
        )

    section_def = method_map.get(owner_class, {}).get(segment.method_name)
    if section_def is None:
        raise ValueError(
            f"Missing section method `{owner_class}.{segment.method_name}`."
        )
    if not _section_method_looks_complete(section_def):
        raise ValueError(
            f"Section method `{owner_class}.{segment.method_name}` is not complete enough yet."
        )
    if not _section_method_is_sealed(
        segment=segment,
        owner_class=owner_class,
        lesson_base_name=spec.lesson_base_name,
        method_map=method_map,
        class_map=class_map,
        manifest=spec.manifest,
    ):
        raise ValueError(
            f"Section method `{owner_class}.{segment.method_name}` is not sealed yet."
        )

    wrapper_class = class_map.get(segment.scene_name)
    wrapper_scene_source = (
        _node_source(code, wrapper_class)
        if wrapper_class is not None
        else _synthesized_wrapper_scene_source(
            segment=segment,
            lesson_base_name=spec.lesson_base_name or LESSON_BASE_NAME,
        )
    )

    helper_methods = _collect_segment_helper_methods(
        code=code,
        spec=spec,
        method_map=method_map,
        owner_class=owner_class,
        section_name=segment.method_name,
    )

    return SegmentRepairContext(
        segment=segment,
        lesson_base_name=spec.lesson_base_name,
        wrapper_scene_name=segment.scene_name,
        section_owner_class=owner_class,
        manifest_source=_manifest_source(code, module),
        wrapper_scene_source=wrapper_scene_source,
        section_method=MethodSnippet(
            owner_class=owner_class,
            method_name=segment.method_name,
            source=_node_source(code, section_def),
            lineno=section_def.lineno,
            end_lineno=section_def.end_lineno or section_def.lineno,
        ),
        helper_methods=helper_methods,
    )


def build_segment_scene_source(code: str, segment_id: str) -> str:
    """Build a minimal single-segment Scene Pack source for syntax/render dry-runs."""
    module = _parse_module(code)
    class_map, _ = _collect_class_defs(module)
    method_map, _ = _collect_class_methods(class_map)
    segments, manifest_errors = _extract_manifest_segments(module)
    if manifest_errors:
        raise ValueError(_format_errors(manifest_errors))
    segment = next((item for item in segments if item.segment_id == segment_id), None)
    if segment is None:
        raise ValueError(f"Unknown segment id `{segment_id}`.")
    lesson_base_name = LESSON_BASE_NAME if LESSON_BASE_NAME in class_map else None
    owner_class = _resolve_segment_owner(
        segment=segment,
        lesson_base_name=lesson_base_name,
        method_map=method_map,
    )
    if owner_class is None:
        raise ValueError(
            f"Missing section method `{segment.method_name}` for segment `{segment.segment_id}`."
        )
    pseudo_spec = ScenePackSpec(
        manifest=segments,
        wrapper_scenes={},
        lesson_base_name=lesson_base_name,
        section_method_owners={
            item.method_name: _resolve_segment_owner(
                segment=item,
                lesson_base_name=lesson_base_name,
                method_map=method_map,
            ) or ""
            for item in segments
        },
    )
    section_def = method_map.get(owner_class, {}).get(segment.method_name)
    if section_def is None:
        raise ValueError(
            f"Missing section method `{owner_class}.{segment.method_name}`."
        )
    if not _section_method_looks_complete(section_def):
        raise ValueError(
            f"Section method `{owner_class}.{segment.method_name}` is not complete enough yet."
        )
    if not _section_method_is_sealed(
        segment=segment,
        owner_class=owner_class,
        lesson_base_name=lesson_base_name,
        method_map=method_map,
        class_map=class_map,
        manifest=segments,
    ):
        raise ValueError(
            f"Section method `{owner_class}.{segment.method_name}` is not sealed yet."
        )
    helper_methods = _collect_segment_helper_methods(
        code=code,
        spec=pseudo_spec,
        method_map=method_map,
        owner_class=owner_class,
        section_name=segment.method_name,
    )

    parts: list[str] = []
    preamble = _scene_pack_preamble_source(code, module)
    if preamble:
        parts.append(preamble)

    parts.append(_single_segment_manifest_source(segment))

    emitted_classes: set[str] = set()

    if lesson_base_name and lesson_base_name in class_map:
        lesson_method_names = _lesson_base_supporting_method_names(
            class_def=class_map[lesson_base_name],
            target_method_name=segment.method_name,
            manifest_method_names={item.method_name for item in segments},
        )
        parts.append(
            _minimal_class_source(
                code=code,
                class_def=class_map[lesson_base_name],
                selected_method_names=lesson_method_names,
            )
        )
        emitted_classes.add(lesson_base_name)

    if owner_class not in emitted_classes and owner_class in class_map:
        parts.append(
            _minimal_class_source(
                code=code,
                class_def=class_map[owner_class],
                selected_method_names={
                    item.method_name
                    for item in [
                        MethodSnippet(
                            owner_class=owner_class,
                            method_name=segment.method_name,
                            source=_node_source(code, section_def),
                            lineno=section_def.lineno,
                            end_lineno=section_def.end_lineno or section_def.lineno,
                        ),
                        *helper_methods,
                    ]
                    if item.owner_class == owner_class
                },
            )
        )
        emitted_classes.add(owner_class)

    if segment.scene_name in class_map:
        parts.append(
            _minimal_class_source(
                code=code,
                class_def=class_map[segment.scene_name],
                selected_method_names={"construct"},
            )
        )
    else:
        parts.append(
            _synthesized_wrapper_scene_source(
                segment=segment,
                lesson_base_name=lesson_base_name or LESSON_BASE_NAME,
            )
        )

    return "\n\n".join(part for part in parts if part.strip()).strip() + "\n"


def replace_method_source(
    code: str,
    *,
    owner_class: str,
    method_name: str,
    new_method_source: str,
) -> str:
    module = _parse_module(code)
    class_map, _ = _collect_class_defs(module)
    method_map, _ = _collect_class_methods(class_map)
    method_def = method_map.get(owner_class, {}).get(method_name)
    if method_def is None:
        raise ValueError(f"Missing method `{owner_class}.{method_name}`.")

    lines = code.splitlines(keepends=True)
    start_lineno = min(
        [decorator.lineno for decorator in method_def.decorator_list] or [method_def.lineno]
    )
    end_lineno = method_def.end_lineno or method_def.lineno
    start_index = start_lineno - 1
    end_index = end_lineno
    indent = _line_indent(lines[start_index]) if lines else "    "
    replacement = textwrap.indent(
        _normalize_method_block(new_method_source) + "\n",
        indent,
    )
    return "".join(lines[:start_index] + [replacement] + lines[end_index:])


def _parse_module(code: str) -> ast.Module:
    try:
        return ast.parse(code)
    except SyntaxError as exc:
        line_info = f"line {exc.lineno}, column {exc.offset}" if exc.lineno else "unknown location"
        raise ValueError(f"Python syntax invalid: {exc.msg} ({line_info})") from exc


def _build_scene_pack_spec(module: ast.Module) -> tuple[ScenePackSpec | None, list[str]]:
    errors: list[str] = []
    errors.extend(_validate_generated_code_security(module))
    class_map, duplicate_class_errors = _collect_class_defs(module)
    errors.extend(duplicate_class_errors)

    segments, manifest_errors = _extract_manifest_segments(module)
    errors.extend(manifest_errors)

    method_map, duplicate_method_errors = _collect_class_methods(class_map)
    errors.extend(duplicate_method_errors)

    wrapper_scenes: dict[str, WrapperSceneSpec] = {}
    section_method_owners: dict[str, str] = {}
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


def _collect_class_defs(module: ast.Module) -> tuple[dict[str, ast.ClassDef], list[str]]:
    class_map: dict[str, ast.ClassDef] = {}
    errors: list[str] = []
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
    class_map: dict[str, ast.ClassDef],
) -> tuple[dict[str, dict[str, ast.FunctionDef]], list[str]]:
    method_map: dict[str, dict[str, ast.FunctionDef]] = {}
    errors: list[str] = []
    for class_name, class_def in class_map.items():
        methods: dict[str, ast.FunctionDef] = {}
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


def _manifest_source(code: str, module: ast.Module) -> str:
    for node in module.body:
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == SCENE_MANIFEST_NAME
                for target in node.targets
            ):
                return _node_source(code, node)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == SCENE_MANIFEST_NAME:
                return _node_source(code, node)
    return ""


def _scene_pack_preamble_source(code: str, module: ast.Module) -> str:
    parts: list[str] = []
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(_node_source(code, node).strip())
            continue
        if isinstance(node, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == SCENE_MANIFEST_NAME
                for target in node.targets
            ):
                continue
            parts.append(_node_source(code, node).strip())
            continue
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == SCENE_MANIFEST_NAME:
                continue
            parts.append(_node_source(code, node).strip())
    return "\n\n".join(part for part in parts if part)


def _single_segment_manifest_source(segment: SegmentSpec) -> str:
    return (
        f"{SCENE_MANIFEST_NAME} = [\n"
        "    {\n"
        f'        "id": {segment.segment_id!r},\n'
        f'        "scene": {segment.scene_name!r},\n'
        f'        "method": {segment.method_name!r},\n'
        "    },\n"
        "]"
    )


def _extract_manifest_segments(module: ast.Module) -> tuple[list[SegmentSpec], list[str]]:
    manifest_nodes: list[ast.AST] = []
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

    segments: list[SegmentSpec] = []
    errors: list[str] = []
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
        elif not SAFE_SEGMENT_ID_RE.fullmatch(segment_id):
            errors.append(
                f"`{SCENE_MANIFEST_NAME}` entry #{index} has unsafe id `{segment_id}`; "
                "use only letters, numbers, `_`, and `-`."
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


def _validate_generated_code_security(module: ast.Module) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORT_ROOTS:
                    errors.append(
                        f"Forbidden generated-code import `{alias.name}` on line {node.lineno}."
                    )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            root = module_name.split(".", 1)[0]
            if root in _FORBIDDEN_IMPORT_ROOTS:
                errors.append(
                    f"Forbidden generated-code import `{module_name}` on line {node.lineno}."
                )
        elif isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            if not call_name:
                continue
            if call_name in _FORBIDDEN_CALL_NAMES:
                errors.append(
                    f"Forbidden generated-code call `{call_name}()` on line {node.lineno}."
                )
                continue
            root = call_name.split(".", 1)[0]
            if root in _FORBIDDEN_ATTRIBUTE_CALL_ROOTS:
                errors.append(
                    f"Forbidden generated-code call `{call_name}()` on line {node.lineno}."
                )
    return errors


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _dict_string_value(node: ast.Dict, key_name: str) -> str | None:
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
) -> tuple[str | None, list[str]]:
    errors: list[str] = []
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


def _collect_segment_helper_methods(
    *,
    code: str,
    spec: ScenePackSpec,
    method_map: dict[str, dict[str, ast.FunctionDef]],
    owner_class: str,
    section_name: str,
) -> list[MethodSnippet]:
    manifest_method_names = {segment.method_name for segment in spec.manifest}
    search_classes = [owner_class]
    if spec.lesson_base_name and spec.lesson_base_name not in search_classes:
        search_classes.append(spec.lesson_base_name)

    resolved: list[MethodSnippet] = []
    seen: set[tuple[str, str]] = set()

    def _resolve_owner(method_name: str) -> str | None:
        for class_name in search_classes:
            if method_name in method_map.get(class_name, {}):
                return class_name
        return None

    def _walk(owner: str, method_name: str) -> None:
        key = (owner, method_name)
        if key in seen:
            return
        seen.add(key)

        method_def = method_map.get(owner, {}).get(method_name)
        if method_def is None:
            return

        for call_name in _self_calls_anywhere(method_def):
            if call_name == section_name:
                continue
            if call_name in manifest_method_names:
                continue
            helper_owner = _resolve_owner(call_name)
            if helper_owner is None:
                continue
            helper_key = (helper_owner, call_name)
            if helper_key in seen:
                continue
            helper_def = method_map.get(helper_owner, {}).get(call_name)
            if helper_def is None:
                continue
            resolved.append(
                MethodSnippet(
                    owner_class=helper_owner,
                    method_name=call_name,
                    source=_node_source(code, helper_def),
                    lineno=helper_def.lineno,
                    end_lineno=helper_def.end_lineno or helper_def.lineno,
                )
            )
            _walk(helper_owner, call_name)

    _walk(owner_class, section_name)
    resolved.sort(key=lambda item: (item.lineno, item.method_name))
    return resolved


def _segment_method_exists(
    *,
    segment: SegmentSpec,
    lesson_base_name: str | None,
    method_map: dict[str, dict[str, ast.FunctionDef]],
) -> bool:
    if segment.method_name in method_map.get(segment.scene_name, {}):
        return True
    if lesson_base_name and segment.method_name in method_map.get(lesson_base_name, {}):
        return True
    return False


def _resolve_segment_owner(
    *,
    segment: SegmentSpec,
    lesson_base_name: str | None,
    method_map: dict[str, dict[str, ast.FunctionDef]],
) -> str | None:
    if segment.method_name in method_map.get(segment.scene_name, {}):
        return segment.scene_name
    if lesson_base_name and segment.method_name in method_map.get(lesson_base_name, {}):
        return lesson_base_name
    return None


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


def _self_call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Attribute):
        return None
    if not isinstance(node.value, ast.Name) or node.value.id != "self":
        return None
    return node.attr


def _top_level_self_calls(function_def: ast.FunctionDef) -> list[str]:
    call_names: list[str] = []
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


def _self_calls_anywhere(function_def: ast.FunctionDef) -> list[str]:
    call_names: list[str] = []
    for node in ast.walk(function_def):
        if not isinstance(node, ast.Call):
            continue
        call_name = _self_call_name(node.func)
        if call_name:
            call_names.append(call_name)
    return call_names


def _section_method_looks_complete(function_def: ast.FunctionDef) -> bool:
    body = list(function_def.body)
    if body and _is_docstring_stmt(body[0]):
        body = body[1:]
    executable = [stmt for stmt in body if not _is_docstring_stmt(stmt)]
    if not executable:
        return False
    if len(executable) == 1:
        only_stmt = executable[0]
        if isinstance(only_stmt, ast.Expr) and isinstance(only_stmt.value, ast.Name):
            return False
    last_stmt = executable[-1]
    if isinstance(last_stmt, ast.Expr) and isinstance(last_stmt.value, ast.Name):
        return False
    return True


def _section_method_is_sealed(
    *,
    segment: SegmentSpec,
    owner_class: str,
    lesson_base_name: str | None,
    method_map: dict[str, dict[str, ast.FunctionDef]],
    class_map: dict[str, ast.ClassDef],
    manifest: list[SegmentSpec],
) -> bool:
    if segment.scene_name in class_map:
        return True

    try:
        current_index = next(i for i, item in enumerate(manifest) if item.segment_id == segment.segment_id)
    except StopIteration:
        return False

    for later in manifest[current_index + 1 :]:
        later_owner = _resolve_segment_owner(
            segment=later,
            lesson_base_name=lesson_base_name,
            method_map=method_map,
        )
        if later_owner == owner_class and later.method_name in method_map.get(owner_class, {}):
            return True
        if later.scene_name in class_map:
            return True
    return False


def _is_docstring_stmt(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _build_section_probe_source(
    *,
    code: str,
    manifest_source: str,
    class_map: dict[str, ast.ClassDef],
    method_map: dict[str, dict[str, ast.FunctionDef]],
    lesson_base_name: str | None,
    segment: SegmentSpec,
    owner_class: str,
) -> str:
    parts: list[str] = [manifest_source.strip()]

    if lesson_base_name:
        lesson_base = class_map.get(lesson_base_name)
        if lesson_base is not None:
            parts.append(_node_source(code, lesson_base).strip())

    if owner_class != lesson_base_name:
        owner_def = class_map.get(owner_class)
        if owner_def is not None:
            parts.append(_node_source(code, owner_def).strip())

    wrapper_def = class_map.get(segment.scene_name)
    if wrapper_def is not None:
        parts.append(_node_source(code, wrapper_def).strip())
    else:
        parts.append(
            _synthesized_wrapper_scene_source(
                segment=segment,
                lesson_base_name=lesson_base_name or LESSON_BASE_NAME,
            ).strip()
        )

    return "\n\n".join(part for part in parts if part).strip() + "\n"


def recover_scene_pack_skeleton(code: str) -> str | None:
    """Best-effort recovery for partially streamed Scene Pack code.

    If we can parse at least the manifest and rebuild one syntactically valid
    single-segment scene, return that minimal source so downstream codegen can
    keep operating. Otherwise return ``None`` and let callers fall back.
    """
    try:
        segments = extract_manifest_order(code)
    except Exception:
        segments = []
    if segments:
        for segment in segments:
            try:
                recovered = build_segment_scene_source(code, segment.segment_id)
            except Exception:
                continue
            if recovered.strip():
                return recovered

    try:
        module = _parse_module(code)
        recovered = _recover_scene_pack_from_lesson_base(code, module)
    except Exception:
        recovered = None
    if recovered is not None:
        return recovered
    return None


def _recover_scene_pack_from_lesson_base(code: str, module: ast.Module) -> str | None:
    class_map, _ = _collect_class_defs(module)
    lesson_base = class_map.get(LESSON_BASE_NAME)
    if lesson_base is None:
        return None

    segments = _synthesized_segments_from_lesson_base(lesson_base)
    if not segments:
        return None

    parts: list[str] = []
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            parts.append(_node_source(code, node).strip())

    parts.append(_manifest_source_from_segments(segments))
    parts.append(_node_source(code, lesson_base).strip())

    for segment in segments:
        wrapper_def = class_map.get(segment.scene_name)
        if wrapper_def is not None:
            parts.append(_node_source(code, wrapper_def).strip())
        else:
            parts.append(
                _synthesized_wrapper_scene_source(
                    segment=segment,
                    lesson_base_name=LESSON_BASE_NAME,
                ).strip()
            )

    recovered = "\n\n".join(part for part in parts if part).strip() + "\n"
    return recovered if _module_syntax_ok(recovered) else None


def _synthesized_segments_from_lesson_base(class_def: ast.ClassDef) -> list[SegmentSpec]:
    methods: list[str] = []
    for stmt in class_def.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        name = stmt.name
        if name == "opening_page" or name == "closing_page" or name.startswith("section_"):
            methods.append(name)

    segments: list[SegmentSpec] = []
    for index, method_name in enumerate(methods):
        segment_id, scene_label = _segment_identity_from_method(method_name)
        segments.append(
            SegmentSpec(
                segment_id=segment_id,
                scene_name=f"Segment{index:02d}{scene_label}Scene",
                method_name=method_name,
                order=index,
            )
        )
    return segments


def _segment_identity_from_method(method_name: str) -> tuple[str, str]:
    if method_name == "opening_page":
        return "opening", "Opening"
    if method_name == "closing_page":
        return "closing", "Closing"

    stem = method_name.removeprefix("section_").strip("_") or method_name
    words = [word for word in stem.split("_") if word]
    scene_label = "".join(word.capitalize() for word in words) or "Section"
    return stem, scene_label


def _manifest_source_from_segments(segments: list[SegmentSpec]) -> str:
    lines = [f"{SCENE_MANIFEST_NAME} = ["]
    for segment in segments:
        lines.append("    {")
        lines.append(f'        "id": {segment.segment_id!r},')
        lines.append(f'        "scene": {segment.scene_name!r},')
        lines.append(f'        "method": {segment.method_name!r},')
        lines.append("    },")
    lines.append("]")
    return "\n".join(lines)


def _synthesized_wrapper_scene_source(
    *,
    segment: SegmentSpec,
    lesson_base_name: str,
) -> str:
    return (
        f"class {segment.scene_name}({lesson_base_name}):\n"
        "    def construct(self):\n"
        f"        self.{segment.method_name}()\n"
    )


def _module_syntax_ok(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


def _node_source(code: str, node: ast.AST) -> str:
    snippet = ast.get_source_segment(code, node)
    if snippet is not None:
        return snippet
    lines = code.splitlines()
    start = getattr(node, "lineno", 1) - 1
    end = getattr(node, "end_lineno", getattr(node, "lineno", 1))
    return "\n".join(lines[start:end])


def _line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _class_member_source(code: str, node: ast.AST) -> str:
    lines = code.splitlines()
    start_lineno = getattr(node, "lineno", 1)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.decorator_list:
        start_lineno = min(
            [getattr(decorator, "lineno", start_lineno) for decorator in node.decorator_list]
        )
    end_lineno = getattr(node, "end_lineno", start_lineno)
    snippet = "\n".join(lines[start_lineno - 1 : end_lineno])
    if not snippet.strip():
        snippet = _node_source(code, node)
    return textwrap.dedent(snippet).strip()


def _minimal_class_source(
    *,
    code: str,
    class_def: ast.ClassDef,
    selected_method_names: set[str],
) -> str:
    header = f"class {class_def.name}"
    bases = [_expr_name(base) or _node_source(code, base).strip() for base in class_def.bases]
    if bases:
        header += f"({', '.join(bases)})"
    header += ":"

    body_lines: list[str] = []
    for stmt in class_def.body:
        if isinstance(stmt, ast.FunctionDef):
            if stmt.name in selected_method_names:
                body_lines.append(textwrap.indent(_class_member_source(code, stmt), "    "))
            continue
        if isinstance(stmt, ast.AsyncFunctionDef):
            if stmt.name in selected_method_names:
                body_lines.append(textwrap.indent(_class_member_source(code, stmt), "    "))
            continue
        if isinstance(stmt, ast.Pass):
            continue
        if _is_docstring_stmt(stmt) or isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            body_lines.append(textwrap.indent(_class_member_source(code, stmt), "    "))

    if not body_lines:
        body_lines.append("    pass")
    return "\n".join([header, *body_lines])


def _lesson_base_supporting_method_names(
    *,
    class_def: ast.ClassDef,
    target_method_name: str,
    manifest_method_names: set[str],
) -> set[str]:
    names: set[str] = set()
    for stmt in class_def.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        if stmt.name == target_method_name:
            names.add(stmt.name)
            break
        if stmt.name in manifest_method_names:
            continue
        names.add(stmt.name)
    return names


def _normalize_method_block(source: str) -> str:
    lines = textwrap.dedent(source).strip("\n").splitlines()
    if not lines:
        return ""

    normalized = [lines[0].lstrip()]
    body_indents = [
        len(line) - len(line.lstrip(" \t"))
        for line in lines[1:]
        if line.strip()
    ]
    body_base = min(body_indents) if body_indents else 4

    for line in lines[1:]:
        if not line.strip():
            normalized.append("")
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        relative = max(indent - body_base, 0)
        normalized.append(" " * (4 + relative) + line.lstrip(" \t"))
    return "\n".join(normalized)


def _format_errors(errors: list[str]) -> str:
    if not errors:
        return "Invalid Scene Pack."
    return "Invalid Scene Pack:\n- " + "\n- ".join(errors)


def extract_speak_texts_for_segment(
    code: str,
    segment: SegmentSpec,
    spec: ScenePackSpec,
) -> list[str]:
    """Return literal strings passed to ``speak`` / ``speak_with_subtitle`` in the section method."""
    module = _parse_module(code)
    class_map, _ = _collect_class_defs(module)
    method_map, _ = _collect_class_methods(class_map)
    owner = spec.section_method_owners.get(segment.method_name)
    if not owner:
        return []
    method_def = method_map.get(owner, {}).get(segment.method_name)
    if method_def is None:
        return []
    texts: list[str] = []
    for node in ast.walk(method_def):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"speak", "speak_with_subtitle"}:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            texts.append(first_arg.value)
    return texts


__all__ = [
    "SegmentSpec",
    "WrapperSceneSpec",
    "ScenePackSpec",
    "SectionReadinessSpec",
    "MethodSnippet",
    "SegmentRepairContext",
    "extract_manifest_order",
    "validate_scene_pack",
    "parse_scene_pack",
    "recover_scene_pack_skeleton",
    "inspect_section_readiness",
    "build_segment_repair_context",
    "build_segment_scene_source",
    "replace_method_source",
    "extract_speak_texts_for_segment",
]
