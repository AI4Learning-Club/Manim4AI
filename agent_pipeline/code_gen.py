"""
Agent for generating, fixing, and improving Manim scene code via LLM.

Supports text and image inputs.  Uses the OpenAI-compatible API with
streaming to handle long responses.
"""

from __future__ import annotations

import ast
import base64
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from plugins.manim.runtime_config import get_manim_settings

from .agent_skills import (
    RouterDecision,
    SkillSelection,
    build_manim_skill_audit,
    build_manim_skill_context,
    build_selected_skills_manifest,
    get_manim_skill_index,
    merge_router_decision,
    select_manim_references,
)
from .llm import LLMClient, LLMConfig, LLMDeltaCallback, LLMEventCallback, StreamTerminated
from .output_language import normalize_output_language, output_language_name
from .scene_capabilities import (
    SceneCapabilityContract,
    compile_scene_capability_contract,
    validate_scene_capability_contract,
)
from .scene_pack import (
    build_segment_scene_source,
    parse_scene_pack,
    recover_scene_pack_skeleton,
)
from .streaming_scene_pack import extract_parseable_prefix, sanitize_streaming_code
from .tool_runtime import ManimToolRuntime, ToolResult, build_openai_tool_schemas

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

def _stage_system_contract(stage: str, output_contract: str) -> str:
    return f"""\
You are the Manim CodeGen agent for stage `{stage}`.

Hard contract:
- Target Manim Community v0.20.1.
- Follow the selected skill context exactly; do not apply examples or long-form
  procedures from skills that were not loaded for this run.
- Generate or preserve a Scene Pack with `SCENE_MANIFEST`, one shared
  `LessonBase` based on `AI4LearningBaseScene`, and thin wrapper scenes.
- Use the selected theme and selected local assets exactly when provided.
- If no local assets were selected, do not invent image paths, raw URLs, or
  external assets.
- Keep runtime safety ahead of visual ambition: prefer runnable, debuggable
  Manim code over clever but fragile constructs.
- Preserve the teaching plan, section order, and requested output language.
- When `render_scope` is provided, generate exactly its allowed section ids and
  included semantic roles. Never render context owned by another backend.
- Treat Planner fields as final pedagogical decisions. Skills may guide safe
  implementation but must not reselect the opening, teaching structure,
  examples, representation strategy, narration goals, transitions, or closing.
- When an implementation detail is unspecified, choose the simplest safe
  Manim implementation that preserves the Planner's teaching intent.
- {output_contract}
"""


_SYSTEM_GENERATE = _stage_system_contract(
    "generate",
    "Output only runnable Python code inside a ```python``` block.",
)
_SYSTEM_FIX = _stage_system_contract(
    "fix",
    "Output only the corrected Python code inside a ```python``` block.",
)
_SYSTEM_SEGMENT_FIX = _stage_system_contract(
    "segment_fix",
    'Output JSON only with keys "method_name" and "updated_method_code".',
)
_SYSTEM_SEGMENT_VALIDATION_FIX = _stage_system_contract(
    "validation_fix",
    'Output JSON only with keys "method_name" and "updated_method_code".',
)
_SYSTEM_CODE_EVAL_FIX = _stage_system_contract(
    "code_eval_fix",
    "Output only the corrected Python code inside a ```python``` block.",
)
_SYSTEM_IMPROVE = _stage_system_contract(
    "improve",
    "Output only the improved Python code inside a ```python``` block.",
)


@dataclass(frozen=True)
class PromptSection:
    name: str
    text: str


@dataclass(frozen=True)
class PromptBundle:
    system: str
    user_sections: List[PromptSection]
    selected_refs: List[str]

    def user_text(self) -> str:
        return "\n\n".join(section.text for section in self.user_sections if section.text)


# ---------------------------------------------------------------------------
# Code extraction
# ---------------------------------------------------------------------------

def _extract_code(text: str) -> str:
    """Extract the first ```python ... ``` block from LLM output."""
    pattern = r"```python\s*\n(.*?)```"
    m = re.search(pattern, text, re.DOTALL)
    if m:
        return m.group(1).strip()
    pattern2 = r"```\s*\n(.*?)```"
    m2 = re.search(pattern2, text, re.DOTALL)
    if m2:
        return m2.group(1).strip()
    return text.strip()


def _extract_json_object(text: str) -> Dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    left = cleaned.find("{")
    right = cleaned.rfind("}")
    if left < 0 or right <= left:
        raise ValueError("No JSON object found in segment-fix response")
    return json.loads(cleaned[left : right + 1])


def _image_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    suffix = path.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "webp": "image/webp"}.get(suffix, "image/png")
    return f"data:{mime};base64,{b64}"


def _build_actionable_feedback(eval_report: Dict) -> str:
    """Translate the new eval_pipeline report into concrete repair guidance."""

    def _as_float(value) -> Optional[float]:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _metric_map(dimension: Dict) -> Dict[str, Dict]:
        metrics = dimension.get("metrics", [])
        result: Dict[str, Dict] = {}
        if not isinstance(metrics, list):
            return result
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            name = str(metric.get("name", "")).strip()
            if name:
                result[name] = metric
        return result

    def _metric_note(metric: Dict) -> str:
        details = str(metric.get("details", "") or "").strip()
        if details:
            return details
        return str(metric.get("description", "") or "").strip()

    lines: List[str] = []
    score = float(eval_report.get("overall_score", 0) or 0)
    lines.append(
        f"Overall score: {score:.2f} / 1.00 - "
        f"{'PASS' if eval_report.get('overall_passed') else 'FAIL'}\n"
    )

    dimensions = eval_report.get("dimensions", [])
    dimension_map: Dict[str, Dict] = {}
    if isinstance(dimensions, list):
        for dim in dimensions:
            if not isinstance(dim, dict):
                continue
            name = str(dim.get("name", "")).strip()
            if name:
                dimension_map[name] = dim

    hard_bug_issues = []
    soft_layout_issues = []
    for issue in eval_report.get("issues", []):
        if not isinstance(issue, dict):
            continue
        taxonomy = str(issue.get("taxonomy", "")).strip()
        if taxonomy == "hard_bug":
            hard_bug_issues.append(issue)
        elif taxonomy == "soft_layout_note":
            soft_layout_issues.append(issue)

    lines.append("### Problem summary from evaluation dimensions:\n")
    if hard_bug_issues:
        lines.append(
            "Repair policy: only confirmed `hard_bug` issues may justify rebuilding an affected local page block.\n"
            "Do NOT rewrite the whole lesson structure unless a hard bug truly requires it.\n"
        )
    else:
        lines.append(
            "Repair policy: no confirmed `hard_bug` issues were found.\n"
            "Keep the existing page structure and teaching flow intact. Only apply local polish for soft notes.\n"
        )

    visual_dim = dimension_map.get("Visual Quality")
    if visual_dim:
        visual_metrics = _metric_map(visual_dim)

        overlap = visual_metrics.get("overlap")
        overlap_score = _as_float(overlap.get("value")) if overlap else None
        if overlap and overlap_score is not None and overlap_score < 0.70:
            lines.append(
                f"**VISUAL QUALITY / OVERLAP (score {overlap_score:.2f})**: {_metric_note(overlap)}\n"
                "  -> Use this only as a diagnostic hint.\n"
                "  -> Trust the confirmed issue inventory below over this aggregate score.\n"
                "  -> Do not restructure the whole scene from this metric alone.\n"
            )

        layout = visual_metrics.get("layout")
        layout_score = _as_float(layout.get("value")) if layout else None
        if layout and layout_score is not None and layout_score < 0.60:
            lines.append(
                f"**VISUAL QUALITY / LAYOUT (score {layout_score:.2f})**: {_metric_note(layout)}\n"
                "  -> Treat this as a soft diagnostic only.\n"
                "  -> Do not split pages or rebuild the whole layout from this score alone.\n"
            )

        animation = visual_metrics.get("animation_continuity")
        animation_score = _as_float(animation.get("value")) if animation else None
        if animation and animation_score is not None and animation_score < 0.60:
            lines.append(
                f"**VISUAL QUALITY / ANIMATION CONTINUITY (score {animation_score:.2f})**: {_metric_note(animation)}\n"
                "  -> Motion pacing is jerky or visually discontinuous.\n"
                "  -> FIX: add short pauses between transitions, avoid moving whole layouts after entry, and use longer run_time for dense transformations.\n"
            )

        consistency = visual_metrics.get("visual_content_consistency")
        consistency_score = _as_float(consistency.get("value")) if consistency else None
        if consistency and consistency_score is not None and consistency_score < 0.60:
            lines.append(
                f"**VISUAL QUALITY / CONTENT CONSISTENCY (score {consistency_score:.2f})**: {_metric_note(consistency)}\n"
                "  -> The video does not clearly cover the intended teaching sections or drifts away from the lesson plan.\n"
                "  -> FIX: make each scene map to a teaching-plan section and ensure every key takeaway appears on screen explicitly.\n"
            )

        anchor_binding = visual_metrics.get("anchor_binding")
        anchor_binding_score = _as_float(anchor_binding.get("value")) if anchor_binding else None
        if anchor_binding and anchor_binding_score is not None and anchor_binding_score < 0.70:
            lines.append(
                f"**VISUAL QUALITY / ANCHOR BINDING (score {anchor_binding_score:.2f})**: {_metric_note(anchor_binding)}\n"
                "  -> Some labels, callouts, arrows, or highlighted ranges do not clearly match the thing they claim to annotate.\n"
                "  -> FIX: bind annotations to the actual target geometry, recompute positions after layout shifts, and avoid free-floating explanation boxes that can drift away from their targets.\n"
            )

    task_dim = dimension_map.get("Task Correctness")
    if task_dim:
        task_metrics = _metric_map(task_dim)

        accuracy = task_metrics.get("content_accuracy")
        if accuracy and accuracy.get("value") is False:
            lines.append(
                f"**TASK CORRECTNESS / CONTENT ACCURACY**: {_metric_note(accuracy)}\n"
                "  -> Some explanation, formula, label, or conclusion is incorrect or does not answer the requested lesson properly.\n"
                "  -> FIX: correct the math/science content first, and align every major claim with the teaching plan and topic.\n"
            )

        clarity = task_metrics.get("pedagogical_clarity")
        clarity_score = _as_float(clarity.get("value")) if clarity else None
        if clarity and clarity_score is not None and clarity_score < 0.60:
            lines.append(
                f"**TASK CORRECTNESS / PEDAGOGICAL CLARITY (score {clarity_score:.2f})**: {_metric_note(clarity)}\n"
                "  -> The explanation order is unclear or too jumpy.\n"
                "  -> FIX: simplify the narrative arc, make each shot do one teaching job, and tighten transitions between idea -> visual -> conclusion.\n"
                "  -> If the visual itself is too busy, simplify it before adding more explanatory text.\n"
            )

        engagement = task_metrics.get("engagement")
        engagement_score = _as_float(engagement.get("value")) if engagement else None
        if engagement and engagement_score is not None and engagement_score < 0.60:
            lines.append(
                f"**TASK CORRECTNESS / ENGAGEMENT (score {engagement_score:.2f})**: {_metric_note(engagement)}\n"
                "  -> The pacing or visual storytelling is too flat.\n"
                "  -> FIX: reduce dead time, reveal information progressively, and make the key insight appear through motion rather than static dumping.\n"
                "  -> Prefer one strong visual idea at a time over a crowded frame with many competing details.\n"
            )

    audio_dim = dimension_map.get("Audio Quality")
    if audio_dim:
        audio_metrics = _metric_map(audio_dim)
        alignment = audio_metrics.get("av_temporal_alignment")
        alignment_score = _as_float(alignment.get("value")) if alignment else None
        if alignment and alignment_score is not None and alignment_score < 0.60:
            lines.append(
                f"**AUDIO QUALITY / AV TEMPORAL ALIGNMENT (score {alignment_score:.2f})**: {_metric_note(alignment)}\n"
                "  -> Narration timing does not match on-screen changes well enough.\n"
                "  -> FIX: align spoken beats with visual reveals, avoid long stretches of speech with static visuals, and avoid large visual jumps before narration catches up.\n"
            )

    if hard_bug_issues:
        lines.append("\n### Confirmed hard bugs to fix:\n")
        for issue in hard_bug_issues:
            desc = str(issue.get("description") or issue.get("vlm_reason") or issue.get("cv_reason") or "").strip()
            severity = str(issue.get("severity", "high")).strip()
            tr = str(issue.get("time_range", "")).strip()
            conf = issue.get("confidence", issue.get("vlm_confidence"))
            overlap_kind = str(issue.get("overlap_kind", "")).strip()
            repair_action = str(issue.get("repair_action", "")).strip()
            metadata = ""
            if overlap_kind or repair_action:
                metadata = (
                    f"  -> Overlap kind: {overlap_kind or 'unspecified'}; "
                    f"repair action: {repair_action or 'rebuild affected local block'}.\n"
                )
            lines.append(
                f"- HARD BUG ({severity}, confidence={conf}): {desc}\n"
                f"  -> Time range: {tr}\n"
                f"{metadata}"
                "  -> FIX: repair this concrete bug locally. Rebuild only the affected block/page if necessary, while preserving the overall teaching flow. "
                "If the bug is a drifting geometry leaf, prefer a local builder plus `build_on_anchor(...)`.\n"
            )

    if soft_layout_issues:
        lines.append("\n### Soft layout notes:\n")
        for issue in soft_layout_issues:
            desc = str(issue.get("description") or issue.get("vlm_reason") or issue.get("cv_reason") or "").strip()
            severity = str(issue.get("severity", "medium")).strip()
            tr = str(issue.get("time_range", "")).strip()
            conf = issue.get("confidence", issue.get("vlm_confidence"))
            overlap_kind = str(issue.get("overlap_kind", "")).strip()
            repair_action = str(issue.get("repair_action", "")).strip()
            metadata = ""
            if overlap_kind or repair_action:
                metadata = (
                    f"  -> Overlap kind: {overlap_kind or 'unspecified'}; "
                    f"repair action: {repair_action or 'local spacing polish'}.\n"
                )
            lines.append(
                f"- SOFT NOTE ({severity}, confidence={conf}): {desc}\n"
                f"  -> Time range: {tr}\n"
                f"{metadata}"
                "  -> FIX: apply only local polish such as spacing, alignment, shortening text slightly, or repositioning arrows/labels.\n"
                "  -> Do NOT split pages, repack the whole layout, or rewrite the lesson structure because of this note.\n"
            )

    return "\n".join(lines)


def _build_local_asset_prompt(teaching_plan: Optional[Dict]) -> str:
    if not teaching_plan:
        return (
            "## Local icons\n"
            "No local icons were selected for this lesson. Do not invent icon "
            "filenames, image paths, URLs, or external assets."
        )

    selected_assets = teaching_plan.get("selected_assets", [])
    if not selected_assets:
        return (
            "## Local icons\n"
            "No local icons were selected for this lesson. Do not invent icon "
            "filenames, image paths, URLs, or external assets."
        )

    return (
        "## Local icons selected for this lesson\n"
        + json.dumps(selected_assets, ensure_ascii=False, indent=2)
        + "\n\nUse only these filenames. Load them only with "
        "`self.load_local_icon(\"filename.png\", height=...)`. "
        "Do not invent more icons or switch to raw `ImageMobject(...)` paths."
    )


def _build_opening_prompt(teaching_plan: Optional[Dict]) -> str:
    if not teaching_plan:
        return ""
    render_scope = teaching_plan.get("render_scope")
    if isinstance(render_scope, dict) and render_scope.get("include_opening") is False:
        return ""

    opening = teaching_plan.get("opening")
    if not isinstance(opening, dict):
        return ""

    style = str(opening.get("style", "")).strip()
    roadmap_style = str(opening.get("roadmap_style", "")).strip()
    hook_line = str(opening.get("hook_line", "")).strip()
    if not (style or roadmap_style or hook_line):
        return ""

    architecture = str(opening.get("architecture", "")).strip()
    opening_summary = {
        "architecture": architecture,
        "style": style,
        "hook_line": hook_line,
        "roadmap_style": roadmap_style,
    }
    problem_intake = teaching_plan.get("problem_intake")
    is_problem_solving = isinstance(problem_intake, dict) and bool(problem_intake.get("is_problem_solving"))

    prompt = (
        "## Opening plan for this lesson\n"
        + json.dumps(opening_summary, ensure_ascii=False, indent=2)
        + "\n\nThis opening plan is already fixed for the lesson.\n"
        + "- The opening must follow `opening.architecture` and `opening.style`.\n"
        + "- `opening.hook_line` is the chosen opening beat; it is not necessarily a question.\n"
        + "- The lesson roadmap must follow `opening.roadmap_style`.\n"
        + "- Every roadmap style must explain how THIS lesson will proceed.\n"
        + "- Do NOT write empty slogans, generic motivation lines, or repeated rhetorical questions.\n"
    )
    if is_problem_solving:
        prompt += (
            "- Because this is a problem-solving lesson, `opening.hook_line` belongs AFTER the concise read-in and opening marking beat.\n"
            "- Treat `opening.hook_line` as the next opening beat, not necessarily a question.\n"
            "- If `opening.style` is `question_first`, this is the second narration question beat after read-in, not the first line.\n"
            "- Do NOT lead with a meta strategy slogan or hook before the concise read-in.\n"
        )
    else:
        prompt += "- The first spoken or visual beat should cash out `opening.hook_line` according to `opening.architecture`; do not force it into a question.\n"
    return prompt


def _build_problem_intake_prompt(teaching_plan: Optional[Dict]) -> str:
    if not teaching_plan:
        return ""
    render_scope = teaching_plan.get("render_scope")
    if (
        isinstance(render_scope, dict)
        and render_scope.get("include_problem_intake") is False
    ):
        return ""

    problem_intake = teaching_plan.get("problem_intake")
    if not isinstance(problem_intake, dict):
        return ""

    summary = {
        "is_problem_solving": problem_intake.get("is_problem_solving"),
        "restatement": problem_intake.get("restatement"),
        "givens": problem_intake.get("givens"),
        "target": problem_intake.get("target"),
        "key_terms": problem_intake.get("key_terms"),
        "visual_marking_plan": problem_intake.get("visual_marking_plan"),
    }

    include_opening = not (
        isinstance(render_scope, dict)
        and render_scope.get("include_opening") is False
    )
    first_beat_location = (
        "`opening_page()`"
        if include_opening
        else "the first rendered section method in `render_scope.allowed_section_ids`"
    )

    return (
        "## Problem-intake plan for this lesson\n"
        + json.dumps(summary, ensure_ascii=False, indent=2)
        + "\n\nUse this problem-intake plan to shape the opening.\n"
        + f"- If `is_problem_solving` is true, the first `speak_with_subtitle(...)` beat in {first_beat_location} must read `problem_intake.restatement` in 1-2 concise student-language sentences.\n"
        + "- If `is_problem_solving` is true, do NOT lead with strategy commentary, generic motivation, or `opening.hook_line` before that read-in.\n"
        + "- If `is_problem_solving` is true, show a compact problem card or reconstructed题面 card before solving.\n"
        + "- If `is_problem_solving` is true and the problem has multiple sub-questions, the compact reconstructed题面 card must cover each sub-question before structural explanation begins.\n"
        + "- If `is_problem_solving` is true, visually mark givens, target, key terms, variables, or diagram relations before the first derivation.\n"
        + "- If `is_problem_solving` is true, the opening order is: concise restatement -> visual marking -> `opening.hook_line` -> roadmap/structure.\n"
        + "- If `is_problem_solving` is false, use this only as a short topic-intake: restate the learner's central question and highlight key terms without inventing a fake exercise.\n"
        + "- Use sequential circles/ellipses, outline boxes, underlines, arrows, braces, color highlights, or callout labels.\n"
        + "- Keep markings attached to the exact text, formula part, or diagram relation they explain; do not place decorative floating marks.\n"
        + "- If the original problem is long, display only the essential clauses and clearly label them as 已知 / 要求 / 关键关系.\n"
    )


def _build_camera_execution_prompt(
    selection: SkillSelection,
    teaching_plan: Optional[Dict] = None,
) -> str:
    if "camera-movement" not in selection.reference_ids:
        return ""

    planned_intents: list[str] = []
    has_representation_contract = False
    if isinstance(teaching_plan, dict):
        sections = teaching_plan.get("sections")
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict):
                    continue
                representation = section.get("representation_plan")
                if not isinstance(representation, dict):
                    continue
                has_representation_contract = True
                intent = str(representation.get("camera_intent", "")).strip()
                if intent and intent not in {"none", "fixed"}:
                    planned_intents.append(intent)

    if has_representation_contract:
        if not planned_intents:
            return (
                "## Camera implementation boundary\n"
                "The Planner did not select a moving-camera intent. Treat the "
                "camera reference as implementation knowledge only; do not invent "
                "zoom, pan, or follow beats.\n"
            )
        return (
            "## Planned camera execution\n"
            f"Implement only these Planner-selected camera intents: {', '.join(dict.fromkeys(planned_intents))}.\n"
            "Do not add extra camera beats or replace the planned representation. "
            "Use the selected camera skill only for safe Manim mechanics.\n"
        )

    return (
        "## Camera movement execution\n"
        "- Because no Planner contract was supplied, realize the requested camera movement directly in code.\n"
        "- Do not satisfy camera movement only with a single zoom-in/restore when the visual has a moving point, path, trajectory, process, or region comparison.\n"
        "- Use at least one non-zoom camera motion when the lesson has those opportunities: pan between semantic targets, follow/track a moving target, or track along a curve/path.\n"
        "- For multi-section lessons with camera opportunities, prefer 2-4 deliberate camera beats across the full video; each beat must serve a distinct teaching intent.\n"
        "- A follow beat may use `frame.add_updater(lambda f: f.move_to(target.get_center()))` while the target moves; clear the updater immediately and call `Restore(frame)` before unrelated content.\n"
    )


def _build_selected_theme_prompt(teaching_plan: Optional[Dict]) -> str:
    if not teaching_plan:
        return ""

    selected_theme = teaching_plan.get("selected_theme")
    if not isinstance(selected_theme, dict):
        return ""

    theme_id = str(selected_theme.get("theme_id", "")).strip()
    if not theme_id:
        return ""

    theme_summary = {
        "theme_id": theme_id,
        "display_name": selected_theme.get("display_name"),
        "brightness": selected_theme.get("brightness"),
        "background_asset": selected_theme.get("background_asset"),
        "notes": selected_theme.get("notes"),
        "recommended_opening_style": selected_theme.get("recommended_opening_style", []),
        "recommended_scene_density": selected_theme.get("recommended_scene_density"),
        "preferred_topics": selected_theme.get("preferred_topics", []),
        "reason": selected_theme.get("reason"),
    }

    return (
        "## Selected theme for this run\n"
        + json.dumps(theme_summary, ensure_ascii=False, indent=2)
        + "\n\nThis theme choice is already fixed for the lesson.\n"
        + f'- `LessonBase` MUST set `theme_id = "{theme_id}"`.\n'
        + "- Set `theme_id` on `LessonBase`, not separately on each wrapper scene.\n"
        + "OVERRIDE any older legacy-palette examples in the generic prompt.\n"
        + "Respect the registered theme background treatment and contrast strategy already encoded in the theme pack.\n"
        + "Do not simulate a different mood by adding a new full-screen recolor overlay, replacing the background image, or hardcoding a separate palette on top of the selected theme.\n"
        + "For new code, prefer theme-aware APIs:\n"
        + "- `self.get_text(...)` for default text\n"
        + "- `self.get_math(...)` for default formulas\n"
        + "- `self.theme_token(\"text_secondary\")` / `self.theme_token(\"text_muted\")` for softer labels\n"
        + "- `self.theme_token(\"accent_primary\")` / `self.theme_token(\"accent_secondary\")` for highlights\n"
        + "- `self.theme_token(\"warning_color\")` / `self.theme_token(\"success_color\")` for warning and success states\n"
        + "- `self.theme_token(\"panel_stroke\")`, `self.theme_token(\"panel_fill_color\")`, and `self.theme_token(\"panel_fill_opacity\")` for panels\n"
        + "- `self.theme_token(\"grid_or_axis_color\")` for axes and grids\n"
        + "Do not import or rely on legacy palette names like `BLUE_100` or `CYAN_400` in newly generated scenes unless you are preserving existing code."
        + (
            "\n- `slate_mist` should read as a cool gray-blue textured background with near-white text, cyan primary structure, and amber secondary emphasis. Preserve that hierarchy through theme helpers instead of ad-hoc styling."
            if theme_id == "slate_mist"
            else ""
        )
    )


_CODEGEN_LESSON_CONTRACT_FIELDS = (
    "lesson_goal",
    "student_profile",
    "teaching_promise",
    "hook",
    "big_idea",
    "teacher_voice",
    "narrative_arc",
    "closing",
)
_CODEGEN_SECTION_CONTRACT_FIELDS = (
    "id",
    "title",
    "teacher_goal",
    "teacher_move",
    "student_question",
    "why_this_step_now",
    "expected_student_reaction",
    "concrete_example",
    "visual_strategy",
    "representation_plan",
    "board_plan",
    "narration_goal",
    "key_takeaway",
    "check_for_understanding",
    "transition",
)


def _copy_contract_fields(source: Dict, fields: tuple[str, ...]) -> Dict[str, object]:
    return {key: deepcopy(source[key]) for key in fields if key in source}


def compile_codegen_execution_contract(
    teaching_plan: Optional[Dict],
) -> Dict[str, object]:
    """Compile Planner decisions into a lossless CodeGen execution contract."""

    if not isinstance(teaching_plan, dict):
        return {}

    context: Dict[str, object] = {
        "contract_version": "teaching_execution.v1",
        "decision_ownership": (
            "Planner fields are final pedagogical decisions. CodeGen must implement "
            "them and must not reselect the opening, lesson structure, examples, "
            "representation strategy, narration goals, transitions, or closing."
        ),
    }
    render_scope = (
        teaching_plan.get("render_scope")
        if isinstance(teaching_plan.get("render_scope"), dict)
        else {}
    )
    lesson_fields = list(_CODEGEN_LESSON_CONTRACT_FIELDS)
    if render_scope.get("include_opening") is False:
        lesson_fields = [key for key in lesson_fields if key != "hook"]
    if render_scope.get("include_closing") is False:
        lesson_fields = [key for key in lesson_fields if key != "closing"]
    context.update(_copy_contract_fields(teaching_plan, tuple(lesson_fields)))

    structured_fields = {
        "problem_intake": (
            "is_problem_solving",
            "restatement",
            "givens",
            "target",
            "key_terms",
            "visual_marking_plan",
        ),
        "opening": ("architecture", "style", "hook_line", "roadmap_style"),
    }
    for key, fields in structured_fields.items():
        if key == "opening" and render_scope.get("include_opening") is False:
            continue
        if (
            key == "problem_intake"
            and render_scope.get("include_problem_intake") is False
        ):
            continue
        value = teaching_plan.get(key)
        if isinstance(value, dict):
            context[key] = _copy_contract_fields(value, fields)

    misconceptions = teaching_plan.get("misconceptions")
    if isinstance(misconceptions, list):
        context["misconceptions"] = [
            _copy_contract_fields(
                item,
                ("mistake", "why_student_thinks_so", "teacher_response"),
            )
            for item in misconceptions
            if isinstance(item, dict)
        ]

    sections = teaching_plan.get("sections")
    if isinstance(sections, list):
        context["sections"] = [
            _copy_contract_fields(item, _CODEGEN_SECTION_CONTRACT_FIELDS)
            for item in sections
            if isinstance(item, dict)
        ]

    passthrough_fields = (
        "selected_assets",
        "selected_theme",
        "hybrid_routes",
        "render_scope",
        "fast_path",
    )
    context.update(_copy_contract_fields(teaching_plan, passthrough_fields))
    return context


def _build_render_scope_prompt(teaching_plan: Optional[Dict]) -> str:
    if not isinstance(teaching_plan, dict):
        return ""
    render_scope = teaching_plan.get("render_scope")
    if not isinstance(render_scope, dict):
        return ""
    return (
        "## Render scope — hard ownership contract\n"
        + json.dumps(render_scope, ensure_ascii=False, indent=2)
        + "\n\nGenerate only the section ids and semantic roles owned by this scope. "
        "Continuity context may guide the first and last transition, but it must "
        "not create manifest entries, wrapper scenes, section methods, visuals, "
        "or narration for excluded opening, problem-intake, summary, or closing roles."
    )


def _build_codegen_acceptance_checklist(teaching_plan: Optional[Dict]) -> str:
    render_scope = (
        teaching_plan.get("render_scope")
        if isinstance(teaching_plan, dict)
        and isinstance(teaching_plan.get("render_scope"), dict)
        else {}
    )
    lines = ["## Acceptance checklist"]
    allowed_ids = render_scope.get("allowed_section_ids")
    if isinstance(allowed_ids, list):
        lines.append(
            "- `SCENE_MANIFEST` contains exactly the render-scope section ids in order; do not add opening or closing segments."
        )
    else:
        lines.append(
            "- `SCENE_MANIFEST` contains the teaching-plan section ids exactly once and in exact order; only optional `opening` and `closing` semantic segments may appear at their respective boundaries."
        )
    lines.append(
        "- `LessonBase.theme_id` matches the selected theme when one is provided."
    )
    if render_scope.get("include_problem_intake", True):
        lines.append(
            "- Problem-solving lessons start with the problem-intake read-in and visual marking before solving."
        )
    lines.append(
        "- Every rendered section implements its Planner-selected example and `representation_plan`; do not substitute another teaching design."
    )
    if render_scope.get("include_closing", True):
        lines.append(
            "- The final section realizes the Planner's closing summary, transfer question, and after-class prompt when provided."
        )
    lines.extend(
        [
            "- Every spoken beat uses a direct extractable call such as `self.speak_with_subtitle(\"literal text\", ...)` or `self.speak(\"literal text\", ...)`; do not wrap TTS in `narrate`/`say` helpers or pass variables as the first argument.",
            "- Do not use local icons, raw image paths, or URLs when no local assets were selected.",
        ]
    )
    return "\n".join(lines)


def _build_codegen_teaching_context(
    teaching_plan: Optional[Dict],
) -> Dict[str, object]:
    """Backward-compatible alias for the teaching execution contract compiler."""

    return compile_codegen_execution_contract(teaching_plan)


def _build_fast_path_reference_prompt(teaching_plan: Optional[Dict]) -> str:
    if not isinstance(teaching_plan, dict):
        return ""
    fast_path = teaching_plan.get("fast_path")
    reference = teaching_plan.get("fast_path_reference")
    if not isinstance(fast_path, dict) or not isinstance(reference, dict):
        return ""
    template_code = str(reference.get("template_code") or "").strip()
    if not template_code:
        return ""
    fast_path_meta = {
        key: fast_path.get(key)
        for key in (
            "template_id",
            "category_id",
            "category_display_name",
            "mode",
            "rewrite_required",
            "taxonomy_size",
        )
        if key in fast_path
    }
    return (
        "## Classified template reference\n"
        "A backend-side classifier matched this request to a lesson/video category. "
        "The template below is a reference baseline only, not final code. "
        "You must fuse it with the actual student request and teaching plan, rewriting sections, narration beats, "
        "examples, labels, scene flow, and visual emphasis so the output matches the current lesson rather than copying the baseline verbatim.\n\n"
        "Allowed reuse:\n"
        "- high-level segment ordering when it still fits the current lesson\n"
        "- stable helper patterns and page-composition techniques\n"
        "- proven visual idioms that remain relevant\n\n"
        "Required changes:\n"
        "- adapt the math/content details to the current request\n"
        "- rewrite scene text, examples, and checks for understanding\n"
        "- remove or replace any baseline-specific content that does not belong to this lesson\n"
        "- preserve the Scene Pack contract while producing a fresh lesson-specific implementation\n\n"
        f"Category metadata:\n{_compact_json_text(fast_path_meta)}\n\n"
        f"Reference template baseline:\n```python\n{template_code}\n```"
    )


def _compact_json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _prompt_audit(
    bundle: PromptBundle,
    *,
    selection: SkillSelection,
    teaching_plan: Optional[Dict],
) -> Dict[str, Any]:
    user_text = bundle.user_text()
    section_ids = []
    render_scope: Dict[str, object] = {}
    if isinstance(teaching_plan, dict):
        sections = teaching_plan.get("sections")
        if isinstance(sections, list):
            section_ids = [
                str(item.get("id")).strip()
                for item in sections
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            ]
        if isinstance(teaching_plan.get("render_scope"), dict):
            render_scope = deepcopy(teaching_plan["render_scope"])
    audit = build_manim_skill_audit(selection)
    skill_chars = sum(
        len(section.text)
        for section in bundle.user_sections
        if section.name == "selected_skill_refs"
    )
    audit.update(
        {
            "section_ids_included": section_ids,
            "render_scope": render_scope,
            "prompt_character_counts": {
                "system": len(bundle.system),
                "user": len(user_text),
                "skill_context": skill_chars,
                "total": len(bundle.system) + len(user_text),
                "sections": {section.name: len(section.text) for section in bundle.user_sections},
            },
        }
    )
    return audit


def _emit_skill_selection_event(
    on_event: LLMEventCallback | None,
    audit: Dict[str, Any],
) -> None:
    if on_event is None or not audit:
        return
    try:
        on_event(
            {
                "type": "codegen_skill_selection",
                "prompt_audit": audit,
                "selected_skills_manifest": build_selected_skills_manifest(audit),
            }
        )
    except Exception:
        return


def _build_prompt_bundle(
    *,
    system: str,
    sections: List[PromptSection],
    selection: SkillSelection,
) -> PromptBundle:
    return PromptBundle(
        system=system,
        user_sections=sections,
        selected_refs=list(selection.reference_ids),
    )


def _lesson_base_theme_id(code: str) -> str:
    try:
        module = ast.parse(code)
    except SyntaxError:
        return ""
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != "LessonBase":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "theme_id" for target in stmt.targets):
                continue
            if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                return stmt.value.value.strip()
    return ""


def _core_terms(text: str) -> List[str]:
    terms = []
    for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", text or ""):
        if token not in terms:
            terms.append(token)
        if len(terms) >= 8:
            break
    return terms


def _post_generation_contract_issues(
    code: str,
    teaching_plan: Optional[Dict],
    *,
    capability_contract: SceneCapabilityContract | None = None,
) -> List[str]:
    plan = teaching_plan if isinstance(teaching_plan, dict) else {}
    issues: List[str] = []
    try:
        spec = parse_scene_pack(code)
    except Exception as exc:
        return [f"Scene Pack is not parseable after generation: {exc}"]

    routes = plan.get("hybrid_routes") if isinstance(plan.get("hybrid_routes"), dict) else {}
    routed_ids = routes.get("manim_section_ids") if isinstance(routes.get("manim_section_ids"), list) else []
    render_scope = plan.get("render_scope") if isinstance(plan.get("render_scope"), dict) else {}
    scoped_ids = (
        render_scope.get("allowed_section_ids")
        if isinstance(render_scope.get("allowed_section_ids"), list)
        else []
    )
    sections = plan.get("sections") if isinstance(plan.get("sections"), list) else []
    expected_section_ids = [
        str(item.get("id")).strip()
        for item in sections
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    ]
    if scoped_ids:
        expected_section_ids = [str(item).strip() for item in scoped_ids if str(item).strip()]
    elif routed_ids:
        expected_section_ids = [str(item).strip() for item in routed_ids if str(item).strip()]

    manifest_ids = [segment.segment_id for segment in spec.manifest]
    if scoped_ids and manifest_ids != expected_section_ids:
        issues.append(
            "SCENE_MANIFEST must exactly match `render_scope.allowed_section_ids` in order; "
            f"expected {expected_section_ids}, got {manifest_ids}."
        )
    elif expected_section_ids:
        permitted_semantic_ids = {"opening", "closing"}
        unexpected_ids = [
            segment_id
            for segment_id in manifest_ids
            if segment_id not in expected_section_ids
            and segment_id not in permitted_semantic_ids
        ]
        manifest_section_ids = [
            segment_id for segment_id in manifest_ids if segment_id in expected_section_ids
        ]
        missing_ids = [section_id for section_id in expected_section_ids if section_id not in manifest_ids]
        if missing_ids:
            issues.append(
                "SCENE_MANIFEST must cover the teaching-plan section order; missing section id(s): "
                + ", ".join(missing_ids)
            )
        if unexpected_ids:
            issues.append(
                "SCENE_MANIFEST contains unrelated extra section id(s): "
                + ", ".join(unexpected_ids)
                + "."
            )
        if manifest_section_ids != expected_section_ids:
            issues.append(
                "SCENE_MANIFEST teaching section ids must exactly match the teaching-plan order; "
                f"expected {expected_section_ids}, got {manifest_section_ids}."
            )
        if "opening" in manifest_ids and manifest_ids[0] != "opening":
            issues.append("The optional `opening` segment must be the first manifest entry.")
        if "closing" in manifest_ids and manifest_ids[-1] != "closing":
            issues.append("The optional `closing` segment must be the last manifest entry.")

    try:
        module = ast.parse(code)
        defined_method_names = {
            node.name for node in ast.walk(module) if isinstance(node, ast.FunctionDef)
        }
    except SyntaxError:
        defined_method_names = set()
    forbidden_methods = []
    if render_scope.get("include_opening") is False:
        forbidden_methods.append("opening_page")
    if render_scope.get("include_closing") is False:
        forbidden_methods.append("closing_page")
    rendered_forbidden_methods = [
        method_name
        for method_name in forbidden_methods
        if method_name in defined_method_names
    ]
    if rendered_forbidden_methods:
        issues.append(
            "Render scope excludes these semantic role method(s), so remove them and "
            "their wrappers/manifest entries: " + ", ".join(rendered_forbidden_methods)
        )

    selected_theme = plan.get("selected_theme")
    if isinstance(selected_theme, dict):
        expected_theme_id = str(selected_theme.get("theme_id", "")).strip()
        if expected_theme_id:
            actual_theme_id = _lesson_base_theme_id(code)
            if actual_theme_id != expected_theme_id:
                issues.append(
                    f'LessonBase.theme_id must be "{expected_theme_id}", got '
                    f'"{actual_theme_id or "<missing>"}".'
                )

    problem_intake = plan.get("problem_intake")
    if (
        render_scope.get("include_problem_intake", True)
        and isinstance(problem_intake, dict)
        and bool(problem_intake.get("is_problem_solving"))
    ):
        restatement = str(problem_intake.get("restatement", "")).strip()
        terms = _core_terms(restatement)
        if terms and spec.manifest:
            try:
                first_segment_code = build_segment_scene_source(
                    code,
                    spec.manifest[0].segment_id,
                )
            except Exception:
                first_segment_code = ""
            first_segment_terms = set(
                re.findall(r"[\w\u4e00-\u9fff]{2,}", first_segment_code)
            )
            matched_terms = sum(term in first_segment_terms for term in terms)
            required_matches = min(2, len(terms))
            if matched_terms < required_matches:
                issues.append(
                    "The first problem-solving segment must include the core wording from "
                    "`problem_intake.restatement` before solving."
                )

    selected_assets = plan.get("selected_assets")
    if not (isinstance(selected_assets, list) and selected_assets):
        forbidden_asset_patterns = ("load_local_icon(", "ImageMobject(", "http://", "https://")
        if any(pattern in code for pattern in forbidden_asset_patterns):
            issues.append(
                "No local assets were selected; generated code must not load icons, raw images, or URLs."
            )

    compiled_capabilities = capability_contract or compile_scene_capability_contract(())
    issues.extend(validate_scene_capability_contract(code, compiled_capabilities))

    return issues


def _pascal_from_snake(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[_\W]+", name) if part)


def _segment_id_from_method(method_name: str) -> str:
    if method_name == "opening_page":
        return "opening"
    if method_name == "closing_page":
        return "closing"
    if method_name.startswith("section_"):
        return method_name[len("section_") :]
    if method_name.endswith("_page"):
        return method_name[: -len("_page")]
    return method_name


def _scene_name_from_segment(index: int, segment_id: str) -> str:
    return f"Segment{index:02d}{_pascal_from_snake(segment_id)}Scene"


def _recoverable_scene_method(node: ast.FunctionDef) -> bool:
    method_name = node.name
    if not (
        method_name in {"opening_page", "closing_page"}
        or method_name.startswith("section_")
        or method_name.endswith("_page")
    ):
        return False
    if node.decorator_list:
        return False

    positional = [*node.args.posonlyargs, *node.args.args]
    if not positional or positional[0].arg != "self":
        return False
    required_positional = len(positional) - len(node.args.defaults)
    if required_positional > 1:
        return False
    if any(default is None for default in node.args.kw_defaults):
        return False
    return True


def _recover_missing_manifest_scene_pack(code: str) -> str | None:
    if "SCENE_MANIFEST" in code:
        return None
    try:
        module = ast.parse(code)
    except SyntaxError:
        return None

    lesson_base = next(
        (node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "LessonBase"),
        None,
    )
    if lesson_base is None:
        return None

    method_names = [
        stmt.name
        for stmt in lesson_base.body
        if isinstance(stmt, ast.FunctionDef) and _recoverable_scene_method(stmt)
    ]
    if not method_names:
        return None

    manifest_entries = []
    wrappers = []
    segment_ids: set[str] = set()
    existing_class_names = {
        node.name for node in module.body if isinstance(node, ast.ClassDef)
    }
    for index, method_name in enumerate(method_names):
        segment_id = _segment_id_from_method(method_name)
        scene_name = _scene_name_from_segment(index, segment_id)
        if not segment_id or segment_id in segment_ids or scene_name in existing_class_names:
            return None
        segment_ids.add(segment_id)
        manifest_entries.append(
            f'    {{"id": "{segment_id}", "scene": "{scene_name}", "method": "{method_name}"}},'
        )
        wrappers.append(
            f"class {scene_name}(LessonBase):\n"
            "    def construct(self):\n"
            f"        self.{method_name}()\n"
        )

    manifest_source = "SCENE_MANIFEST = [\n" + "\n".join(manifest_entries) + "\n]"
    import_end_line = max(
        (
            int(node.end_lineno or node.lineno)
            for node in module.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ),
        default=0,
    )
    original_lines = code.rstrip().splitlines()
    recovered_lines = [
        *original_lines[:import_end_line],
        "",
        manifest_source,
        "",
        *original_lines[import_end_line:],
        "",
        "\n\n".join(wrappers).rstrip(),
        "",
    ]
    recovered = "\n".join(recovered_lines)
    try:
        parse_scene_pack(recovered)
    except Exception:
        return None
    return recovered


_SYSTEM_POST_GENERATION_CONTRACT_REPAIR = _stage_system_contract(
    "contract_fix",
    "Output only the complete corrected Python file inside a ```python``` block.",
) + """

Deterministic post-generation checks found full-file contract mismatches. Fix
ONLY those reported mismatches while preserving correct teaching content,
narration, theme helpers, section methods, and existing visuals. This is not a
segment-method repair stage.
"""


def _build_output_language_prompt(output_language: str) -> str:
    language = normalize_output_language(output_language)
    language_name = output_language_name(language)
    if language == "zh":
        return (
            "## Output language\n"
            "The final video must use Chinese for all user-facing natural language.\n"
            "- All titles, labels, captions, section headers, subtitles, and narration must be in natural Chinese.\n"
            "- If the teaching plan or request contains English teaching text, translate its meaning into Chinese before putting it on screen.\n"
            "- Only formulas, variable names, standard math symbols, units, file names, and truly necessary abbreviations may remain non-Chinese.\n"
            "- If an abbreviation is important, prefer translated Chinese plus the abbreviation in parentheses.\n"
            "- Avoid stock roadmap slogans such as \"我们将看懂三件事\".\n"
        )

    return (
        "## Output language\n"
        f"The final video must use {language_name} for all user-facing natural language.\n"
        "- All titles, labels, captions, section headers, subtitles, and narration must be in clear classroom English.\n"
        "- The teaching plan may be written in Chinese; translate its teacher intent into English instead of copying Chinese wording into the video.\n"
        "- Only formulas, variable names, standard math symbols, units, file names, and truly necessary abbreviations may remain non-English.\n"
        "- If a translated term benefits from an abbreviation, write the English term first and keep the abbreviation short.\n"
    )


# ---------------------------------------------------------------------------
# Tool-based repair (patch-first)
# ---------------------------------------------------------------------------

SCENE_PACK_TOOL_FILENAME = "scene_pack.py"


def _build_system_tool_repair_for_file(target_file: str) -> str:
    sf = target_file
    selection = select_manim_references(
        "fix",
        diagnostics={
            "repair_kind": "tool_loop",
            "tool_loop": True,
            "categories": ["render", "scene_pack", "layout", "anchor", "runtime"],
        },
    )
    skill_context = build_manim_skill_context(selection)
    return (
        f"""You are an expert Manim debugger. You MUST use tools to repair `{sf}`.

Do NOT paste the entire Python file in chat. The writable scene file is `{sf}` under the run directory; tools enforce path safety.

## Selected skill context
{skill_context}
"""
    )


def _build_system_tool_repair() -> str:
    return _build_system_tool_repair_for_file(SCENE_PACK_TOOL_FILENAME)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class CodeGenAgent:
    """LLM-backed agent for Manim code generation / repair / improvement."""

    def __init__(
        self,
        api_key: str | LLMConfig,
        base_url: str = "https://api2.tabcode.cc/openai",
        model: str = "gpt-5.4",
    ):
        if isinstance(api_key, LLMConfig):
            llm_config = api_key
            self.model = llm_config.model
            self.client = LLMClient(llm_config)
        else:
            self.model = model
            self.client = LLMClient(
                LLMConfig(
                    stage="adhoc",
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                ),
            )
        self._last_tool_fix_meta: Dict[str, Any] = {}
        self._last_prompt_audit: Dict[str, Any] = {}

    def get_last_tool_fix_meta(self) -> Dict[str, Any]:
        return dict(self._last_tool_fix_meta)

    def get_last_prompt_audit(self) -> Dict[str, Any]:
        return dict(getattr(self, "_last_prompt_audit", {}) or {})

    def _call(
        self,
        system: str,
        user_content: list,
        max_retries: int = 3,
        *,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
    ) -> str:
        import time as _time
        for attempt in range(max_retries):
            try:
                if on_event is None:
                    text = self.client.generate_text(
                        system,
                        user_content,
                        max_retries=1,
                        on_delta=on_delta,
                    )
                else:
                    text = self.client.generate_text(
                        system,
                        user_content,
                        max_retries=1,
                        on_delta=on_delta,
                        on_event=on_event,
                    )
                if text.strip():
                    return text.strip()
                raise TimeoutError("Empty response from API")
            except Exception as exc:
                if attempt < max_retries - 1:
                    wait = 5 * (attempt + 1)
                    print(f"  API error (attempt {attempt+1}/{max_retries}): {exc}")
                    print(f"  Retrying in {wait}s ...")
                    _time.sleep(wait)
                else:
                    raise

    def _select_manim_skills(
        self,
        stage: str,
        *,
        request_text: Optional[str] = None,
        teaching_plan: Optional[Dict] = None,
        diagnostics: Any = None,
        code: Optional[str] = None,
    ) -> SkillSelection:
        selection = select_manim_references(
            stage,
            request_text=request_text,
            teaching_plan=teaching_plan,
            diagnostics=diagnostics,
            code=code,
        )
        if not selection.candidate_reference_ids:
            return selection
        return self._route_manim_skill_selection(selection)

    def _route_manim_skill_selection(self, selection: SkillSelection) -> SkillSelection:
        catalog = get_manim_skill_index()
        stage = selection.stage
        router_payload = {
            "stage": stage,
            "rule_layer": {
                "mandatory_reference_ids": list(selection.mandatory_reference_ids),
                "strong_reference_ids": list(selection.strong_reference_ids),
                "candidate_reference_ids": list(selection.candidate_reference_ids),
                "rule_signals": [
                    {
                        "reference_id": signal.reference_id,
                        "strength": signal.strength,
                        "source": signal.source,
                        "reason": signal.reason,
                    }
                    for signal in selection.rule_signals
                ],
            },
            "router_input_summary": dict(selection.router_input_summary),
            "skill_catalog": {
                "packages": catalog.get("packages", []),
                "references": [
                    ref
                    for ref in catalog.get("references", [])
                    if stage in ref.get("stages", [])
                ],
            },
        }
        system = (
            "You are a Manim CodeGen skill router. Decide which skill reference ids "
            "are semantically relevant for this single stage. Use only metadata in "
            "the provided catalog; do not request or infer full reference text. "
            "Return JSON only with keys: selected_reference_ids, rejected_reference_ids, "
            "confidence, reason. Mandatory and strong refs are guardrails and may be "
            "repeated, but they cannot be removed by you."
        )
        try:
            raw = self.client.generate_text(
                system,
                [{"type": "input_text", "text": _compact_json_text(router_payload)}],
                max_retries=1,
            )
            payload = _extract_json_object(raw)
            decision = RouterDecision(
                selected_reference_ids=tuple(
                    str(item).strip()
                    for item in payload.get("selected_reference_ids", [])
                    if str(item).strip()
                ),
                rejected_reference_ids=tuple(
                    str(item).strip()
                    for item in payload.get("rejected_reference_ids", [])
                    if str(item).strip()
                ),
                confidence=float(payload.get("confidence", 0.0) or 0.0),
                reason=str(payload.get("reason", "") or ""),
                raw_response=raw,
            )
        except Exception as exc:
            decision = RouterDecision(error=str(exc))
        return merge_router_decision(selection, decision)

    def _repair_post_generation_contract(
        self,
        *,
        code: str,
        teaching_plan: Dict,
        output_language: str,
        issues: List[str],
        selection: SkillSelection,
        capability_contract: SceneCapabilityContract,
    ) -> str:
        execution_contract = compile_codegen_execution_contract(teaching_plan)
        user_sections = [
            PromptSection("output_language", _build_output_language_prompt(output_language)),
            PromptSection(
                "scene_capability_contract",
                capability_contract.prompt_text(),
            ),
            PromptSection(
                "contract_issues",
                "## Post-generation contract mismatches\n"
                + "\n".join(f"- {issue}" for issue in issues),
            ),
            PromptSection(
                "teaching_plan",
                "## Teaching plan contract to preserve\n"
                + _compact_json_text(execution_contract),
            ),
        ]
        if selection.reference_ids:
            user_sections.append(
                PromptSection(
                    "selected_skill_refs",
                    build_manim_skill_context(selection),
                )
            )
        user_sections.append(
            PromptSection(
                "original_code",
                f"## Generated code to repair\n```python\n{code}\n```",
            )
        )
        bundle = _build_prompt_bundle(
            system=_SYSTEM_POST_GENERATION_CONTRACT_REPAIR,
            sections=user_sections,
            selection=selection,
        )
        try:
            raw = self._call(bundle.system, [{"type": "input_text", "text": bundle.user_text()}], max_retries=1)
            repaired = _extract_code(raw)
            if not _post_generation_contract_issues(
                repaired,
                teaching_plan,
                capability_contract=capability_contract,
            ):
                return repaired
        except Exception:
            return code
        return code

    def _finalize_generated_code(
        self,
        code: str,
        *,
        teaching_plan: Optional[Dict],
        output_language: str,
        selection: SkillSelection,
    ) -> str:
        capability_contract = compile_scene_capability_contract(
            selection.reference_ids,
            teaching_plan,
        )
        issues = _post_generation_contract_issues(
            code,
            teaching_plan,
            capability_contract=capability_contract,
        )
        if not issues:
            return code
        plan = teaching_plan if isinstance(teaching_plan, dict) else {}
        repair_selection = self._select_manim_skills(
            "contract_fix",
            teaching_plan=plan,
            diagnostics=issues,
            code=code,
        )
        return self._repair_post_generation_contract(
            code=code,
            teaching_plan=plan,
            output_language=output_language,
            issues=issues,
            selection=repair_selection,
            capability_contract=capability_contract,
        )

    def fix_with_tools(
        self,
        *,
        run_dir: Path,
        code: str,
        output_language: str,
        repair_kind: str,
        error_context: str,
        code_eval_report: Optional[Dict] = None,
    ) -> Optional[str]:
        """Patch-first repair via read/search/apply_patch under run_dir. None => use full-file fix."""
        ms = get_manim_settings()
        if not getattr(ms, "tool_fix_enabled", False):
            return None
        if not getattr(ms, "tool_fix_run_dir_only", True):
            return None

        run_dir = run_dir.resolve()
        scene_file = SCENE_PACK_TOOL_FILENAME
        target = run_dir / scene_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")

        return self._tool_fix_existing_file(
            run_dir=run_dir,
            target_file=scene_file,
            output_language=output_language,
            repair_kind=repair_kind,
            error_context=error_context,
            code_eval_report=code_eval_report,
        )

    def _tool_fix_existing_file(
        self,
        *,
        run_dir: Path,
        target_file: str,
        output_language: str,
        repair_kind: str,
        error_context: str,
        code_eval_report: Optional[Dict] = None,
    ) -> Optional[str]:
        ms = get_manim_settings()
        if not getattr(ms, "tool_fix_enabled", False):
            return None
        if not getattr(ms, "tool_fix_run_dir_only", True):
            return None

        run_dir = run_dir.resolve()
        target = run_dir / target_file
        if not target.exists():
            return None

        runtime = ManimToolRuntime(run_dir)
        patch_count = 0
        meta: Dict[str, Any] = {
            "stopped_reason": "not_started",
            "fallback_required": False,
            "finish_summary": "",
            "tool_rounds": 0,
            "tool_calls": 0,
        }
        max_patches = max(1, int(getattr(ms, "tool_fix_max_patches", 12)))
        max_patch_bytes = max(1024, int(getattr(ms, "tool_fix_max_patch_bytes", 256_000)))

        def dispatch(name: str, args: Dict[str, Any]) -> ToolResult:
            nonlocal patch_count
            requested_path = str(args.get("path", ""))
            if name in {"read_file", "search_file", "apply_patch"} and requested_path != target_file:
                return ToolResult(
                    False,
                    f"tool access is restricted to `{target_file}` during scene-file repair",
                    {"path": requested_path, "allowed_path": target_file},
                )
            if name == "apply_patch":
                if patch_count >= max_patches:
                    return ToolResult(
                        False,
                        "max_patch_budget_exceeded",
                        {"max": max_patches},
                    )
                r = runtime.apply_patch(
                    str(args.get("path", "")),
                    str(args.get("old_text", "")),
                    str(args.get("new_text", "")),
                    max_patch_bytes=int(args.get("max_patch_bytes", max_patch_bytes)),
                )
                if r.ok:
                    patch_count += 1
                return r
            return runtime.dispatch(name, args)

        tools = build_openai_tool_schemas(max_patch_bytes)
        system = _build_system_tool_repair_for_file(target_file)
        user_parts = [
            _build_output_language_prompt(output_language),
            f"## Repair kind\n{repair_kind}",
            "## Error / context\n```\n" + (error_context[-8000:] if error_context else "") + "\n```",
        ]
        if code_eval_report is not None:
            user_parts.append(
                "## Pre-render code_eval report\n```json\n"
                + json.dumps(code_eval_report, ensure_ascii=False, indent=2)[:12000]
                + "\n```"
            )
        user_parts.append(
            f"## Scene file\nThe full code is on disk at `{target_file}` (relative to run_dir). "
            "Edit it only via tools."
        )
        content: list = [{"type": "input_text", "text": "\n\n".join(user_parts)}]

        try:
            text, meta = self.client.generate_with_tool_loop(
                system,
                content,
                tools=tools,
                dispatch=dispatch,
                max_iterations=max(1, int(getattr(ms, "tool_fix_max_iterations", 8))),
            )
        except Exception as exc:
            meta.update(
                {
                    "stopped_reason": "tool_loop_exception",
                    "fallback_required": True,
                    "finish_summary": str(exc),
                }
            )
            self._last_tool_fix_meta = dict(meta)
            return None

        inferred_stopped_reason = meta.get("stopped_reason")
        if not inferred_stopped_reason and "fallback_required" in meta:
            inferred_stopped_reason = "finish_repair"
        meta = {
            "stopped_reason": str(inferred_stopped_reason or "unknown"),
            "fallback_required": bool(meta.get("fallback_required", False)),
            "finish_summary": str(meta.get("finish_summary", meta.get("summary", ""))),
            "tool_rounds": int(meta.get("tool_rounds", 0) or 0),
            "tool_calls": int(meta.get("tool_calls", 0) or 0),
        }
        self._last_tool_fix_meta = dict(meta)
        if meta.get("fallback_required"):
            return None

        if target.exists():
            out = target.read_text(encoding="utf-8")
            if out.strip():
                return out

        raw_text = (text or "").strip()
        if raw_text:
            extracted = _extract_code(raw_text)
            if extracted.strip():
                return extracted
        return None

    def generate(
        self,
        request_text: str,
        image_path: Optional[Path] = None,
        teaching_plan: Optional[Dict] = None,
        output_language: str = "en",
        *,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
    ) -> str:
        """Generate Manim code from a student request (text, optionally image)."""
        selection = self._select_manim_skills(
            "generate",
            request_text=request_text,
            teaching_plan=teaching_plan,
        )
        user_sections = [
            PromptSection("output_language", _build_output_language_prompt(output_language)),
            PromptSection("student_request", f"## Student request\n{request_text}"),
        ]
        if teaching_plan:
            codegen_context = compile_codegen_execution_contract(teaching_plan)
            render_scope_prompt = _build_render_scope_prompt(teaching_plan)
            if render_scope_prompt:
                user_sections.append(PromptSection("render_scope", render_scope_prompt))
            user_sections.append(
                PromptSection(
                    "teaching_plan",
                    "## Teaching plan\n" + _compact_json_text(codegen_context),
                )
            )
            opening_prompt = _build_opening_prompt(teaching_plan)
            if opening_prompt:
                user_sections.append(PromptSection("opening_plan", opening_prompt))
            problem_intake_prompt = _build_problem_intake_prompt(teaching_plan)
            if problem_intake_prompt:
                user_sections.append(PromptSection("problem_intake_plan", problem_intake_prompt))
            user_sections.append(
                PromptSection(
                    "selected_skill_refs",
                    build_manim_skill_context(selection),
                )
            )
            user_sections.append(
                PromptSection(
                    "required_teaching_plan_execution",
                    "## Required teaching-plan execution\n"
                    "Compile the teaching execution contract into concrete Manim behavior. "
                    "Use the selected skill context for the stage-specific teaching, layout, graph, annotation, formula, motion, and repair rules. "
                    "Preserve every render-scope section id in order. Execute, rather than redesign, each included section's student question, teacher move, concrete example, visual and representation plan, board plan, narration goal, takeaway, check for understanding, and transition. Render opening and closing only when the scope explicitly includes them.",
                )
            )
            user_sections.append(PromptSection("local_assets", _build_local_asset_prompt(teaching_plan)))
            theme_prompt = _build_selected_theme_prompt(teaching_plan)
            if theme_prompt:
                user_sections.append(PromptSection("selected_theme", theme_prompt))
            fast_path_reference_prompt = _build_fast_path_reference_prompt(teaching_plan)
            if fast_path_reference_prompt:
                user_sections.append(PromptSection("fast_path_reference", fast_path_reference_prompt))
        elif selection.reference_ids:
            user_sections.append(
                PromptSection(
                    "selected_skill_refs",
                    build_manim_skill_context(selection),
                )
            )
        camera_execution_prompt = _build_camera_execution_prompt(
            selection,
            teaching_plan,
        )
        if camera_execution_prompt:
            user_sections.append(PromptSection("camera_movement_execution", camera_execution_prompt))
        capability_contract = compile_scene_capability_contract(
            selection.reference_ids,
            teaching_plan,
        )
        user_sections.append(
            PromptSection(
                "scene_capability_contract",
                capability_contract.prompt_text(),
            )
        )
        user_sections.append(PromptSection("output_structure", "## Output structure\nFollow the Scene Pack contract exactly."))
        user_sections.append(
            PromptSection(
                "output_prefix_requirement",
                "## Output prefix requirement\n"
                "Start the file with a valid Scene Pack skeleton as early as possible: imports, top-level `SCENE_MANIFEST`, "
                "`LessonBase`, then numbered wrapper scenes. Do not spend the early output on a single-scene script, prose, or helper-only code before `SCENE_MANIFEST` appears.",
            )
        )
        user_sections.append(
            PromptSection(
                "implementation_priority",
                "## Implementation priority\n"
                "Use the selected skills as the detailed procedure. Keep the first segment independently renderable early, avoid unnecessary helper coupling, and prefer a compact correct implementation over an overloaded one.",
            )
        )
        user_sections.append(
            PromptSection(
                "acceptance_checklist",
                _build_codegen_acceptance_checklist(teaching_plan),
            )
        )
        bundle = _build_prompt_bundle(
            system=_SYSTEM_GENERATE,
            sections=user_sections,
            selection=selection,
        )
        self._last_prompt_audit = _prompt_audit(bundle, selection=selection, teaching_plan=teaching_plan)
        _emit_skill_selection_event(on_event, self._last_prompt_audit)
        content: list = [{"type": "input_text", "text": bundle.user_text()}]
        if image_path and image_path.exists():
            content.append({
                "type": "input_image",
                "image_url": _image_to_data_url(image_path),
            })
        streamed_raw = ""

        def _codegen_stream_bridge(delta: str) -> None:
            nonlocal streamed_raw
            if not delta:
                return
            streamed_raw += delta
            if on_delta is not None:
                on_delta(delta)

            sanitized = sanitize_streaming_code(streamed_raw)
            restart_detected = sanitized != streamed_raw
            if not restart_detected:
                return
            try:
                spec = parse_scene_pack(sanitized)
            except Exception:
                return
            if spec.manifest:
                raise StreamTerminated()

        raw = self._call(
            bundle.system,
            content,
            on_delta=_codegen_stream_bridge,
            on_event=on_event,
        )
        extracted = _extract_code(raw)
        sanitized = sanitize_streaming_code(extracted)
        try:
            spec = parse_scene_pack(sanitized)
        except Exception:
            parseable_prefix = extract_parseable_prefix(sanitized)
            if parseable_prefix and parseable_prefix != sanitized:
                try:
                    prefix_spec = parse_scene_pack(parseable_prefix)
                except Exception:
                    prefix_spec = None
                if prefix_spec is not None and prefix_spec.manifest:
                    return self._finalize_generated_code(
                        parseable_prefix,
                        teaching_plan=teaching_plan,
                        output_language=output_language,
                        selection=selection,
                    )
            recovered = recover_scene_pack_skeleton(sanitized)
            if recovered is not None:
                return self._finalize_generated_code(
                    recovered,
                    teaching_plan=teaching_plan,
                    output_language=output_language,
                    selection=selection,
                )
            recovered_missing_manifest = _recover_missing_manifest_scene_pack(sanitized)
            if recovered_missing_manifest is not None:
                return self._finalize_generated_code(
                    recovered_missing_manifest,
                    teaching_plan=teaching_plan,
                    output_language=output_language,
                    selection=selection,
                )
            return extracted
        final_code = sanitized if spec.manifest else extracted
        return self._finalize_generated_code(
            final_code,
            teaching_plan=teaching_plan,
            output_language=output_language,
            selection=selection,
        )

    def fix(self, code: str, error_log: str, output_language: str = "en") -> str:
        """Fix code that failed to render, given the error output."""
        selection = self._select_manim_skills("fix", diagnostics=error_log, code=code)
        content: list = [{
            "type": "input_text",
            "text": (
                _build_output_language_prompt(output_language)
                + "\n\n"
                + build_manim_skill_context(selection)
                + "\n\n"
                f"## Original code\n```python\n{code}\n```\n\n"
                f"## Render error\n```\n{error_log[-3000:]}\n```"
            ),
        }]
        raw = self._call(_SYSTEM_FIX, content)
        return _extract_code(raw)

    def fix_from_code_eval(
        self,
        code: str,
        code_eval_report: Dict,
        output_language: str = "en",
    ) -> str:
        """Fix code based on the pre-render code-eval report."""
        selection = self._select_manim_skills("code_eval_fix", diagnostics=code_eval_report, code=code)
        content: list = [{
            "type": "input_text",
            "text": (
                _build_output_language_prompt(output_language)
                + "\n\n"
                + build_manim_skill_context(selection)
                + "\n\n"
                + f"## Original code\n```python\n{code}\n```\n\n"
                + "## Pre-render code_eval report\n```json\n"
                + json.dumps(code_eval_report, ensure_ascii=False, indent=2)
                + "\n```"
            ),
        }]
        raw = self._call(_SYSTEM_CODE_EVAL_FIX, content)
        return _extract_code(raw)

    def fix_segment_method(
        self,
        *,
        segment_id: str,
        method_name: str,
        manifest_source: str,
        wrapper_scene_source: str,
        section_method_source: str,
        helper_method_sources: List[str],
        error_log: str,
        output_language: str = "en",
    ) -> str:
        """Repair one failed section method and return the replacement def block."""
        selection = self._select_manim_skills("segment_fix", diagnostics=error_log, code=section_method_source)
        helpers_block = "\n\n".join(helper_method_sources).strip()
        content: list = [{
            "type": "input_text",
            "text": (
                _build_output_language_prompt(output_language)
                + "\n\n"
                + build_manim_skill_context(selection)
                + "\n\n"
                + f"## Target segment id\n{segment_id}\n\n"
                + f"## Target method name\n{method_name}\n\n"
                + f"## Scene manifest\n```python\n{manifest_source}\n```\n\n"
                + f"## Wrapper scene\n```python\n{wrapper_scene_source}\n```\n\n"
                + f"## Target section method\n```python\n{section_method_source}\n```\n\n"
                + (
                    "## Read-only shared helper methods\n```python\n"
                    + helpers_block
                    + "\n```\n\n"
                    if helpers_block
                    else ""
                )
                + f"## Render error for this segment\n```\n{error_log[-3000:]}\n```"
            ),
        }]
        raw = self._call(_SYSTEM_SEGMENT_FIX, content)
        payload = _extract_json_object(raw)
        returned_name = str(payload.get("method_name", "")).strip()
        updated_method_code = str(payload.get("updated_method_code", "")).strip()
        if returned_name != method_name:
            raise ValueError(
                f"Segment fix returned method `{returned_name}`, expected `{method_name}`."
            )
        if not updated_method_code:
            raise ValueError("Segment fix response did not include `updated_method_code`.")
        return updated_method_code

    def fix_segment_method_from_validation(
        self,
        *,
        segment_id: str,
        method_name: str,
        manifest_source: str,
        wrapper_scene_source: str,
        section_method_source: str,
        helper_method_sources: List[str],
        validation_report: Dict,
        output_language: str = "en",
    ) -> str:
        """Repair one section method from validation diagnostics and return the replacement def block."""
        selection = self._select_manim_skills("validation_fix", diagnostics=validation_report, code=section_method_source)
        helpers_block = "\n\n".join(helper_method_sources).strip()
        content: list = [{
            "type": "input_text",
            "text": (
                _build_output_language_prompt(output_language)
                + "\n\n"
                + build_manim_skill_context(selection)
                + "\n\n"
                + f"## Target segment id\n{segment_id}\n\n"
                + f"## Target method name\n{method_name}\n\n"
                + f"## Scene manifest\n```python\n{manifest_source}\n```\n\n"
                + f"## Wrapper scene\n```python\n{wrapper_scene_source}\n```\n\n"
                + f"## Target section method\n```python\n{section_method_source}\n```\n\n"
                + (
                    "## Read-only shared helper methods\n```python\n"
                    + helpers_block
                    + "\n```\n\n"
                    if helpers_block
                    else ""
                )
                + "## Validation report\n```json\n"
                + json.dumps(validation_report, ensure_ascii=False, indent=2)
                + "\n```"
            ),
        }]
        raw = self._call(_SYSTEM_SEGMENT_VALIDATION_FIX, content)
        payload = _extract_json_object(raw)
        returned_name = str(payload.get("method_name", "")).strip()
        updated_method_code = str(payload.get("updated_method_code", "")).strip()
        if returned_name != method_name:
            raise ValueError(
                f"Validation fix returned method `{returned_name}`, expected `{method_name}`."
            )
        if not updated_method_code:
            raise ValueError("Validation fix response did not include `updated_method_code`.")
        return updated_method_code

    def narrate(self, code: str, request_text: str, output_language: str = "en") -> List[str]:
        """Generate a narration script (list of paragraphs) for the video."""
        language = normalize_output_language(output_language)
        language_name = output_language_name(language)
        sentence_hint = (
            "15-40 Chinese characters"
            if language == "zh"
            else "1-2 short sentences, usually 6-18 English words total"
        )
        content: list = [{
            "type": "input_text",
            "text": (
                _build_output_language_prompt(language)
                + "\n\n"
                f"## Student request\n{request_text}\n\n"
                f"## Manim code\n```python\n{code}\n```"
            ),
        }]
        system = (
            f"You are a warm, clear {language_name}-speaking teacher narrating an educational "
            "animation video.  Based on the Manim code and the student's question, "
            f"write a narration script in {language_name}.\n\n"
            "Rules:\n"
            "- Write 5-10 short paragraphs, one for each visual section.\n"
            f"- Each paragraph should be {sentence_hint}.\n"
            "- Match the pacing of the animation: brief for visual parts, detailed "
            "for formula/concept explanations.\n"
            "- Use conversational, encouraging tone (like talking to a student).\n"
            "- Do NOT include timestamps, stage directions, or code references.\n"
            "- Output ONLY a JSON array of strings, like:\n"
            '  ["First narration beat", "Second narration beat", ...]\n'
        )
        raw = self._call(system, content)
        try:
            import json as _json
            text = raw.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            left = text.find("[")
            right = text.rfind("]")
            if left >= 0 and right > left:
                return _json.loads(text[left:right + 1])
        except Exception:
            pass
        return [p.strip() for p in raw.split("\n") if p.strip()]

    def improve(
        self,
        code: str,
        eval_report: Dict,
        keyframe_paths: Optional[List[Path]] = None,
        teaching_plan: Optional[Dict] = None,
        output_language: str = "en",
    ) -> str:
        """Improve code based on evaluation feedback + optional keyframe images."""
        feedback = _build_actionable_feedback(eval_report)
        selection = self._select_manim_skills(
            "improve",
            teaching_plan=teaching_plan,
            diagnostics=eval_report,
            code=code,
        )
        prompt_parts = [_build_output_language_prompt(output_language)]
        prompt_parts.append(build_manim_skill_context(selection))
        prompt_parts.append(
            f"## Original code\n```python\n{code}\n```\n\n## Evaluation feedback\n{feedback}"
        )
        if teaching_plan:
            prompt_parts.append(
                "## Teaching plan to preserve\n"
                + json.dumps(teaching_plan, ensure_ascii=False, indent=2)
            )
            prompt_parts.append(_build_local_asset_prompt(teaching_plan))
            theme_prompt = _build_selected_theme_prompt(teaching_plan)
            if theme_prompt:
                prompt_parts.append(theme_prompt)
        content: list = [{
            "type": "input_text",
            "text": "\n\n".join(prompt_parts),
        }]
        if keyframe_paths:
            content.append({
                "type": "input_text",
                "text": "## Keyframe screenshots (so you can SEE the problems):",
            })
            for kf in keyframe_paths:
                if kf.exists():
                    content.append({
                        "type": "input_image",
                        "image_url": _image_to_data_url(kf),
                    })
        raw = self._call(_SYSTEM_IMPROVE, content)
        return _extract_code(raw)
