from __future__ import annotations

from manim import Group, LEFT, RIGHT, Rectangle

from plugins.manim.agent_pipeline.code_eval import _normalize_issue
from plugins.manim.agent_pipeline.lint_error import lint_section_code
from plugins.manim.colortest.narrated_scene import NarratedScene
from plugins.manim.eval_pipeline.fusion import _collect_overlap_review_issues
from plugins.manim.eval_pipeline.vlm_judge import OverlapReviewVerdict, _normalize_overlap_issues


def _issue_categories(report: dict[str, object]) -> set[str]:
    issues = report.get("issues")
    assert isinstance(issues, list)
    return {
        str(issue.get("category"))
        for issue in issues
        if isinstance(issue, dict)
    }


def test_lint_flags_loose_sentence_text_after_fit_body() -> None:
    code = """
from manim import *

class LessonBase(Scene):
    def section_one_example(self):
        body1 = Group(Text("Graph"))
        self.fit_body(body1)
        note = self.get_text("This takeaway should not float below the fitted body.", font_size=22)
        note.next_to(body1, DOWN, buff=0.04)
        self.add(body1, note)
"""

    report = lint_section_code(code)

    categories = _issue_categories(report)
    assert "post_fit_loose_text_overlap_risk" in categories
    assert "tiny_layout_buff_overlap_risk" in categories


def test_runtime_body_overlap_relaxer_separates_top_level_blocks() -> None:
    scene = NarratedScene.__new__(NarratedScene)
    left_block = Rectangle(width=2.0, height=1.0).shift(LEFT * 0.2)
    right_block = Rectangle(width=2.0, height=1.0).shift(RIGHT * 0.2)
    body = Group(left_block, right_block)

    scene._relax_top_level_body_overlaps(body, min_gap=0.14)

    overlap_w, overlap_h = NarratedScene._intersection_size(
        NarratedScene._bbox_edges(left_block),
        NarratedScene._bbox_edges(right_block),
    )
    assert overlap_w <= 0 or overlap_h <= 0


def test_lint_flags_same_anchor_side_label_stack() -> None:
    code = """
from manim import *

class LessonBase(Scene):
    def section_one_example(self):
        dot = Dot()
        label_a = self.get_secondary_text("A", font_size=18)
        label_b = self.get_secondary_text("B", font_size=18)
        label_a.next_to(dot, RIGHT, buff=0.06)
        label_b.next_to(dot, RIGHT, buff=0.06)
        body1 = Group(dot, label_a, label_b)
        self.fit_body(body1)
"""

    report = lint_section_code(code)

    assert "same_anchor_side_label_stack_overlap_risk" in _issue_categories(report)


def test_code_eval_accepts_block_overlap_risk_rule_id() -> None:
    normalized = _normalize_issue(
        {
            "rule_id": "block_overlap_risk",
            "severity": "error",
            "message": "loose text overlaps graph",
        }
    )

    assert normalized["rule_id"] == "block_overlap_risk"
    assert normalized["severity"] == "error"


def test_vlm_overlap_issue_metadata_is_normalized_and_collected() -> None:
    normalized = _normalize_overlap_issues(
        [
            {
                "frame_index": 2,
                "description": "Formula covers the graph label.",
                "overlap_kind": "text_graph",
                "repair_action": "separate_body_blocks",
                "severity": "severe",
            }
        ]
    )

    assert normalized == [
        {
            "frame_index": 2,
            "description": "Formula covers the graph label.",
            "overlap_kind": "text_graph",
            "repair_action": "separate_body_blocks",
            "severity": "severe",
        }
    ]

    issues = _collect_overlap_review_issues(
        OverlapReviewVerdict(
            has_overlap=True,
            overlap_frames=1,
            total_frames=4,
            overlap_ratio=0.25,
            issues=normalized,
            reason="one severe overlap",
        )
    )

    assert issues[0]["taxonomy"] == "hard_bug"
    assert issues[0]["overlap_kind"] == "text_graph"
    assert issues[0]["repair_action"] == "separate_body_blocks"
