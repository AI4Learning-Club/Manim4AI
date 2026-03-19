"""
Layer 3 – Fusion scoring and structured report generation.

Report dimensions follow the paper Table 1:
  1. Executability       – render@1, render@final                  (Binary, external)
  2. Task Correctness    – Content Accuracy, Pedagogical Clarity,
                           Engagement                              (VLM)
  3. Visual Quality      – Overlap (CV), Layout (CV),
                           Animation Continuity (CV),
                           Visual Content Consistency (CV+VLM)
  4. Efficiency          – Token Usage, End-to-End Time            (external)
  5. Repairability       – Score Improvement (ΔS), fix rate        (external)
  6. Audio Quality       – Audio Signal Quality (CV),
                           AV Temporal Alignment (CV)
  7. Human Evaluation    – (placeholder, not auto-scored)

CV metrics are computed deterministically; VLM provides semantic review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .audio_features import AlignmentMetrics, AudioMetrics
from .config import ExternalMeta, FusionConfig
from .cv_features import GlobalCVMetrics, SegmentFeatures
from .vlm_judge import (
    AVAlignmentVerdict,
    OverlapReviewVerdict,
    SemanticCoherenceVerdict,
    TaskCorrectnessVerdict,
    VisualCoverageVerdict,
    VLMVerdict,
)


# =====================================================================
# Metric containers
# =====================================================================

@dataclass
class MetricResult:
    """Single metric within a dimension."""
    name: str
    scale: str              # "binary", "likert_1_5", "continuous"
    value: Any              # bool, int, float, str, None
    description: str = ""
    details: str = ""
    source: str = ""        # "cv", "vlm", "cv+vlm", "external", "human"


@dataclass
class DimensionResult:
    """One dimension grouping multiple metrics."""
    name: str
    metrics: List[MetricResult] = field(default_factory=list)
    # Optional aggregate score for dimensions with CV-based continuous metrics
    aggregate_score: Optional[float] = None
    aggregate_passed: Optional[bool] = None


@dataclass
class EvalReport:
    """Final structured evaluation report for one video (paper Table 1)."""

    video: str = ""
    duration_sec: float = 0.0
    total_frames: int = 0
    fps: float = 0.0

    # Paper Table 1 dimensions
    dimensions: List[DimensionResult] = field(default_factory=list)

    # Legacy flat dimension scores (for backward compat)
    dimension_scores: List[Dict] = field(default_factory=list)

    # Overall
    overall_score: float = 0.0
    overall_passed: bool = False

    # Issue inventory
    issues: List[Dict] = field(default_factory=list)

    # Raw references
    cv_fail_segments: List[str] = field(default_factory=list)
    vlm_fail_segments: List[str] = field(default_factory=list)


# =====================================================================
# CV scoring helpers (unchanged logic, new wrappers)
# =====================================================================

def _score_overlap(g: GlobalCVMetrics, segments: List[SegmentFeatures]) -> float:
    ratio_penalty = min(1.0, g.overlap_frame_ratio / 0.50)
    fail_penalty = min(1.0, g.cv_fail_count / 10.0)
    dur_penalty = min(1.0, g.cv_fail_duration_sec / (g.duration_sec * 0.18 + 1e-6))
    return max(0.0, min(1.0, 1.0 - 0.40 * ratio_penalty - 0.35 * fail_penalty - 0.25 * dur_penalty))


def _score_layout(g: GlobalCVMetrics) -> float:
    penalty = min(1.0, g.layout_dense_frame_ratio / 0.40)
    return max(0.0, 1.0 - penalty)


def _score_animation(g: GlobalCVMetrics) -> float:
    disc_penalty = min(1.0, g.motion_discontinuity_count / 20.0)
    flash_penalty = min(1.0, g.flash_event_total / 10.0)
    return max(0.0, 1.0 - 0.6 * disc_penalty - 0.4 * flash_penalty)


def _score_color(g: GlobalCVMetrics) -> float:
    if g.total_frames <= 1:
        return 1.0
    event_ratio = g.color_shift_events / g.total_frames
    penalty = min(1.0, event_ratio / 0.05)
    return max(0.0, 1.0 - penalty)


def _score_rendering(g: GlobalCVMetrics) -> float:
    if g.ocr_artifact_total == 0:
        return 1.0
    penalty = min(1.0, g.ocr_artifact_total / 10.0)
    return max(0.0, 1.0 - penalty)


def _score_audio_signal(audio: AudioMetrics) -> float:
    """Score purely signal-level audio quality: SNR, clipping, spectral flatness.

    Duration mismatch and silence are NOT signal quality — they belong in
    AV alignment / pacing.
    """
    if not audio.has_audio:
        return 1.0

    # 1. SNR penalty (weight 0.45): penalise below 20 dB
    snr_penalty = max(0.0, 1.0 - audio.snr_db / 20.0) if audio.snr_db < 20.0 else 0.0

    # 2. Clipping penalty (weight 0.30): any clipping is bad
    clip_penalty = min(1.0, audio.clipping_ratio / 0.005)

    # 3. Spectral flatness penalty (weight 0.25): high flatness = noisy
    #    Clean speech ~0.02-0.15; noisy > 0.3
    flat_penalty = min(1.0, max(0.0, (audio.spectral_flatness - 0.15) / 0.35))

    raw = 1.0 - (0.45 * snr_penalty + 0.30 * clip_penalty + 0.25 * flat_penalty)
    return max(0.0, min(1.0, raw))


def _score_av_alignment(
    align: AlignmentMetrics,
    audio: Optional[AudioMetrics] = None,
) -> float:
    """Score temporal alignment + pacing (duration mismatch, silence distribution)."""
    if not align.has_audio or align.speech_total_sec < 0.5:
        return 1.0

    # Core alignment: speech↔motion temporal IoU + coverage
    alignment_score = 0.6 * align.iou + 0.4 * align.coverage

    # Pacing penalties (absorbed from old audio quality scorer)
    pacing_penalty = 0.0
    if audio is not None:
        # Duration mismatch between audio and video streams
        mismatch_p = min(1.0, audio.duration_mismatch_sec / 5.0)
        # Excessive edge silence (> 2s each side is suspicious)
        edge_silence = audio.leading_silence_sec + audio.trailing_silence_sec
        edge_p = min(1.0, max(0.0, (edge_silence - 1.0) / 4.0))
        # Mid-silence ratio (long gaps = bad pacing)
        mid_ratio = audio.mid_silence_total_sec / max(audio.audio_duration_sec, 1e-6)
        mid_p = min(1.0, mid_ratio / 0.25)
        pacing_penalty = 0.4 * mismatch_p + 0.3 * edge_p + 0.3 * mid_p

    # Blend: 70% alignment + 30% pacing
    score = 0.70 * alignment_score + 0.30 * (1.0 - pacing_penalty)
    return max(0.0, min(1.0, score))


# =====================================================================
# VLM-based visual review score
# =====================================================================

def _vlm_review_score(verdicts: List[VLMVerdict]) -> float:
    """Score from VLM overlap/rendering review. 1.0 if skipped."""
    if not verdicts:
        return 1.0
    total = len(verdicts)
    fails = sum(1 for v in verdicts if v.vlm_verdict == "FAIL")
    if total == 0:
        return 1.0
    score = max(0.0, 1.0 - fails / total)
    if fails > 0:
        avg_conf = sum(v.vlm_confidence for v in verdicts if v.vlm_verdict == "FAIL") / fails
        score = max(0.0, score - 0.2 * avg_conf)
    return max(0.0, min(1.0, score))


# =====================================================================
# Issue extraction
# =====================================================================

def _collect_issues(
    segments: List[SegmentFeatures],
    verdicts: List[VLMVerdict],
) -> List[Dict]:
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
# Main fusion – builds paper Table 1 report
# =====================================================================

def compute_report(
    video_name: str,
    global_cv: GlobalCVMetrics,
    segments: List[SegmentFeatures],
    verdicts: List[VLMVerdict],
    fusion_cfg: FusionConfig,
    audio_metrics: Optional[AudioMetrics] = None,
    alignment_metrics: Optional[AlignmentMetrics] = None,
    task_correctness: Optional[TaskCorrectnessVerdict] = None,
    av_alignment_verdict: Optional[AVAlignmentVerdict] = None,
    overlap_review: Optional[OverlapReviewVerdict] = None,
    visual_coverage: Optional[VisualCoverageVerdict] = None,
    semantic_coherence: Optional[SemanticCoherenceVerdict] = None,
    meta: Optional[ExternalMeta] = None,
) -> EvalReport:
    """Compute the final evaluation report structured per paper Table 1."""

    report = EvalReport(
        video=video_name,
        duration_sec=global_cv.duration_sec,
        total_frames=global_cv.total_frames,
        fps=global_cv.fps,
    )

    meta = meta or ExternalMeta()

    # Standalone mode: if no external meta was provided, the video exists
    # so executability is implicitly true.
    standalone = (meta.render_at_1 is None and meta.render_at_final is None
                  and meta.token_usage_mean is None and meta.time_total_sec is None)

    # ==================================================================
    # 1. Executability (Binary, external metadata)
    # ==================================================================
    # Standalone: video exists → both are true by definition.
    r1 = meta.render_at_1 if meta.render_at_1 is not None else (True if standalone else None)
    rf = meta.render_at_final if meta.render_at_final is not None else (True if standalone else None)

    dim_exec = DimensionResult(name="Executability", metrics=[
        MetricResult(
            name="render@1", scale="binary",
            value=r1,
            description="Whether the generated code renders successfully on the first attempt",
            source="external" if not standalone else "inferred",
        ),
        MetricResult(
            name="render@final", scale="binary",
            value=rf,
            description="Whether the code renders successfully after iterative repair",
            source="external" if not standalone else "inferred",
        ),
    ])

    # ==================================================================
    # 2. Task Correctness (VLM-based)
    # ==================================================================
    if task_correctness:
        tc = task_correctness
        dim_tc = DimensionResult(name="Task Correctness", metrics=[
            MetricResult(
                name="content_accuracy", scale="binary",
                value=tc.content_accuracy,
                description="Whether the rendered output addresses the specified topic correctly",
                details=tc.content_accuracy_reason,
                source="vlm",
            ),
            MetricResult(
                name="pedagogical_clarity", scale="continuous",
                value=round(tc.pedagogical_clarity, 4),
                description="Clarity and logical coherence of the instructional presentation",
                details=tc.pedagogical_clarity_reason,
                source="vlm",
            ),
            MetricResult(
                name="engagement", scale="continuous",
                value=round(tc.engagement, 4),
                description="Degree to which the output engages viewers and sustains attention",
                details=tc.engagement_reason,
                source="vlm",
            ),
        ])
    else:
        dim_tc = DimensionResult(name="Task Correctness", metrics=[
            MetricResult(name="content_accuracy", scale="binary", value=None,
                         description="VLM skipped", source="vlm"),
            MetricResult(name="pedagogical_clarity", scale="continuous", value=None,
                         description="VLM skipped", source="vlm"),
            MetricResult(name="engagement", scale="continuous", value=None,
                         description="VLM skipped", source="vlm"),
        ])

    # ==================================================================
    # 3. Visual Quality (CV + VLM review)
    # ==================================================================
    s_overlap_cv = _score_overlap(global_cv, segments)
    s_layout = _score_layout(global_cv)
    s_anim_cv = _score_animation(global_cv)
    s_render = _score_rendering(global_cv)
    s_color = _score_color(global_cv)

    # --- Overlap: CV + VLM keyframe review ---
    overlap_details = (
        f"cv_overlap_frame_ratio={global_cv.overlap_frame_ratio:.3f}, "
        f"cv_fail_count={global_cv.cv_fail_count}"
    )
    overlap_source = "cv"
    s_overlap = s_overlap_cv
    if overlap_review:
        # VLM overlap ratio: fraction of keyframes with issues
        vlm_overlap_score = max(0.0, 1.0 - overlap_review.overlap_ratio)
        s_overlap = 0.5 * s_overlap_cv + 0.5 * vlm_overlap_score
        overlap_details += (
            f", vlm_overlap_ratio={overlap_review.overlap_ratio:.3f}"
            f", vlm_issues={len(overlap_review.issues)}"
        )
        overlap_source = "cv+vlm"

    # --- Animation Continuity: CV + VLM segment review ---
    anim_details = (
        f"motion_discontinuities={global_cv.motion_discontinuity_count}, "
        f"flash_events={global_cv.flash_event_total}"
    )
    anim_source = "cv"
    s_anim = s_anim_cv
    # VLM segment verdicts that mention animation dimension
    anim_vlm_fails = sum(
        1 for v in verdicts
        if v.vlm_verdict == "FAIL" and "animation" in v.raw_response.lower()
    )
    if verdicts and anim_vlm_fails > 0:
        vlm_anim_penalty = min(1.0, anim_vlm_fails / max(len(verdicts), 1))
        s_anim = 0.6 * s_anim_cv + 0.4 * (1.0 - vlm_anim_penalty)
        anim_details += f", vlm_anim_fails={anim_vlm_fails}"
        anim_source = "cv+vlm"

    # --- Visual Content Consistency: teaching plan coverage or semantic coherence ---
    if visual_coverage:
        s_consistency = visual_coverage.coverage_ratio
        consistency_details = (
            f"coverage={visual_coverage.coverage_ratio:.2f}, "
            f"missing={visual_coverage.missing_sections}"
        )
        consistency_source = "vlm"
    elif semantic_coherence:
        s_consistency = semantic_coherence.score
        consistency_details = (
            f"topic={semantic_coherence.topic_consistency:.2f}, "
            f"progression={semantic_coherence.logical_progression:.2f}, "
            f"relevance={semantic_coherence.visual_relevance:.2f}, "
            f"reason={semantic_coherence.reason}"
        )
        consistency_source = "vlm"
    else:
        # No teaching plan and no coherence check → neutral score
        s_consistency = 1.0
        consistency_details = "no teaching plan or coherence check — skipped"
        consistency_source = "n/a"

    visual_agg = (
        fusion_cfg.w_overlap * s_overlap +
        fusion_cfg.w_layout * s_layout +
        fusion_cfg.w_animation * s_anim +
        fusion_cfg.w_color_consistency * s_color +
        fusion_cfg.w_vlm_semantic * s_consistency +
        fusion_cfg.w_rendering * s_render
    )
    visual_w = (fusion_cfg.w_overlap + fusion_cfg.w_layout + fusion_cfg.w_animation +
                fusion_cfg.w_color_consistency + fusion_cfg.w_vlm_semantic + fusion_cfg.w_rendering)
    visual_score = visual_agg / max(visual_w, 1e-6)

    dim_visual = DimensionResult(
        name="Visual Quality",
        aggregate_score=round(visual_score, 4),
        aggregate_passed=visual_score >= 0.60,
        metrics=[
            MetricResult(
                name="overlap", scale="continuous", value=round(s_overlap, 4),
                description="Degree of spatial overlap between visual elements (lower is better)",
                details=overlap_details,
                source=overlap_source,
            ),
            MetricResult(
                name="layout", scale="continuous", value=round(s_layout, 4),
                description="Adherence to layout constraints and spatial organization quality",
                details=f"dense_frame_ratio={global_cv.layout_dense_frame_ratio:.3f}",
                source="cv",
            ),
            MetricResult(
                name="animation_continuity", scale="continuous", value=round(s_anim, 4),
                description="Smoothness and temporal coherence of animation transitions",
                details=anim_details,
                source=anim_source,
            ),
            MetricResult(
                name="visual_content_consistency", scale="continuous",
                value=round(s_consistency, 4),
                description="Whether the video covers all sections of the teaching plan",
                details=consistency_details,
                source=consistency_source,
            ),
        ],
    )

    # ==================================================================
    # 4. Efficiency (external metadata)
    # ==================================================================
    na_desc = "N/A (standalone evaluation)" if standalone else ""
    dim_eff = DimensionResult(name="Efficiency", metrics=[
        MetricResult(
            name="token_usage", scale="continuous",
            value=f"{meta.token_usage_mean:.0f} +/- {meta.token_usage_std:.0f}" if meta.token_usage_mean is not None else None,
            description=na_desc or "Mean +/- std of tokens consumed, with estimated cost in USD",
            details=f"cost=${meta.token_cost_usd:.4f}" if meta.token_cost_usd is not None else "",
            source="external",
        ),
        MetricResult(
            name="end_to_end_time", scale="continuous",
            value=f"{meta.time_total_sec:.1f}s" if meta.time_total_sec is not None else None,
            description=na_desc or "Mean +/- std of total generation time, reported per stage",
            details=json.dumps(meta.time_per_stage) if meta.time_per_stage else "",
            source="external",
        ),
    ])

    # ==================================================================
    # 5. Repairability (external metadata)
    # ==================================================================
    dim_repair = DimensionResult(name="Repairability", metrics=[
        MetricResult(
            name="score_improvement", scale="continuous",
            value=meta.score_delta,
            description=na_desc or "Quality score delta (delta_S) and fix rate after iterative refinement",
            details=f"fix_rate={meta.fix_rate:.2%}, rounds={meta.repair_rounds}" if meta.fix_rate is not None else "",
            source="external",
        ),
    ])

    # ==================================================================
    # 6. Audio Quality (CV-based)
    # ==================================================================
    if audio_metrics is not None:
        s_audio = _score_audio_signal(audio_metrics)
        audio_details = (
            f"snr={audio_metrics.snr_db:.1f}dB, "
            f"clipping={audio_metrics.clipping_ratio:.4f}, "
            f"spectral_flatness={audio_metrics.spectral_flatness:.4f}"
        )
    else:
        s_audio = 1.0
        audio_details = "audio analysis skipped"

    if alignment_metrics is not None:
        s_align = _score_av_alignment(alignment_metrics, audio_metrics)
        align_details = (
            f"iou={alignment_metrics.iou:.3f}, "
            f"coverage={alignment_metrics.coverage:.3f}"
        )
        if audio_metrics is not None:
            align_details += (
                f", dur_mismatch={audio_metrics.duration_mismatch_sec:.1f}s"
                f", edge_silence={audio_metrics.leading_silence_sec + audio_metrics.trailing_silence_sec:.1f}s"
                f", mid_silence={audio_metrics.mid_silence_total_sec:.1f}s"
            )
    else:
        s_align = 1.0
        align_details = "alignment analysis skipped"

    audio_agg = (fusion_cfg.w_audio_quality * s_audio +
                 fusion_cfg.w_av_alignment * s_align)
    audio_w = fusion_cfg.w_audio_quality + fusion_cfg.w_av_alignment
    audio_score = audio_agg / max(audio_w, 1e-6)

    # Blend MLLM verdict into AV alignment score (if available)
    av_source = "cv"
    if av_alignment_verdict:
        av = av_alignment_verdict
        # MLLM scores are 1-5, normalise to 0-1
        mllm_score = (
            (av.semantic_alignment - 1) / 4.0 * 0.4 +
            (av.temporal_pacing - 1) / 4.0 * 0.4 +
            (av.narration_naturalness - 1) / 4.0 * 0.2
        )
        # Blend: 50% CV signal + 50% MLLM semantic
        s_align = 0.5 * s_align + 0.5 * mllm_score
        s_align = max(0.0, min(1.0, s_align))
        align_details += (
            f", mllm_semantic={av.semantic_alignment}/5"
            f", mllm_pacing={av.temporal_pacing}/5"
            f", mllm_naturalness={av.narration_naturalness}/5"
        )
        av_source = "cv+vlm"

    # Recompute audio aggregate with updated s_align
    audio_agg = (fusion_cfg.w_audio_quality * s_audio +
                 fusion_cfg.w_av_alignment * s_align)
    audio_score = audio_agg / max(audio_w, 1e-6)

    dim_audio = DimensionResult(
        name="Audio Quality",
        aggregate_score=round(audio_score, 4),
        aggregate_passed=audio_score >= 0.55,
        metrics=[
            MetricResult(
                name="audio_signal_quality", scale="continuous",
                value=round(s_audio, 4),
                description="Signal-level quality: SNR, clipping, spectral flatness",
                details=audio_details,
                source="cv",
            ),
            MetricResult(
                name="av_temporal_alignment", scale="continuous",
                value=round(s_align, 4),
                description="Synchronization accuracy between audio narration and visual events",
                details=align_details,
                source=av_source,
            ),
        ],
    )

    # ==================================================================
    # 7. Human Evaluation (placeholder)
    # ==================================================================
    dim_human = DimensionResult(name="Human Evaluation (Double-Blind)", metrics=[
        MetricResult(name="preference", scale="binary", value=None,
                     description="Pairwise preference judgment between two systems",
                     source="human"),
        MetricResult(name="perceived_clarity", scale="likert_1_5", value=None,
                     description="Human-rated instructional clarity of the rendered output",
                     source="human"),
        MetricResult(name="perceived_engagement", scale="likert_1_5", value=None,
                     description="Human-rated engagement level of the rendered output",
                     source="human"),
    ])

    # ==================================================================
    # Assemble all dimensions
    # ==================================================================
    report.dimensions = [
        dim_exec, dim_tc, dim_visual, dim_eff, dim_repair, dim_audio, dim_human,
    ]

    # ==================================================================
    # Overall score (weighted from scorable dimensions)
    # ==================================================================
    weights = {
        "overlap": fusion_cfg.w_overlap,
        "rendering": fusion_cfg.w_rendering,
        "layout": fusion_cfg.w_layout,
        "animation": fusion_cfg.w_animation,
        "color_consistency": fusion_cfg.w_color_consistency,
        "vlm_semantic": fusion_cfg.w_vlm_semantic,
        "audio_quality": fusion_cfg.w_audio_quality,
        "av_alignment": fusion_cfg.w_av_alignment,
    }
    flat_scores = {
        "overlap": s_overlap,
        "rendering": s_render,
        "layout": s_layout,
        "animation": s_anim,
        "color_consistency": s_color,
        "vlm_semantic": s_consistency,
        "audio_quality": s_audio,
        "av_alignment": s_align,
    }
    total_w = sum(weights.values())
    overall = sum(flat_scores[k] * weights[k] for k in weights) / max(total_w, 1e-6)
    report.overall_score = round(max(0.0, min(1.0, overall)), 4)
    report.overall_passed = report.overall_score >= fusion_cfg.overall_pass

    # Legacy flat dimension_scores for backward compat
    report.dimension_scores = [
        {"name": k, "score": round(v, 4), "weight": weights[k]}
        for k, v in flat_scores.items()
    ]

    # Issues
    report.issues = _collect_issues(segments, verdicts)
    report.cv_fail_segments = [s.segment_id for s in segments if s.label == "cv_fail"]
    report.vlm_fail_segments = [v.segment_id for v in verdicts if v.vlm_verdict == "FAIL"]

    return report


# =====================================================================
# Report output
# =====================================================================

def _serialize_metric(m: MetricResult) -> Dict:
    return {
        "name": m.name,
        "scale": m.scale,
        "value": m.value,
        "description": m.description,
        "details": m.details,
        "source": m.source,
    }


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
                "name": dim.name,
                "aggregate_score": dim.aggregate_score,
                "aggregate_passed": dim.aggregate_passed,
                "metrics": [_serialize_metric(m) for m in dim.metrics],
            }
            for dim in report.dimensions
        ],
        "dimension_scores_flat": report.dimension_scores,
        "issues": report.issues,
        "cv_fail_segments": report.cv_fail_segments,
        "vlm_fail_segments": report.vlm_fail_segments,
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def print_report(report: EvalReport) -> None:
    """Pretty-print the evaluation report to stdout (paper Table 1 format)."""

    print("=" * 78)
    print("  VIDEO EVALUATION REPORT (Paper Table 1)")
    print("=" * 78)
    print(f"  Video    : {report.video}")
    print(f"  Duration : {report.duration_sec:.1f}s  ({report.total_frames} frames @ {report.fps:.1f} fps)")
    print()

    for dim in report.dimensions:
        # Dimension header
        header = f"  {dim.name}"
        if dim.aggregate_score is not None:
            bar_len = int(dim.aggregate_score * 20)
            bar = "#" * bar_len + "-" * (20 - bar_len)
            status = "PASS" if dim.aggregate_passed else "FAIL"
            header += f"  [{bar}] {dim.aggregate_score:.2f} [{status}]"
        print(header)
        print("  " + "-" * 72)

        for m in dim.metrics:
            scale_tag = {"binary": "Binary", "likert_1_5": "Likert 1-5",
                         "continuous": "Continuous"}.get(m.scale, m.scale)

            if m.value is None:
                val_str = "N/A"
            elif isinstance(m.value, bool):
                val_str = "YES" if m.value else "NO"
            elif isinstance(m.value, float):
                val_str = f"{m.value:.4f}"
            else:
                val_str = str(m.value)

            print(f"    {m.name:<30s} [{scale_tag:^12s}]  {val_str:<12s}  ({m.source})")
            if m.details:
                safe = m.details.encode("ascii", errors="replace").decode("ascii")
                print(f"    {'':30s}  {safe}")
        print()

    # Overall
    overall_bar_len = int(report.overall_score * 20)
    overall_bar = "#" * overall_bar_len + "-" * (20 - overall_bar_len)
    overall_status = "PASS" if report.overall_passed else "FAIL"
    print(f"  OVERALL (Visual+Audio)  [{overall_bar}] {report.overall_score:.2f}  [{overall_status}]")
    print()

    # Issues
    if report.issues:
        print(f"  ISSUES ({len(report.issues)})")
        print("  " + "-" * 72)
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
    print("=" * 78)
