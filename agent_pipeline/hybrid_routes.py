"""Validation and normalization for Hybrid teaching-section routes."""

from __future__ import annotations

from typing import Any, Dict


def normalize_hybrid_storyboard_routes(
    teaching_plan: Dict[str, Any],
    storyboard: Dict[str, Any],
) -> Dict[str, Any]:
    """Return one validated route per teaching section in Planner order."""

    sections = (
        teaching_plan.get("sections")
        if isinstance(teaching_plan.get("sections"), list)
        else []
    )
    ordered_section_ids = [
        str(section.get("id") or "").strip()
        for section in sections
        if isinstance(section, dict) and str(section.get("id") or "").strip()
    ]
    if len(ordered_section_ids) != len(set(ordered_section_ids)):
        raise ValueError("Teaching-plan section ids must be unique for hybrid routing.")

    scenes = storyboard.get("scenes") if isinstance(storyboard.get("scenes"), list) else []
    valid_ids = set(ordered_section_ids)
    route_by_section_id: dict[str, Dict[str, Any]] = {}
    for scene in scenes:
        if not isinstance(scene, dict) or scene.get("type") not in {
            "manim_chunk",
            "concept_card",
        }:
            continue
        section_id = str(scene.get("source_section_id") or "").strip()
        if section_id not in valid_ids:
            continue
        existing_route = route_by_section_id.get(section_id)
        if existing_route is not None:
            if existing_route.get("type") != scene.get("type"):
                raise ValueError(
                    "Hybrid storyboard cannot route one teaching-plan section "
                    f"to multiple backends: {section_id}."
                )
            continue
        normalized_route = dict(scene)
        normalized_route["backend"] = (
            "manim" if scene.get("type") == "manim_chunk" else "remotion"
        )
        route_by_section_id[section_id] = normalized_route

    missing_ids = [
        section_id
        for section_id in ordered_section_ids
        if section_id not in route_by_section_id
    ]
    if missing_ids:
        raise ValueError(
            "Hybrid storyboard must route every teaching-plan section exactly once; "
            "missing valid route(s): " + ", ".join(missing_ids)
        )

    normalized_content = [
        route_by_section_id[section_id] for section_id in ordered_section_ids
    ]
    if not any(scene.get("type") == "manim_chunk" for scene in normalized_content):
        raise ValueError(
            "Hybrid storyboard contains no valid Manim section after route validation."
        )

    intro_scene = next(
        (
            dict(scene)
            for scene in scenes
            if isinstance(scene, dict) and scene.get("type") == "title_card"
        ),
        None,
    )
    summary_scene = next(
        (
            dict(scene)
            for scene in reversed(scenes)
            if isinstance(scene, dict) and scene.get("type") == "summary_card"
        ),
        None,
    )
    normalized = dict(storyboard)
    normalized["scenes"] = [
        *([intro_scene] if intro_scene is not None else []),
        *normalized_content,
        *([summary_scene] if summary_scene is not None else []),
    ]
    return normalized
