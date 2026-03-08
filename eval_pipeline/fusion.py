"""
Layer 3 – Fusion scoring and structured report generation.

Combines deterministic CV metrics with (optional) VLM verdicts to produce
a final multi-dimensional quality score and a human-readable report.

Scoring dimensions:
  1. Overlap       – element occlusion / overlap bugs
  2. Rendering     – formula garbling, rendering artefacts
  3. Layout        – screen density, readability
  4. Animation     – motion smoothness, flash events
  5. Colour        – palette consistency across frames
  6. VLM Semantic  – content correctness, intent judgment (optional)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import FusionConfig
from .cv_features import GlobalCVMetrics, SegmentFeatures
from .vlm_judge import VLMVerdict


# =====================================================================
# Per-dimension scores
# =====================================================================

@dataclass
class DimensionScore:
    name: str
    score: float            # 0-1 (1 = perfect)
    passed: bool
    details: str = ""


@dataclass
class EvalReport:
    """Final structured evaluation report for one video."""

    video: str = ""
    duration_sec: float = 0.0
    total_frames: int = 0
    fps: float = 0.0

    # Per-dimension
    dimensions: List[DimensionScore] = field(default_factory=list)

    # Overall
    overall_score: float = 0.0
    overall_passed: bool = False

    # Issue inventory
    issues: List[Dict] = field(default_factory=list)

    # Raw references
    cv_fail_segments: List[str] = field(default_factory=list)
    vlm_fail_segments: List[str] = field(default_factory=list)


# =====================================================================
# Scoring functions
# =====================================================================

def _score_overlap(g: GlobalCVMetrics, segments: List[SegmentFeatures]) -> DimensionScore:
    """Score based on overlap frame ratio and cv_fail segment count."""
    # Penalise: more overlap frames → lower score
    ratio_penalty = min(1.0, g.overlap_frame_ratio / 0.30)  # 30% frames → score 0
    fail_penalty = min(1.0, g.cv_fail_count / 5.0)          # 5 fail segments → score 0
    dur_penalty = min(1.0, g.cv_fail_duration_sec / (g.duration_sec * 0.10 + 1e-6))

    raw = 1.0 - 0.40 * ratio_penalty - 0.35 * fail_penalty - 0.25 * dur_penalty
    score = max(0.0, min(1.0, raw))

    details = (
        f"overlap_frame_ratio={g.overlap_frame_ratio:.3f}, "
        f"cv_fail_count={g.cv_fail_count}, "
        f"cv_fail_duration={g.cv_fail_duration_sec:.1f}s"
    )
    return DimensionScore("overlap", score, score >= 0.70, details)


def _score_rendering(g: GlobalCVMetrics) -> DimensionScore:
    """Score based on OCR-detected rendering artefacts."""
    if g.ocr_artifact_total == 0:
        return DimensionScore("rendering", 1.0, True, "no artefacts detected")

    penalty = min(1.0, g.ocr_artifact_total / 10.0)
    score = max(0.0, 1.0 - penalty)
    details = f"ocr_artifact_total={g.ocr_artifact_total}"
    return DimensionScore("rendering", score, score >= 0.70, details)


def _score_layout(g: GlobalCVMetrics) -> DimensionScore:
    """Score based on layout density distribution."""
    penalty = min(1.0, g.layout_dense_frame_ratio / 0.40)
    score = max(0.0, 1.0 - penalty)
    details = f"dense_frame_ratio={g.layout_dense_frame_ratio:.3f}"
    return DimensionScore("layout", score, score >= 0.60, details)


def _score_animation(g: GlobalCVMetrics) -> DimensionScore:
    """Score based on motion discontinuities and flash events."""
    disc_penalty = min(1.0, g.motion_discontinuity_count / 20.0)
    flash_penalty = min(1.0, g.flash_event_total / 10.0)
    score = max(0.0, 1.0 - 0.6 * disc_penalty - 0.4 * flash_penalty)
    details = (
        f"motion_discontinuities={g.motion_discontinuity_count}, "
        f"flash_events={g.flash_event_total}"
    )
    return DimensionScore("animation", score, score >= 0.60, details)


def _score_color(g: GlobalCVMetrics) -> DimensionScore:
    """Score based on colour palette consistency."""
    if g.total_frames <= 1:
        return DimensionScore("color_consistency", 1.0, True, "single frame")
    event_ratio = g.color_shift_events / g.total_frames
    penalty = min(1.0, event_ratio / 0.05)  # 5% frames with shift → score 0
    score = max(0.0, 1.0 - penalty)
    details = (
        f"shift_events={g.color_shift_events}, "
        f"shift_max={g.color_shift_max:.4f}"
    )
    return DimensionScore("color_consistency", score, score >= 0.70, details)


def _score_vlm(
    verdicts: List[VLMVerdict],
    segments: List[SegmentFeatures],
) -> DimensionScore:
    """Score based on VLM verdicts.  Returns 1.0 if VLM was skipped."""
    if not verdicts:
        return DimensionScore(
            "vlm_semantic", 1.0, True, "VLM skipped - CV-only mode"
        )

    total = len(verdicts)
    fails = sum(1 for v in verdicts if v.vlm_verdict == "FAIL")
    passes = sum(1 for v in verdicts if v.vlm_verdict in ("PASS", "INTENTIONAL"))

    if total == 0:
        return DimensionScore("vlm_semantic", 1.0, True, "no segments reviewed")

    fail_ratio = fails / total
    score = max(0.0, 1.0 - fail_ratio)

    # Weight by confidence
    if fails > 0:
        avg_conf = sum(
            v.vlm_confidence for v in verdicts if v.vlm_verdict == "FAIL"
        ) / fails
        score = max(0.0, score - 0.2 * avg_conf)

    details = f"vlm_pass={passes}, vlm_fail={fails}, total={total}"
    return DimensionScore("vlm_semantic", max(0.0, min(1.0, score)), score >= 0.60, details)


# =====================================================================
# Issue extraction
# =====================================================================

def _collect_issues(
    segments: List[SegmentFeatures],
    verdicts: List[VLMVerdict],
) -> List[Dict]:
    """Build a flat list of detected issues for the report."""
    issues: List[Dict] = []

    vlm_map = {v.segment_id: v for v in verdicts}

    for seg in segments:
        if seg.label not in ("cv_fail", "needs_vlm"):
            continue

        issue: Dict = {
            "segment_id": seg.segment_id,
            "time_range": f"{seg.start_sec:.2f}s -> {seg.end_sec:.2f}s",
            "cv_label": seg.label,
            "cv_score": round(seg.score, 3),
            "cv_reason": seg.reason,
        }

        vlm_v = vlm_map.get(seg.segment_id)
        if vlm_v:
            issue["vlm_verdict"] = vlm_v.vlm_verdict
            issue["vlm_confidence"] = round(vlm_v.vlm_confidence, 3)
            issue["vlm_reason"] = vlm_v.vlm_reason

        # Determine final status
        if vlm_v and vlm_v.vlm_verdict == "FAIL":
            issue["final_status"] = "FAIL"
        elif vlm_v and vlm_v.vlm_verdict in ("PASS", "INTENTIONAL"):
            issue["final_status"] = vlm_v.vlm_verdict
        elif seg.label == "cv_fail":
            issue["final_status"] = "FAIL (CV-only)"
        else:
            issue["final_status"] = "UNCERTAIN"

        issues.append(issue)

    return issues


# =====================================================================
# Main fusion
# =====================================================================

def compute_report(
    video_name: str,
    global_cv: GlobalCVMetrics,
    segments: List[SegmentFeatures],
    verdicts: List[VLMVerdict],
    fusion_cfg: FusionConfig,
) -> EvalReport:
    """Compute the final multi-dimensional evaluation report."""

    report = EvalReport(
        video=video_name,
        duration_sec=global_cv.duration_sec,
        total_frames=global_cv.total_frames,
        fps=global_cv.fps,
    )

    # Per-dimension scores
    d_overlap = _score_overlap(global_cv, segments)
    d_render = _score_rendering(global_cv)
    d_layout = _score_layout(global_cv)
    d_anim = _score_animation(global_cv)
    d_color = _score_color(global_cv)
    d_vlm = _score_vlm(verdicts, segments)

    report.dimensions = [d_overlap, d_render, d_layout, d_anim, d_color, d_vlm]

    # Weighted overall score
    weights = {
        "overlap": fusion_cfg.w_overlap,
        "rendering": fusion_cfg.w_rendering,
        "layout": fusion_cfg.w_layout,
        "animation": fusion_cfg.w_animation,
        "color_consistency": fusion_cfg.w_color_consistency,
        "vlm_semantic": fusion_cfg.w_vlm_semantic,
    }
    total_w = sum(weights.values())
    overall = sum(
        d.score * weights.get(d.name, 0.0) for d in report.dimensions
    ) / max(total_w, 1e-6)
    report.overall_score = round(max(0.0, min(1.0, overall)), 4)
    report.overall_passed = report.overall_score >= fusion_cfg.overall_pass

    # Issues
    report.issues = _collect_issues(segments, verdicts)
    report.cv_fail_segments = [s.segment_id for s in segments if s.label == "cv_fail"]
    report.vlm_fail_segments = [v.segment_id for v in verdicts if v.vlm_verdict == "FAIL"]

    return report


# =====================================================================
# Report output
# =====================================================================

def save_report_json(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "video": report.video,
        "duration_sec": report.duration_sec,
        "total_frames": report.total_frames,
        "fps": report.fps,
        "overall_score": report.overall_score,
        "overall_passed": report.overall_passed,
        "dimensions": [
            {
                "name": d.name,
                "score": round(d.score, 4),
                "passed": d.passed,
                "details": d.details,
            }
            for d in report.dimensions
        ],
        "issues": report.issues,
        "cv_fail_segments": report.cv_fail_segments,
        "vlm_fail_segments": report.vlm_fail_segments,
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def print_report(report: EvalReport) -> None:
    """Pretty-print the evaluation report to stdout."""

    print("=" * 70)
    print(f"  VIDEO EVALUATION REPORT")
    print("=" * 70)
    print(f"  Video    : {report.video}")
    print(f"  Duration : {report.duration_sec:.1f}s  ({report.total_frames} frames @ {report.fps:.1f} fps)")
    print()

    # Dimension scores
    print("  DIMENSION SCORES")
    print("  " + "-" * 50)
    for d in report.dimensions:
        bar_len = int(d.score * 20)
        bar = "#" * bar_len + "-" * (20 - bar_len)
        status = "PASS" if d.passed else "FAIL"
        print(f"  {d.name:<22s} [{bar}] {d.score:.2f}  [{status}]")
        if d.details:
            print(f"  {'':22s} {d.details}")
    print()

    # Overall
    overall_bar_len = int(report.overall_score * 20)
    overall_bar = "#" * overall_bar_len + "-" * (20 - overall_bar_len)
    overall_status = "PASS" if report.overall_passed else "FAIL"
    print(f"  OVERALL              [{overall_bar}] {report.overall_score:.2f}  [{overall_status}]")
    print()

    # Issues
    if report.issues:
        print(f"  ISSUES ({len(report.issues)})")
        print("  " + "-" * 50)
        for issue in report.issues:
            seg = issue["segment_id"]
            tr = issue["time_range"]
            status = issue["final_status"]
            reason = issue.get("vlm_reason") or issue.get("cv_reason", "")
            print(f"  [{status:^16s}] {seg}  {tr}")
            if reason:
                safe_reason = reason.encode("ascii", errors="replace").decode("ascii")
                print(f"  {'':18s} {safe_reason}")
    else:
        print("  No issues detected.")

    print()
    print("=" * 70)
