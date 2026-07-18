from __future__ import annotations

from pathlib import Path

import pytest

from agent_pipeline import remotion_renderer


def _storyboard() -> dict:
    return {
        "scenes": [
            {"id": "s1", "type": "manim_chunk", "source_section_id": "s1"},
            {"id": "card", "type": "concept_card", "source_section_id": "card"},
            {"id": "s2", "type": "manim_chunk", "source_section_id": "s2"},
        ]
    }


def _build_props(monkeypatch: pytest.MonkeyPatch, durations: list[dict]) -> dict:
    monkeypatch.setattr(
        remotion_renderer,
        "_video_metadata",
        lambda _path: {"fps": 30.0, "duration_sec": 3.0, "frames": 90.0},
    )
    return remotion_renderer._build_props(
        "request",
        {"sections": [{"id": "s1"}, {"id": "s2"}]},
        _storyboard(),
        Path("source.mp4"),
        Path("runtime"),
        durations,
    )


def test_chunks_use_exact_rendered_segment_durations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    props = _build_props(
        monkeypatch,
        [
            {"segment_id": "s1", "order": 0, "duration_sec": 1.0},
            {"segment_id": "s2", "order": 1, "duration_sec": 2.0},
        ],
    )

    chunks = [segment for segment in props["segments"] if segment["type"] == "manim_chunk"]
    assert [chunk["videoStartFrame"] for chunk in chunks] == [0, 60]
    assert [chunk["durationInFrames"] for chunk in chunks] == [60, 120]


@pytest.mark.parametrize(
    "durations",
    [
        [],
        [{"segment_id": "ghost", "order": 0, "duration_sec": 1.0}],
        [
            {"segment_id": "s1", "order": 0, "duration_sec": 1.0},
            {"segment_id": "s1", "order": 1, "duration_sec": 2.0},
        ],
        [
            {"segment_id": "s2", "order": 0, "duration_sec": 2.0},
            {"segment_id": "s1", "order": 1, "duration_sec": 1.0},
        ],
        [
            {"segment_id": "s1", "order": 1, "duration_sec": 1.0},
            {"segment_id": "s2", "order": 0, "duration_sec": 2.0},
        ],
    ],
)
def test_rejects_non_bijective_segment_duration_mapping(
    monkeypatch: pytest.MonkeyPatch,
    durations: list[dict],
) -> None:
    with pytest.raises(ValueError, match="one-to-one in id and order"):
        _build_props(monkeypatch, durations)
