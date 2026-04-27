"""Storyboard / director agent for routed hybrid Manim + Remotion assembly."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .agent_skills import build_remotion_skill_prompt
from .llm import LLMClient, LLMConfig


_SYSTEM_STORYBOARD = """\
You are a creative director for educational animation.

You are designing a TRUE HYBRID rendering plan that deeply fuses:
- Manim for mathematically dense, spatial, equation-heavy, or graph/diagram reasoning
- Remotion for motivation, intuition, misconceptions, concept explanation, transitions, and visual polish

Do NOT write code. Return ONLY a JSON object.

Architecture:
- Each teaching-plan section must be routed to ONE primary backend.
- Use `manim_chunk` when a section needs equations, graphs, geometry, derivations, coordinates, vectors, or precise animated reasoning.
- Use `concept_card` when a section is mainly motivation, intuition, misconception correction, comparison, analogy, causal explanation, or recap.
- Manim renders ONE continuous lesson video covering ONLY the sections routed to Manim.
- Remotion renders its own concept sections directly. It is not just a wrapper around a full Manim video.

Return JSON with this structure:
{
  "render_mode": "hybrid",
  "theme": {
    "visual_tone": "...",
    "accent_color": "#...",
    "accent_color_2": "#...",
    "background": "#...",
    "panel_background": "rgba(...)",
    "font_family": "...",
    "subtitle_style": "...",
    "transition_style": "..."
  },
  "scenes": [
    {
      "id": "intro_card",
      "backend": "remotion",
      "type": "title_card",
      "title": "SHORT punchy title",
      "body": "One compelling sentence.",
      "bullets": ["short bullet 1", "short bullet 2", "short bullet 3"],
      "duration_sec": 5,
      "source_section_id": null,
      "visual_notes": "..."
    },
    {
      "id": "section_1_scene",
      "backend": "remotion",
      "type": "concept_card",
      "title": "SHORT section title",
      "body": "One-sentence concept explanation.",
      "bullets": ["point A", "point B", "point C"],
      "duration_sec": 7,
      "source_section_id": "section_1",
      "visual_notes": "...",
      "route_reason": "Why Remotion is better for this section."
    },
    {
      "id": "section_2_scene",
      "backend": "manim",
      "type": "manim_chunk",
      "title": "SHORT section title",
      "body": "One-sentence description of the animated reasoning.",
      "bullets": ["optional point A", "optional point B"],
      "duration_sec": 0,
      "source_section_id": "section_2",
      "visual_notes": "...",
      "route_reason": "Why Manim is better for this section."
    },
    {
      "id": "summary_card",
      "backend": "remotion",
      "type": "summary_card",
      "title": "SHORT takeaway",
      "body": "The one idea to remember.",
      "bullets": ["takeaway 1", "takeaway 2", "takeaway 3"],
      "duration_sec": 5,
      "source_section_id": null,
      "visual_notes": "..."
    }
  ],
  "subtitle_plan": {
    "mode": "chaptered",
    "overlay_core_video": true,
    "chapter_labels": ["..."],
    "highlight_keywords": ["..."]
  },
  "assembly": {
    "primary_delivery": "remotion",
    "interleaved": true,
    "notes": "..."
  }
}

Rules:
- Use English for all text fields.
- Route sections by instructional need, not by alternating blindly.
- Keep the scene order aligned with the teaching plan order.
- Bodies must be ONE sentence max.
- Bullets must be terse.
- Do NOT include LaTeX or math markup in any text field.
- `concept_card` scenes should usually be 5-9 seconds. `manim_chunk` duration can be 0.
- Include at least one `manim_chunk` unless the lesson is clearly non-mathematical.
- Make the final experience feel like one lesson, not two stitched systems.
"""


_MATHY_KEYWORDS = {
    "equation",
    "formula",
    "graph",
    "plot",
    "axis",
    "axes",
    "coordinate",
    "curve",
    "function",
    "geometry",
    "triangle",
    "circle",
    "vector",
    "matrix",
    "derivative",
    "integral",
    "slope",
    "proof",
    "derive",
    "derivation",
    "parameter",
    "transform",
}

_CONCEPT_KEYWORDS = {
    "intuition",
    "motivate",
    "motivation",
    "misconception",
    "why it matters",
    "story",
    "analogy",
    "compare",
    "prediction",
    "everyday",
    "real-world",
    "summary",
    "recap",
    "hook",
}


def _extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data

    raise ValueError("Failed to parse storyboard JSON from model output")


def _text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        value = value.strip()
        return value or default
    if value is None:
        return default
    return str(value).strip() or default


def _string_list(items: Any, fallback: Optional[List[str]] = None) -> List[str]:
    values: List[str] = []
    if isinstance(items, list):
        for item in items:
            text = _text(item)
            if text:
                values.append(text)
    return values or list(fallback or [])


def _normalize_theme(raw: Any) -> Dict[str, str]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "visual_tone": _text(raw.get("visual_tone"), "modern classroom explainer"),
        "accent_color": _text(raw.get("accent_color"), "#7C3AED"),
        "accent_color_2": _text(raw.get("accent_color_2"), "#22D3EE"),
        "background": _text(raw.get("background"), "#0F172A"),
        "panel_background": _text(raw.get("panel_background"), "rgba(15, 23, 42, 0.72)"),
        "font_family": _text(raw.get("font_family"), "Inter, Arial, sans-serif"),
        "subtitle_style": _text(raw.get("subtitle_style"), "centered lower-third with keyword emphasis"),
        "transition_style": _text(raw.get("transition_style"), "clean slides with soft fades"),
    }


def _keyword_score(text: str, keywords: set[str]) -> int:
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def _default_section_backend(section: Dict[str, Any], index: int, total: int) -> str:
    joined = " ".join(
        _text(section.get(name))
        for name in (
            "title",
            "teacher_goal",
            "teacher_move",
            "student_question",
            "concrete_example",
            "visual_strategy",
            "board_plan",
            "key_takeaway",
            "transition",
        )
    )
    math_score = _keyword_score(joined, _MATHY_KEYWORDS)
    concept_score = _keyword_score(joined, _CONCEPT_KEYWORDS)
    if index == 0 and math_score == 0:
        concept_score += 2
    if index == total - 1 and math_score <= 1:
        concept_score += 1
    return "manim" if math_score > concept_score else "remotion"


def _fallback_section_scene(section: Dict[str, Any], index: int, total: int) -> Dict[str, Any]:
    backend = _default_section_backend(section, index, total)
    title = _text(section.get("title"), f"Part {index + 1}")
    body = (
        _text(section.get("teacher_goal"))
        or _text(section.get("key_takeaway"))
        or _text(section.get("teacher_move"))
    )
    bullets = [
        _text(section.get("student_question")),
        _text(section.get("concrete_example")),
        _text(section.get("key_takeaway")),
    ]
    visual_notes = _text(section.get("visual_strategy")) or _text(section.get("board_plan"))
    return {
        "id": f"{_text(section.get('id'), f'section_{index + 1}')}_scene",
        "backend": backend,
        "type": "manim_chunk" if backend == "manim" else "concept_card",
        "title": title,
        "body": body,
        "bullets": [item for item in bullets if item][:3],
        "duration_sec": 0 if backend == "manim" else 7,
        "source_section_id": _text(section.get("id"), f"section_{index + 1}"),
        "visual_notes": visual_notes,
        "route_reason": (
            "Use Manim for exact visual reasoning and equation-linked motion."
            if backend == "manim"
            else "Use Remotion for concept framing, intuition, and concise explanation."
        ),
    }


def _fallback_scenes(teaching_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    sections = teaching_plan.get("sections") if isinstance(teaching_plan.get("sections"), list) else []
    highlights = [_text(section.get("title")) for section in sections[:3] if _text(section.get("title"))]
    closing = teaching_plan.get("closing") if isinstance(teaching_plan.get("closing"), dict) else {}
    scenes: List[Dict[str, Any]] = [
        {
            "id": "intro_card",
            "backend": "remotion",
            "type": "title_card",
            "title": _text(teaching_plan.get("lesson_goal"), "What you will understand"),
            "body": _text(
                teaching_plan.get("teaching_promise"),
                "We will turn the core idea into a clear visual story.",
            ),
            "bullets": highlights or ["See the intuition", "Follow the mechanism", "Leave with one big idea"],
            "duration_sec": 3,
            "source_section_id": None,
            "visual_notes": "Open with a bold title, concise roadmap, and modern gradient motion background.",
        }
    ]
    scenes.extend(
        _fallback_section_scene(section, index, len(sections))
        for index, section in enumerate(sections)
        if isinstance(section, dict)
    )
    scenes.append(
        {
            "id": "summary_card",
            "backend": "remotion",
            "type": "summary_card",
            "title": "One takeaway to keep",
            "body": _text(closing.get("summary"), "End with the key sentence students should remember."),
            "bullets": [
                _text(closing.get("transfer_question"), "How would the same idea show up elsewhere?"),
                _text(closing.get("after_class_prompt"), "Try explaining the main idea in your own words."),
            ],
            "duration_sec": 4,
            "source_section_id": None,
            "visual_notes": "Finish with a clean summary card and a call-to-think prompt.",
        }
    )
    return scenes


def _normalize_scene(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    scene_type = _text(item.get("type"), "title_card")
    backend = _text(item.get("backend"), "remotion").lower()
    if scene_type == "manim_chunk":
        backend = "manim"
    elif scene_type in {"title_card", "summary_card", "concept_card", "chapter_card"}:
        backend = "remotion"

    default_duration = 0 if scene_type == "manim_chunk" else 6
    normalized_type = "concept_card" if scene_type == "chapter_card" else scene_type
    return {
        "id": _text(item.get("id"), f"scene_{index}"),
        "backend": backend,
        "type": normalized_type,
        "title": _text(item.get("title"), f"Scene {index}"),
        "body": _text(item.get("body"), ""),
        "bullets": _string_list(item.get("bullets")),
        "duration_sec": max(0, int(float(item.get("duration_sec", default_duration) or default_duration))),
        "source_section_id": _text(item.get("source_section_id")) or None,
        "visual_notes": _text(item.get("visual_notes"), ""),
        "route_reason": _text(item.get("route_reason"), ""),
    }


def _ensure_manim_scene(scenes: List[Dict[str, Any]], teaching_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    if any(scene["type"] == "manim_chunk" for scene in scenes):
        return scenes
    sections = teaching_plan.get("sections") if isinstance(teaching_plan.get("sections"), list) else []
    if not sections:
        return scenes
    forced = _fallback_section_scene(sections[min(1, len(sections) - 1)], min(1, len(sections) - 1), len(sections))
    forced["backend"] = "manim"
    forced["type"] = "manim_chunk"
    forced["duration_sec"] = 0
    insert_at = 1 if scenes and scenes[0]["type"] == "title_card" else 0
    scenes.insert(insert_at, forced)
    return scenes


def _normalize_scenes(raw: Any, teaching_plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return _ensure_manim_scene(_fallback_scenes(teaching_plan), teaching_plan)

    normalized = [_normalize_scene(item, index) for index, item in enumerate(raw, start=1) if isinstance(item, dict)]
    if not normalized:
        return _ensure_manim_scene(_fallback_scenes(teaching_plan), teaching_plan)

    intro = next((scene for scene in normalized if scene["type"] == "title_card"), None)
    outro = next((scene for scene in reversed(normalized) if scene["type"] == "summary_card"), None)
    middle = [scene for scene in normalized if scene["type"] not in {"title_card", "summary_card"}]
    fallback = _fallback_scenes(teaching_plan)

    scenes: List[Dict[str, Any]] = [intro or fallback[0]]
    if middle:
        scenes.extend(middle)
    else:
        scenes.extend(scene for scene in fallback if scene["type"] not in {"title_card", "summary_card"})
    scenes.append(outro or fallback[-1])
    return _ensure_manim_scene(scenes, teaching_plan)


def _normalize_storyboard(raw: Dict[str, Any], teaching_plan: Dict[str, Any]) -> Dict[str, Any]:
    subtitle_raw = raw.get("subtitle_plan") if isinstance(raw.get("subtitle_plan"), dict) else {}
    assembly_raw = raw.get("assembly") if isinstance(raw.get("assembly"), dict) else {}
    sections = teaching_plan.get("sections") if isinstance(teaching_plan.get("sections"), list) else []
    section_titles = [_text(section.get("title")) for section in sections if _text(section.get("title"))]
    return {
        "render_mode": "hybrid",
        "theme": _normalize_theme(raw.get("theme")),
        "scenes": _normalize_scenes(raw.get("scenes"), teaching_plan),
        "subtitle_plan": {
            "mode": _text(subtitle_raw.get("mode"), "chaptered"),
            "overlay_core_video": bool(subtitle_raw.get("overlay_core_video", True)),
            "chapter_labels": _string_list(subtitle_raw.get("chapter_labels"), fallback=section_titles[:6]),
            "highlight_keywords": _string_list(subtitle_raw.get("highlight_keywords"), fallback=section_titles[:4]),
        },
        "assembly": {
            "primary_delivery": _text(assembly_raw.get("primary_delivery"), "remotion"),
            "wrap_manim_video": bool(assembly_raw.get("wrap_manim_video", True)),
            "include_intro": bool(assembly_raw.get("include_intro", True)),
            "include_outro": bool(assembly_raw.get("include_outro", True)),
            "notes": _text(
                assembly_raw.get("notes"),
                "Route concept-heavy sections to Remotion and reserve Manim for mathematically dense visual reasoning.",
            ),
        },
    }


class StoryboardAgent:
    """LLM-backed director agent that creates a routed hybrid assembly plan."""

    def __init__(self, llm_config: LLMConfig):
        self.client = LLMClient(llm_config)

    def plan(self, request_text: str, teaching_plan: Dict[str, Any]) -> Dict[str, Any]:
        user_prompt = (
            f"## Student request\n{request_text}\n\n"
            f"## Teaching plan\n{json.dumps(teaching_plan, ensure_ascii=False, indent=2)}\n\n"
            f"{build_remotion_skill_prompt()}"
        )

        raw_text = self.client.generate_text(
            _SYSTEM_STORYBOARD,
            [{"type": "input_text", "text": user_prompt}],
            max_retries=3,
        )
        return _normalize_storyboard(_extract_json_object(raw_text), teaching_plan)
