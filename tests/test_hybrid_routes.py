from __future__ import annotations

import pytest

from agent_pipeline.hybrid_routes import normalize_hybrid_storyboard_routes


def _plan() -> dict:
    return {"sections": [{"id": "s1"}, {"id": "s2"}]}


def test_routes_drop_unknown_dedupe_and_follow_planner_order() -> None:
    storyboard = {
        "scenes": [
            {"id": "s2", "type": "manim_chunk", "source_section_id": "s2"},
            {"id": "ghost", "type": "manim_chunk", "source_section_id": "ghost"},
            {"id": "s2_copy", "type": "manim_chunk", "source_section_id": "s2"},
            {"id": "s1", "type": "concept_card", "source_section_id": "s1"},
        ]
    }

    normalized = normalize_hybrid_storyboard_routes(_plan(), storyboard)

    assert [scene["id"] for scene in normalized["scenes"]] == ["s1", "s2"]
    assert [scene["backend"] for scene in normalized["scenes"]] == [
        "remotion",
        "manim",
    ]


def test_routes_reject_cross_backend_ownership() -> None:
    storyboard = {
        "scenes": [
            {"id": "s1_card", "type": "concept_card", "source_section_id": "s1"},
            {"id": "s1_manim", "type": "manim_chunk", "source_section_id": "s1"},
            {"id": "s2", "type": "manim_chunk", "source_section_id": "s2"},
        ]
    }

    with pytest.raises(ValueError, match="multiple backends: s1"):
        normalize_hybrid_storyboard_routes(_plan(), storyboard)


def test_routes_reject_all_invalid_manim_ids() -> None:
    storyboard = {
        "scenes": [
            {"id": "s1", "type": "concept_card", "source_section_id": "s1"},
            {"id": "s2", "type": "concept_card", "source_section_id": "s2"},
            {"id": "ghost", "type": "manim_chunk", "source_section_id": "ghost"},
        ]
    }

    with pytest.raises(ValueError, match="no valid Manim section"):
        normalize_hybrid_storyboard_routes(_plan(), storyboard)
