"""Progressive skill loading for the Manim code-generation agents.

The catalog is intentionally package-oriented: keep package metadata available,
let deterministic signals narrow the field, and let an optional LLM router make
semantic choices from metadata only. Full skill/reference text is read only for
the final merged selection.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


SKILLS_DIR = Path(__file__).resolve().parent / "skills"


@dataclass(frozen=True)
class SkillPackage:
    id: str
    path: tuple[str, ...]
    description: str
    stages: tuple[str, ...]


@dataclass(frozen=True)
class SkillReference:
    id: str
    package_id: str
    path: tuple[str, ...]
    tags: tuple[str, ...]
    stages: tuple[str, ...]
    use_when: str


@dataclass(frozen=True)
class RuleSignal:
    reference_id: str
    strength: str
    source: str
    reason: str


@dataclass(frozen=True)
class RouterDecision:
    selected_reference_ids: tuple[str, ...] = ()
    rejected_reference_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    reason: str = ""
    raw_response: str = ""
    error: str = ""


@dataclass(frozen=True)
class SkillSelection:
    stage: str
    reference_ids: tuple[str, ...]
    reason: str
    package_ids: tuple[str, ...] = ()
    reasons: Mapping[str, str] = field(default_factory=dict)
    omitted_reference_ids: tuple[str, ...] = ()
    mandatory_reference_ids: tuple[str, ...] = ()
    strong_reference_ids: tuple[str, ...] = ()
    candidate_reference_ids: tuple[str, ...] = ()
    refused_reference_ids: tuple[str, ...] = ()
    invalid_router_reference_ids: tuple[str, ...] = ()
    rule_signals: tuple[RuleSignal, ...] = ()
    router_decision: RouterDecision = field(default_factory=RouterDecision)
    router_input_summary: Mapping[str, Any] = field(default_factory=dict)


_ALL_STAGES = ("generate", "fix", "segment_fix", "validation_fix", "code_eval_fix", "improve")
_REPAIR_STAGES = ("fix", "segment_fix", "validation_fix", "code_eval_fix", "improve")

_PACKAGE_REGISTRY: tuple[SkillPackage, ...] = (
    SkillPackage(
        id="manim-teaching-flow",
        path=("manim-teaching-flow", "SKILL.md"),
        description="Teacher-like educational flow, reverse knowledge tree, opening choices, problem intake, and reveal/narration discipline.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-scene-pack-core",
        path=("manim-scene-pack-core", "SKILL.md"),
        description="Scene Pack manifest, wrapper scene, shared LessonBase, and repair-preservation contracts.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-runtime-safety",
        path=("manim-runtime-safety", "SKILL.md"),
        description="Manim Community v0.20.1 runtime/API, theme, local assets, Group/VGroup/Create, and callback/deepcopy safety.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-layout-composition",
        path=("manim-layout-composition", "SKILL.md"),
        description="Page/body/title/subtitle layout composition, density, overlap, cards, panels, and boxes.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-coordinate-geometry",
        path=("manim-coordinate-geometry", "SKILL.md"),
        description="Coordinate systems, graph dynamics, anchor lifecycle, plotted functions, and geometry followers.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-annotation-patterns",
        path=("manim-annotation-patterns", "SKILL.md"),
        description="Arrows, connectors, labels, highlights, braces, local marks, and annotation clarity.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-equation-derivation",
        path=("manim-equation-derivation", "SKILL.md"),
        description="Formula focus, equation derivation, MathTex staging, and symbolic part highlighting.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-motion-pacing",
        path=("manim-motion-pacing", "SKILL.md"),
        description="Motion transitions, animation continuity, speak/subtitle pacing, and temporal alignment repair.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-camera-movement",
        path=("manim-camera-movement", "SKILL.md"),
        description="MovingCameraScene, camera.frame, zoom, pan, viewport focus, magnification, and camera movement safety.",
        stages=_ALL_STAGES,
    ),
    SkillPackage(
        id="manim-repair-diagnostics",
        path=("manim-repair-diagnostics", "SKILL.md"),
        description="Render, code-eval, section-validation, and QA improvement repair discipline.",
        stages=_REPAIR_STAGES,
    ),
    SkillPackage(
        id="math-physics-director",
        path=("math-physics-director", "SKILL.md"),
        description="Math/physics visualization director for 2D/3D structure, complementary representations, causality beats, and anti-PPT guidance.",
        stages=("generate", "improve"),
    ),
    SkillPackage(
        id="manim-routing",
        path=("manim-routing", "SKILL.md"),
        description="Hybrid routing guidance for Manim-only sections selected by upstream routing.",
        stages=("generate", "improve"),
    ),
)

_REFERENCE_REGISTRY: tuple[SkillReference, ...] = (
    SkillReference(
        id="scene-pack-generation",
        package_id="manim-scene-pack-core",
        path=("references", "generation-contract.md"),
        tags=("scene-pack", "manifest", "wrapper", "lessonbase", "output"),
        stages=("generate",),
        use_when="Generating a new Scene Pack Python file.",
    ),
    SkillReference(
        id="scene-pack-repair",
        package_id="manim-scene-pack-core",
        path=("references", "repair-preservation.md"),
        tags=("scene-pack", "repair", "manifest", "wrapper", "preserve"),
        stages=_REPAIR_STAGES,
        use_when="Repairing existing Scene Pack code while preserving manifest and wrapper architecture.",
    ),
    SkillReference(
        id="runtime-api-core",
        package_id="manim-runtime-safety",
        path=("references", "api-core.md"),
        tags=("runtime", "api", "manim", "v0.20.1", "axes", "tex", "center"),
        stages=_ALL_STAGES,
        use_when="Any Manim code generation or repair that must avoid target-runtime API failures.",
    ),
    SkillReference(
        id="runtime-theme-assets",
        package_id="manim-runtime-safety",
        path=("references", "theme-assets.md"),
        tags=("theme", "assets", "icons", "colors", "image"),
        stages=_ALL_STAGES,
        use_when="Selected theme or local assets are present, or code touches theme helpers, icons, or ImageMobject.",
    ),
    SkillReference(
        id="runtime-object-safety",
        package_id="manim-runtime-safety",
        path=("references", "object-animation-safety.md"),
        tags=("group", "vgroup", "create", "write", "animation", "mobject"),
        stages=_ALL_STAGES,
        use_when="Code builds mixed containers, panels, icons, groups, or Create/Write animations.",
    ),
    SkillReference(
        id="runtime-language-api",
        package_id="manim-runtime-safety",
        path=("references", "language-api-safety.md"),
        tags=("language", "api", "text", "mathtex", "tex", "chinese", "labels"),
        stages=_ALL_STAGES,
        use_when="The scene includes natural language, MathTex/Tex, axis labels, Chinese text, or target-runtime API-sensitive text.",
    ),
    SkillReference(
        id="runtime-callback-safety",
        package_id="manim-runtime-safety",
        path=("references", "callback-safety.md"),
        tags=("callback", "lambda", "always_redraw", "deepcopy", "pickle", "updater"),
        stages=_ALL_STAGES,
        use_when="Code or request involves graphs, callbacks, updaters, always_redraw, or deepcopy/pickle failures.",
    ),
    SkillReference(
        id="teaching-core",
        package_id="manim-teaching-flow",
        path=("references", "teacher-script.md"),
        tags=("teacher", "teaching", "flow", "opening", "takeaway", "visual-formula"),
        stages=("generate", "improve"),
        use_when="The agent must translate a request/teaching plan into teacher-like animation behavior.",
    ),
    SkillReference(
        id="teaching-generate-contract",
        package_id="manim-teaching-flow",
        path=("references", "generate-pedagogy.md"),
        tags=("generate", "pedagogy", "opening", "teacher", "problem-intake", "teacher_move"),
        stages=("generate",),
        use_when="New generation must follow the original CodeGen pedagogical design, opening architecture, and teacher-like delivery contract.",
    ),
    SkillReference(
        id="reveal-narration",
        package_id="manim-teaching-flow",
        path=("references", "reveal-narration.md"),
        tags=("reveal", "narration", "subtitle", "speak", "teacher", "pacing"),
        stages=("generate", "segment_fix", "validation_fix", "improve"),
        use_when="The section needs progressive reveal, subtitle alignment, or narration beat discipline.",
    ),
    SkillReference(
        id="layout-page-body",
        package_id="manim-layout-composition",
        path=("references", "page-body-layout.md"),
        tags=("layout", "page", "body", "title", "subtitle", "fit_body"),
        stages=_ALL_STAGES,
        use_when="The scene needs page/body/title/subtitle composition or fit_body discipline.",
    ),
    SkillReference(
        id="layout-title-protocol",
        package_id="manim-layout-composition",
        path=("references", "title-protocol.md"),
        tags=("title", "chip", "page-title", "top-band", "persistent-title"),
        stages=_ALL_STAGES,
        use_when="A page uses title chips, top-band titles, or title persistence rules.",
    ),
    SkillReference(
        id="layout-visual-clarity",
        package_id="manim-layout-composition",
        path=("references", "visual-clarity.md"),
        tags=("clarity", "simplicity", "visual", "decorative", "density"),
        stages=_ALL_STAGES,
        use_when="The scene needs visual simplicity, anti-decoration guidance, or clarity-first layout choices.",
    ),
    SkillReference(
        id="layout-generate-rules",
        package_id="manim-layout-composition",
        path=("references", "generate-layout-rules.md"),
        tags=("generate", "layout", "canvas", "vector", "density", "subtitle", "helpers"),
        stages=("generate",),
        use_when="New generation needs the original CodeGen layout, vector diagram, density, helper, and overlap-avoidance rules.",
    ),
    SkillReference(
        id="layout-density-overlap",
        package_id="manim-layout-composition",
        path=("references", "density-overlap.md"),
        tags=("overlap", "crowded", "dense", "truncated", "cutoff", "spacing"),
        stages=_ALL_STAGES,
        use_when="Diagnostics or plan mention overlap, density, truncation, small text, or crowded layout.",
    ),
    SkillReference(
        id="cards-boxes",
        package_id="manim-layout-composition",
        path=("references", "cards-boxes.md"),
        tags=("card", "cards", "box", "boxes", "panel", "panels", "problem-card"),
        stages=_ALL_STAGES,
        use_when="The scene needs problem cards, panels, grouped explanation blocks, or panel layout repair.",
    ),
    SkillReference(
        id="coordinate-systems",
        package_id="manim-coordinate-geometry",
        path=("references", "coordinate-systems.md"),
        tags=("coordinate", "coordinates", "axis", "axes", "graph", "plot", "curve"),
        stages=_ALL_STAGES,
        use_when="The scene needs axes, coordinates, plotted functions, number planes, or graph geometry.",
    ),
    SkillReference(
        id="graph-dynamics",
        package_id="manim-coordinate-geometry",
        path=("references", "graph-dynamics.md"),
        tags=("graph", "curve", "derivative", "integral", "slope", "function", "dynamic"),
        stages=_ALL_STAGES,
        use_when="The scene needs dynamic graph reasoning, slopes, derivatives, integrals, or function motion.",
    ),
    SkillReference(
        id="anchor-lifecycle",
        package_id="manim-coordinate-geometry",
        path=("references", "anchor-lifecycle.md"),
        tags=("anchor", "drift", "detached", "follower", "build_on_anchor", "lifecycle"),
        stages=_ALL_STAGES,
        use_when="Dependent geometry, labels, dots, arrows, regions, or highlights must stay attached to fitted anchors.",
    ),
    SkillReference(
        id="arrows",
        package_id="manim-annotation-patterns",
        path=("references", "arrows.md"),
        tags=("arrow", "arrows", "connector", "connectors", "vector", "force", "brace"),
        stages=_ALL_STAGES,
        use_when="The scene needs arrows, braces, connectors, vectors, force directions, or pointer repair.",
    ),
    SkillReference(
        id="labels",
        package_id="manim-annotation-patterns",
        path=("references", "labels.md"),
        tags=("label", "labels", "annotation", "axis", "point", "symbolic"),
        stages=_ALL_STAGES,
        use_when="The scene needs graph, axis, point, geometry, or symbolic labels.",
    ),
    SkillReference(
        id="highlights",
        package_id="manim-annotation-patterns",
        path=("references", "highlights.md"),
        tags=("highlight", "highlights", "mark", "underline", "circle", "color"),
        stages=_ALL_STAGES,
        use_when="The scene needs visual emphasis, marked problem information, formula/text highlights, or correction moments.",
    ),
    SkillReference(
        id="equation-focus",
        package_id="manim-equation-derivation",
        path=("references", "equation-focus.md"),
        tags=("formula", "formulas", "equation", "equations", "derivation", "symbolic", "mathtex"),
        stages=_ALL_STAGES,
        use_when="The scene needs formula focus, symbolic derivation, equation-part emphasis, or MathTex staging.",
    ),
    SkillReference(
        id="motion-transitions",
        package_id="manim-motion-pacing",
        path=("references", "motion-transitions.md"),
        tags=("motion", "transition", "transitions", "pacing", "animation", "continuity"),
        stages=_ALL_STAGES,
        use_when="The scene needs transition, timing, speak/subtitle pacing, or animation continuity guidance.",
    ),
    SkillReference(
        id="camera-movement",
        package_id="manim-camera-movement",
        path=("references", "camera-movement.md"),
        tags=(
            "camera",
            "zoom",
            "pan",
            "move camera",
            "viewport",
            "focus",
            "magnify",
            "MovingCameraScene",
            "camera.frame",
        ),
        stages=_ALL_STAGES,
        use_when="The scene needs MovingCameraScene, camera.frame, zoom, pan, magnification, close-up focus, or viewport repair.",
    ),
    SkillReference(
        id="repair-render",
        package_id="manim-repair-diagnostics",
        path=("references", "render-fix.md"),
        tags=("fix", "render", "error", "debug", "traceback"),
        stages=("fix",),
        use_when="Full-file render repair after Manim failed to render.",
    ),
    SkillReference(
        id="repair-segment",
        package_id="manim-repair-diagnostics",
        path=("references", "segment-fix.md"),
        tags=("segment", "method", "section", "repair"),
        stages=("segment_fix",),
        use_when="Replacing one failed Scene Pack section method.",
    ),
    SkillReference(
        id="repair-validation",
        package_id="manim-repair-diagnostics",
        path=("references", "validation-fix.md"),
        tags=("validation", "section", "method", "layout", "unsupported_api"),
        stages=("validation_fix",),
        use_when="Section-local validation found blocking issues.",
    ),
    SkillReference(
        id="repair-code-eval",
        package_id="manim-repair-diagnostics",
        path=("references", "code-eval-fix.md"),
        tags=("code_eval", "structure", "body", "anchor", "overlap"),
        stages=("code_eval_fix",),
        use_when="Pre-render code evaluation found structural page problems.",
    ),
    SkillReference(
        id="repair-improve",
        package_id="manim-repair-diagnostics",
        path=("references", "improve.md"),
        tags=("improve", "qa", "keyframe", "quality", "visual_bug"),
        stages=("improve",),
        use_when="QA/keyframe evaluation found visual quality issues after rendering.",
    ),
    SkillReference(
        id="repair-tool-loop",
        package_id="manim-repair-diagnostics",
        path=("references", "tool-loop.md"),
        tags=("tool", "tools", "patch", "apply_patch", "tool_loop", "read_file", "search_file"),
        stages=("fix", "code_eval_fix"),
        use_when="Patch-first tool repair needs read/search/apply_patch workflow and finish_repair guidance.",
    ),
    SkillReference(
        id="math-physics-director",
        package_id="math-physics-director",
        path=("references", "visualization-director.md"),
        tags=("math", "physics", "3d", "vector", "field", "surface", "causality"),
        stages=("generate", "improve"),
        use_when="The lesson is primarily math or physics and needs spatial/dynamic representation decisions.",
    ),
    SkillReference(
        id="routing",
        package_id="manim-routing",
        path=("references", "routing.md"),
        tags=("hybrid", "routing", "backend", "manim_section_ids"),
        stages=("generate", "improve"),
        use_when="Hybrid routing has already selected Manim sections.",
    ),
)

_PACKAGE_BY_ID = {package.id: package for package in _PACKAGE_REGISTRY}
_REFERENCE_BY_ID = {reference.id: reference for reference in _REFERENCE_REGISTRY}
_ALL_REFERENCE_IDS = tuple(reference.id for reference in _REFERENCE_REGISTRY)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=None)
def _skill_text(*parts: str) -> str:
    return _read_text(SKILLS_DIR.joinpath(*parts))


def _normalize_stage(stage: str) -> str:
    value = str(stage or "generate").strip().lower()
    aliases = {
        "segment_validation_fix": "validation_fix",
        "contract_repair": "validation_fix",
    }
    return aliases.get(value, value or "generate")


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _has_any(text: str, *needles: str) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def _structured_plan_text(teaching_plan: Optional[Dict[str, Any]], *keys: str) -> str:
    if not isinstance(teaching_plan, dict):
        return ""
    values: list[Any] = []
    for key in keys:
        value = teaching_plan.get(key)
        if value:
            values.append(value)
    sections = teaching_plan.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict):
                continue
            values.extend(
                section.get(name)
                for name in (
                    "title",
                    "teacher_move",
                    "student_question",
                    "visual_strategy",
                    "board_plan",
                    "key_takeaway",
                    "check_for_understanding",
                    "transition",
                )
                if section.get(name)
            )
    return _flatten_text(values).lower()


def _iter_diagnostic_terms(diagnostics: Any) -> Iterable[str]:
    if diagnostics is None:
        return
    if isinstance(diagnostics, Mapping):
        for key, value in diagnostics.items():
            if key in {"category", "taxonomy", "name", "metric", "summary", "description", "details"}:
                yield str(value)
            yield from _iter_diagnostic_terms(value)
    elif isinstance(diagnostics, Iterable) and not isinstance(diagnostics, (str, bytes, bytearray)):
        for item in diagnostics:
            yield from _iter_diagnostic_terms(item)
    else:
        yield str(diagnostics)


def _scan_code_features(code: Optional[str]) -> Dict[str, Any]:
    source = code or ""
    names: set[str] = set()
    calls: set[str] = set()
    attrs: set[str] = set()
    if source.strip():
        try:
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, ast.Attribute):
                    attrs.add(node.attr)
                elif isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        calls.add(func.id)
                    elif isinstance(func, ast.Attribute):
                        calls.add(func.attr)
                        attrs.add(func.attr)
        except SyntaxError:
            names.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", source))

    tokens = names | calls | attrs
    has_camera_movement = bool(tokens & {"MovingCameraScene", "MovingCamera"}) or (
        "camera" in tokens and "frame" in tokens
    )
    has_object_animation_safety = bool(
        tokens
        & {
            "Group",
            "VGroup",
            "Create",
            "Write",
            "GrowArrow",
            "FadeIn",
            "FadeOut",
            "make_panel",
            "fit_body",
        }
    )
    return {
        "tokens": sorted(tokens),
        "has_axes": bool(tokens & {"Axes", "ThreeDAxes", "NumberPlane", "FunctionGraph"}),
        "has_3d": bool(tokens & {"ThreeDAxes", "ThreeDScene", "Surface", "ParametricFunction"}),
        "has_mathtex": "MathTex" in tokens,
        "has_annotations": bool(tokens & {"Arrow", "CurvedArrow", "DoubleArrow", "Brace", "SurroundingRectangle"}),
        "has_images": bool(tokens & {"ImageMobject", "load_local_icon"}),
        "has_groups": bool(tokens & {"Group", "VGroup", "make_panel", "fit_body"}),
        "has_object_animation_safety": has_object_animation_safety,
        "has_callbacks": bool(
            tokens
            & {
                "always_redraw",
                "add_updater",
                "updater",
                "plot",
                "FunctionGraph",
                "ParametricFunction",
                "deepcopy",
                "pickle",
            }
        ),
        "has_camera_movement": has_camera_movement,
    }


def _reference_stage_compatible(reference_id: str, stage: str) -> bool:
    reference = _REFERENCE_BY_ID.get(reference_id)
    return bool(reference and stage in reference.stages)


def _add_signal(
    buckets: Dict[str, Dict[str, str]],
    signals: list[RuleSignal],
    strength: str,
    reference_id: str,
    source: str,
    reason: str,
    *,
    stage: str,
) -> None:
    if not _reference_stage_compatible(reference_id, stage):
        return
    buckets[strength].setdefault(reference_id, reason)
    signals.append(
        RuleSignal(
            reference_id=reference_id,
            strength=strength,
            source=source,
            reason=reason,
        )
    )


def _package_ids_for(reference_ids: Sequence[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for reference_id in reference_ids:
        reference = _REFERENCE_BY_ID.get(reference_id)
        if reference and reference.package_id not in ordered:
            ordered.append(reference.package_id)
    return tuple(ordered)


def _ordered_unique(values: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for value in values:
        if value not in ordered:
            ordered.append(value)
    return tuple(ordered)


def get_manim_skill_index() -> Dict[str, Any]:
    """Return package/reference metadata without reading reference bodies."""
    return {
        "packages": [
            {
                "id": package.id,
                "path": "/".join(package.path),
                "description": package.description,
                "stages": list(package.stages),
            }
            for package in _PACKAGE_REGISTRY
        ],
        "references": [
            {
                "id": reference.id,
                "package_id": reference.package_id,
                "path": "/".join((reference.package_id, *reference.path)),
                "tags": list(reference.tags),
                "stages": list(reference.stages),
                "use_when": reference.use_when,
            }
            for reference in _REFERENCE_REGISTRY
        ],
    }


def build_router_input_summary(
    *,
    stage: str = "generate",
    request_text: Optional[str] = None,
    teaching_plan: Optional[Dict[str, Any]] = None,
    diagnostics: Any = None,
    code: Optional[str] = None,
) -> Dict[str, Any]:
    compact_plan: Dict[str, Any] = {}
    if isinstance(teaching_plan, dict):
        for key in ("lesson_goal", "big_idea", "problem_intake", "opening", "hybrid_routes", "fast_path"):
            if key in teaching_plan:
                compact_plan[key] = teaching_plan[key]
        sections = teaching_plan.get("sections")
        if isinstance(sections, list):
            compact_plan["sections"] = [
                {
                    name: section.get(name)
                    for name in ("id", "title", "teacher_move", "visual_strategy", "key_takeaway")
                    if isinstance(section, dict) and name in section
                }
                for section in sections[:8]
                if isinstance(section, dict)
            ]
    diagnostic_terms = list(_iter_diagnostic_terms(diagnostics))
    return {
        "stage": _normalize_stage(stage),
        "request_excerpt": (request_text or "")[:800],
        "teaching_plan": compact_plan,
        "diagnostic_terms": diagnostic_terms[:40],
        "code_features": _scan_code_features(code),
    }


def select_manim_references(
    stage: str = "generate",
    teaching_plan: Optional[Dict[str, Any]] = None,
    diagnostics: Any = None,
    code: Optional[str] = None,
    request_text: Optional[str] = None,
) -> SkillSelection:
    """Return the deterministic rule-layer selection before optional LLM routing."""
    normalized_stage = _normalize_stage(stage)
    buckets: Dict[str, Dict[str, str]] = {
        "mandatory": {},
        "strong": {},
        "candidate": {},
    }
    signals: list[RuleSignal] = []

    def add(strength: str, reference_id: str, source: str, reason: str) -> None:
        _add_signal(buckets, signals, strength, reference_id, source, reason, stage=normalized_stage)

    if normalized_stage == "generate":
        add("mandatory", "scene-pack-generation", "stage_baseline", "new generation must produce a Scene Pack")
        add("mandatory", "runtime-api-core", "stage_baseline", "new generation must target Manim Community v0.20.1")
        add("mandatory", "runtime-language-api", "stage_baseline", "new generation must keep Text/MathTex/API usage safe")
        add("mandatory", "runtime-object-safety", "stage_baseline", "new generation must avoid Group/VGroup/Create runtime failures")
        add("strong", "teaching-generate-contract", "stage_baseline", "new generation needs the original pedagogical CodeGen contract")
        add("strong", "layout-generate-rules", "stage_baseline", "new generation needs original layout/vector/density authoring rules")
        add("strong", "layout-title-protocol", "stage_baseline", "new generation needs stable page-title protocol")
        add("strong", "layout-visual-clarity", "stage_baseline", "new generation needs visual clarity guardrails")
    elif normalized_stage in _REPAIR_STAGES:
        add("mandatory", "scene-pack-repair", "stage_baseline", "repair must preserve existing Scene Pack architecture")
        add("mandatory", "runtime-api-core", "stage_baseline", "repair must preserve target runtime compatibility")
        add("mandatory", "runtime-language-api", "stage_baseline", "repair must preserve safe Text/MathTex/API usage")
        repair_ref = {
            "fix": "repair-render",
            "segment_fix": "repair-segment",
            "validation_fix": "repair-validation",
            "code_eval_fix": "repair-code-eval",
            "improve": "repair-improve",
        }.get(normalized_stage)
        if repair_ref:
            add("mandatory", repair_ref, "stage_baseline", f"{normalized_stage} requires stage-specific repair discipline")

    if isinstance(teaching_plan, dict):
        if teaching_plan.get("selected_theme") or teaching_plan.get("selected_assets") is not None:
            add("mandatory", "runtime-theme-assets", "planner_structured", "teaching plan contains selected theme/assets contract")
        if teaching_plan.get("sections"):
            add("strong", "teaching-core", "planner_structured", "teaching plan sections require teacher-script execution")
            add("candidate", "reveal-narration", "planner_structured", "section beats may need staged reveal and narration")
            add("strong", "layout-title-protocol", "planner_structured", "planned sections require stable title behavior")
            add("strong", "layout-visual-clarity", "planner_structured", "planned sections require clarity-first visual composition")
        routes = teaching_plan.get("hybrid_routes")
        if isinstance(routes, dict) and routes.get("manim_section_ids"):
            add("strong", "routing", "planner_structured", "hybrid route metadata limits Manim generation scope")
        fast_path = teaching_plan.get("fast_path")
        if isinstance(fast_path, dict) and fast_path:
            add("candidate", "layout-page-body", "planner_structured", "template reference needs layout discipline during fusion")

        problem_intake = teaching_plan.get("problem_intake")
        if isinstance(problem_intake, dict) and bool(problem_intake.get("is_problem_solving")):
            for ref_id in ("cards-boxes", "highlights"):
                add("strong", ref_id, "planner_structured", "problem-solving opening may need a compact task card and marked givens/target")

    plan_text = _structured_plan_text(
        teaching_plan,
        "lesson_goal",
        "big_idea",
        "teaching_promise",
        "misconceptions",
    )
    request_blob = (request_text or "").lower()
    diagnostic_blob = " ".join(_iter_diagnostic_terms(diagnostics)).lower()
    code_features = _scan_code_features(code)

    camera_terms = (
        "camera",
        "camera movement",
        "camera frame",
        "moving camera",
        "movingcamerascene",
        "zoom",
        "zoom in",
        "zoom out",
        "pan",
        "viewport",
        "magnify",
        "magnification",
        "close-up",
        "close up",
        "focus into",
        "focus on a point",
        "viewport focus",
        "相机",
        "镜头",
        "放大",
        "缩小",
        "平移",
        "视角",
    )
    if _has_any(plan_text, *camera_terms) or code_features["has_camera_movement"]:
        add("strong", "camera-movement", "planner_or_code_feature", "structured plan/code contains camera, zoom, pan, or viewport movement")
    elif _has_any(request_blob, *camera_terms):
        add("candidate", "camera-movement", "request_semantic", "student request mentions camera, zoom, pan, or viewport movement")

    graph_terms = ("graph", "axes", "axis", "plot", "curve", "function", "derivative", "integral", "slope")
    if _has_any(plan_text, *graph_terms) or code_features["has_axes"]:
        for ref_id in ("coordinate-systems", "graph-dynamics", "labels"):
            add("strong", ref_id, "planner_or_code_feature", "structured plan/code contains graph, axes, or function reasoning")
    elif _has_any(request_blob, *graph_terms):
        for ref_id in ("coordinate-systems", "graph-dynamics", "labels"):
            add("candidate", ref_id, "request_semantic", "student request mentions graph/axes/function reasoning")

    formula_terms = ("formula", "equation", "derivation", "symbolic", "mathtex", "公式", "推导")
    if _has_any(plan_text, *formula_terms) or code_features["has_mathtex"]:
        for ref_id in ("equation-focus", "highlights"):
            add("strong", ref_id, "planner_or_code_feature", "structured plan/code contains formula or derivation focus")
    elif _has_any(request_blob, *formula_terms):
        for ref_id in ("equation-focus", "highlights"):
            add("candidate", ref_id, "request_semantic", "student request mentions formula or derivation focus")

    annotation_terms = ("arrow", "connector", "brace", "vector", "force", "geometry label", "label")
    if _has_any(plan_text, *annotation_terms) or code_features["has_annotations"]:
        for ref_id in ("arrows", "labels"):
            add("strong", ref_id, "planner_or_code_feature", "structured plan/code contains annotation or vector geometry")
    elif _has_any(request_blob, *annotation_terms):
        for ref_id in ("arrows", "labels"):
            add("candidate", ref_id, "request_semantic", "student request mentions annotations or vector geometry")

    math_physics_terms = (
        "math",
        "physics",
        "calculus",
        "algebra",
        "geometry",
        "function",
        "derivative",
        "integral",
        "vector",
        "force",
        "field",
        "surface",
        "wave",
        "orbit",
        "数学",
        "物理",
        "函数",
        "导数",
        "积分",
        "向量",
        "力",
    )
    if _has_any(plan_text, *math_physics_terms):
        add("strong", "math-physics-director", "planner_structured", "structured plan is primarily math/physics")
    elif _has_any(request_blob, *math_physics_terms):
        add("candidate", "math-physics-director", "request_semantic", "student request appears to be math/physics")

    if _has_any(diagnostic_blob, "non_text_anchor_lifecycle", "anchor_binding", "detached", "drifting", "drift"):
        for ref_id in ("anchor-lifecycle", "coordinate-systems", "arrows", "labels"):
            add("strong", ref_id, "diagnostics_taxonomy", "diagnostics mention anchor drift or detached geometry")

    if _has_any(
        diagnostic_blob,
        "block_overlap_risk",
        "overlap",
        "crowded",
        "dense",
        "layout",
        "truncated",
        "cut off",
        "multiple_body_roots_same_page",
        "fit_body_multiple_calls_same_page",
    ):
        for ref_id in ("layout-page-body", "layout-density-overlap", "layout-title-protocol", "layout-visual-clarity", "cards-boxes"):
            add("strong", ref_id, "diagnostics_taxonomy", "diagnostics mention layout density, overlap, or body-root structure")

    if _has_any(diagnostic_blob, "unsupported_manim_api", "label_marks", "get_grid", "unexpected keyword", "attributeerror"):
        add("strong", "runtime-api-core", "diagnostics_taxonomy", "diagnostics mention unsupported Manim API")
        add("strong", "runtime-language-api", "diagnostics_taxonomy", "diagnostics mention unsupported Manim API or label usage")

    if _has_any(
        diagnostic_blob,
        "pickle",
        "deepcopy",
        "thread.lock",
        "_thread.lock",
        "always_redraw",
        "updater",
        "add_updater",
        "callback",
    ):
        add("strong", "runtime-callback-safety", "diagnostics_taxonomy", "diagnostics mention callback, updater, deepcopy, or thread-lock safety")

    if _has_any(diagnostic_blob, "animation_continuity", "pacing", "transition", "motion", "temporal alignment"):
        add("strong", "motion-transitions", "diagnostics_taxonomy", "diagnostics mention pacing, transition, or motion continuity")

    if _has_any(
        diagnostic_blob,
        "camera movement",
        "camera frame",
        "movingcamerascene",
        "viewport",
        "zoom jerky",
        "jerky zoom",
        "camera zoom",
        "focus too fast",
        "viewport focus",
    ):
        add("strong", "camera-movement", "diagnostics_taxonomy", "diagnostics mention camera movement, viewport, or zoom/focus problems")

    if _has_any(diagnostic_blob, "tool_loop", "apply_patch", "read_file", "search_file", "finish_repair"):
        add("strong", "repair-tool-loop", "diagnostics_taxonomy", "tool-based repair requires patch-first workflow guidance")

    if code_features["has_groups"]:
        add("candidate", "layout-page-body", "code_feature_scan", "code contains Group/VGroup/panel/body layout constructs")
        add("candidate", "runtime-object-safety", "code_feature_scan", "code contains mixed object/layout containers")
    if normalized_stage in _REPAIR_STAGES and code_features["has_object_animation_safety"]:
        add("strong", "runtime-object-safety", "code_feature_scan", "repair code contains object containers, panels, or reveal animations")
    if code_features["has_callbacks"]:
        add("strong", "runtime-callback-safety", "code_feature_scan", "code contains graph/updater/callback-like constructs")
    if code_features["has_images"]:
        add("strong", "runtime-theme-assets", "code_feature_scan", "code touches icons or image mobjects")
    if code_features["has_3d"]:
        add("strong", "math-physics-director", "code_feature_scan", "code contains 3D/spatial Manim constructs")
    if code_features["has_camera_movement"]:
        add("strong", "camera-movement", "code_feature_scan", "code contains MovingCameraScene or camera.frame constructs")

    selected = _ordered_unique((*buckets["mandatory"].keys(), *buckets["strong"].keys()))
    candidates = _ordered_unique(reference_id for reference_id in buckets["candidate"] if reference_id not in selected)
    omitted = tuple(reference_id for reference_id in _ALL_REFERENCE_IDS if reference_id not in selected)
    reasons = {**buckets["mandatory"], **buckets["strong"], **buckets["candidate"]}
    reason = "; ".join(dict.fromkeys(reasons[ref] for ref in selected if ref in reasons)) or "Only baseline skill references selected."
    return SkillSelection(
        stage=normalized_stage,
        reference_ids=selected,
        package_ids=_package_ids_for(selected),
        reason=reason,
        reasons=reasons,
        omitted_reference_ids=omitted,
        mandatory_reference_ids=tuple(buckets["mandatory"].keys()),
        strong_reference_ids=tuple(buckets["strong"].keys()),
        candidate_reference_ids=candidates,
        rule_signals=tuple(signals),
        router_input_summary=build_router_input_summary(
            stage=normalized_stage,
            request_text=request_text,
            teaching_plan=teaching_plan,
            diagnostics=diagnostics,
            code=code,
        ),
    )


def merge_router_decision(selection: SkillSelection, decision: RouterDecision) -> SkillSelection:
    """Merge optional LLM router output without allowing it to remove guarded refs."""
    stage = selection.stage
    guarded = _ordered_unique((*selection.mandatory_reference_ids, *selection.strong_reference_ids))
    invalid: list[str] = []
    router_selected: list[str] = []
    for reference_id in decision.selected_reference_ids:
        if reference_id not in _REFERENCE_BY_ID or not _reference_stage_compatible(reference_id, stage):
            invalid.append(reference_id)
            continue
        router_selected.append(reference_id)

    valid_refused: list[str] = []
    for reference_id in decision.rejected_reference_ids:
        if reference_id not in _REFERENCE_BY_ID or not _reference_stage_compatible(reference_id, stage):
            invalid.append(reference_id)
            continue
        if reference_id not in guarded:
            valid_refused.append(reference_id)

    merged = _ordered_unique((*guarded, *router_selected))
    omitted = tuple(reference_id for reference_id in _ALL_REFERENCE_IDS if reference_id not in merged)
    reason_parts = [selection.reason]
    if decision.reason:
        reason_parts.append(f"router: {decision.reason}")
    return replace(
        selection,
        reference_ids=merged,
        package_ids=_package_ids_for(merged),
        omitted_reference_ids=omitted,
        refused_reference_ids=tuple(valid_refused),
        invalid_router_reference_ids=tuple(_ordered_unique(invalid)),
        reason="; ".join(part for part in reason_parts if part),
        router_decision=decision,
    )


def build_manim_skill_context(selection: SkillSelection) -> str:
    parts = ["## Selected Manim CodeGen skills"]
    if selection.reference_ids:
        parts.append(
            "### Selected references\n"
            + "\n".join(
                f"- `{reference_id}` ({_REFERENCE_BY_ID[reference_id].package_id}): {selection.reasons.get(reference_id, '')}"
                for reference_id in selection.reference_ids
                if reference_id in _REFERENCE_BY_ID
            )
        )
    for package_id in selection.package_ids:
        package = _PACKAGE_BY_ID.get(package_id)
        if package is None:
            continue
        parts.append(_skill_text(*package.path))
        for reference_id in selection.reference_ids:
            reference = _REFERENCE_BY_ID.get(reference_id)
            if reference is None or reference.package_id != package_id:
                continue
            parts.append(_skill_text(reference.package_id, *reference.path))
    return "\n\n".join(part for part in parts if part).strip()


def build_manim_skill_audit(selection: SkillSelection) -> Dict[str, Any]:
    return {
        "stage": selection.stage,
        "selected_package_ids": list(selection.package_ids),
        "selected_reference_ids": list(selection.reference_ids),
        "mandatory_reference_ids": list(selection.mandatory_reference_ids),
        "strong_reference_ids": list(selection.strong_reference_ids),
        "candidate_reference_ids": list(selection.candidate_reference_ids),
        "refused_reference_ids": list(selection.refused_reference_ids),
        "invalid_router_reference_ids": list(selection.invalid_router_reference_ids),
        "selection_reasons": dict(selection.reasons),
        "rule_signals": [
            {
                "reference_id": signal.reference_id,
                "strength": signal.strength,
                "source": signal.source,
                "reason": signal.reason,
            }
            for signal in selection.rule_signals
        ],
        "router_input_summary": dict(selection.router_input_summary),
        "router_decision": {
            "selected_reference_ids": list(selection.router_decision.selected_reference_ids),
            "rejected_reference_ids": list(selection.router_decision.rejected_reference_ids),
            "confidence": selection.router_decision.confidence,
            "reason": selection.router_decision.reason,
            "raw_response": selection.router_decision.raw_response,
            "error": selection.router_decision.error,
        },
        "omitted_reference_ids": list(selection.omitted_reference_ids),
        "reason": selection.reason,
    }


def _string_list_from_value(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _audit_string_list(audit: Mapping[str, Any], key: str) -> list[str]:
    return _string_list_from_value(audit.get(key))


def build_selected_skills_manifest(audit: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a compact run artifact that shows which CodeGen skills were used."""
    selected_reference_ids = _audit_string_list(audit, "selected_reference_ids")
    selected_package_ids = _audit_string_list(audit, "selected_package_ids")
    if not selected_package_ids:
        selected_package_ids = list(_package_ids_for(selected_reference_ids))
    else:
        inferred_package_ids = _package_ids_for(selected_reference_ids)
        selected_package_ids = list(_ordered_unique([*selected_package_ids, *inferred_package_ids]))

    mandatory_ids = set(_audit_string_list(audit, "mandatory_reference_ids"))
    strong_ids = set(_audit_string_list(audit, "strong_reference_ids"))
    candidate_ids = set(_audit_string_list(audit, "candidate_reference_ids"))
    selection_reasons = audit.get("selection_reasons")
    if not isinstance(selection_reasons, Mapping):
        selection_reasons = {}
    router_decision = audit.get("router_decision")
    if not isinstance(router_decision, Mapping):
        router_decision = {}
    router_selected_ids = set(_string_list_from_value(router_decision.get("selected_reference_ids")))

    def selection_source(reference_id: str) -> str:
        if reference_id in mandatory_ids:
            return "mandatory"
        if reference_id in strong_ids:
            return "strong_rule"
        if reference_id in candidate_ids and reference_id in router_selected_ids:
            return "router_promoted_candidate"
        if reference_id in candidate_ids:
            return "candidate"
        if reference_id in router_selected_ids:
            return "router_added"
        return "selected"

    references_by_package: Dict[str, list[Dict[str, Any]]] = {
        package_id: [] for package_id in selected_package_ids
    }
    orphan_references: list[Dict[str, Any]] = []
    for reference_id in selected_reference_ids:
        reference = _REFERENCE_BY_ID.get(reference_id)
        reference_payload = {
            "id": reference_id,
            "selection_source": selection_source(reference_id),
            "reason": str(selection_reasons.get(reference_id, "")).strip(),
        }
        if reference is not None:
            reference_payload.update(
                {
                    "package_id": reference.package_id,
                    "path": "/".join((reference.package_id, *reference.path)),
                    "tags": list(reference.tags),
                    "use_when": reference.use_when,
                }
            )
            references_by_package.setdefault(reference.package_id, []).append(reference_payload)
        else:
            orphan_references.append(reference_payload)

    selected_skills = []
    for package_id in selected_package_ids:
        package = _PACKAGE_BY_ID.get(package_id)
        selected_skills.append(
            {
                "id": package_id,
                "description": package.description if package else "",
                "path": "/".join(package.path) if package else "",
                "references": references_by_package.get(package_id, []),
            }
        )

    safe_router_decision = {
        "selected_reference_ids": _string_list_from_value(router_decision.get("selected_reference_ids")),
        "rejected_reference_ids": _string_list_from_value(router_decision.get("rejected_reference_ids")),
        "confidence": router_decision.get("confidence", 0.0),
        "reason": router_decision.get("reason", ""),
        "error": router_decision.get("error", ""),
    }

    return {
        "stage": audit.get("stage"),
        "selected_skill_count": len(selected_skills),
        "selected_reference_count": len(selected_reference_ids),
        "selected_skills": selected_skills,
        "selected_package_ids": selected_package_ids,
        "selected_reference_ids": selected_reference_ids,
        "mandatory_reference_ids": sorted(mandatory_ids),
        "strong_reference_ids": sorted(strong_ids),
        "candidate_reference_ids": _audit_string_list(audit, "candidate_reference_ids"),
        "refused_reference_ids": _audit_string_list(audit, "refused_reference_ids"),
        "invalid_router_reference_ids": _audit_string_list(audit, "invalid_router_reference_ids"),
        "omitted_reference_ids": _audit_string_list(audit, "omitted_reference_ids"),
        "selection_reasons": dict(selection_reasons),
        "rule_signals": audit.get("rule_signals", []),
        "router_input_summary": audit.get("router_input_summary", {}),
        "router_decision": safe_router_decision,
        "prompt_character_counts": audit.get("prompt_character_counts", {}),
        "orphan_reference_ids": [item["id"] for item in orphan_references],
        "source": "prompt_audit",
    }


def build_manim_skill_prompt(teaching_plan: Optional[Dict] = None) -> str:
    """Compatibility wrapper; it uses the new multi-package registry."""
    selection = select_manim_references("generate", teaching_plan=teaching_plan)
    return build_manim_skill_context(selection)


def build_remotion_skill_prompt() -> str:
    return "\n\n".join(
        [
            "## Local agent skill: Remotion",
            _skill_text("remotion", "SKILL.md"),
            _skill_text("remotion", "references", "hybrid.md"),
        ]
    ).strip()
