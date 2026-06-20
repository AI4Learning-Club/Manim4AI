#!/usr/bin/env python3
"""
Main entry-point for the Manim Video Evaluation Pipeline.

Usage examples:

  # CV-only mode (no VLM, no API key needed):
  python -m eval_pipeline.run video.mp4 --skip-vlm

  # Full pipeline (CV + VLM, defaults from settings.toml [manim.llm.eval]):
  python -m eval_pipeline.run video.mp4

  # Override API key for one-off runs:
  python -m eval_pipeline.run video.mp4 --api-key sk-xxx

  # Custom model / endpoint:
  python -m eval_pipeline.run video.mp4 --model gpt-4.1 --base-url https://...

  # Batch mode (multiple videos):
  python -m eval_pipeline.run video1.mp4 video2.mp4 video3.mp4

Architecture:
  Layer 1  (CV)    → deterministic per-frame feature extraction
  Layer 1b (Audio) → audio quality + audio-visual alignment
  Layer 2  (VLM)   → semantic judgment on suspicious segments
  Layer 3  (Fusion) → weighted multi-dimensional scoring + report
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from plugins.manim.runtime_config import get_manim_settings

from .audio_features import extract_alignment_metrics, extract_audio_metrics_with_pcm
from .config import AudioConfig, CVConfig, ExternalMeta, FusionConfig, PipelineConfig, VLMConfig
from .cv_features import (
    SegmentFeatures,
    classify_segment,
    compute_global_cv_metrics,
    compute_segment_features,
    extract_all_frames,
    extract_keyframes,
    merge_candidate_frames,
    save_frame_csv,
    save_segment_csv,
)
from .fusion import compute_report, print_report, save_report_json
from .vlm_judge import (
    AnchorBindingVerdict,
    AVAlignmentVerdict,
    OverlapReviewVerdict,
    SemanticCoherenceVerdict,
    TaskCorrectnessVerdict,
    VisualCoverageVerdict,
    VLMVerdict,
    WholeVideoMediaCache,
    WholeVideoVisualReviewResult,
    prepare_whole_video_media_cache,
    review_av_alignment,
    review_segments,
    review_semantic_coherence,
    review_task_correctness,
    review_visual_coverage,
    review_whole_video_visual_keyframes,
    save_verdicts_jsonl,
)

MANIM_SETTINGS = get_manim_settings()
DEFAULT_VLM_SETTINGS = MANIM_SETTINGS.llm.eval
DEFAULT_API_KEY = DEFAULT_VLM_SETTINGS.api_key
DEFAULT_BASE_URL = DEFAULT_VLM_SETTINGS.base_url
DEFAULT_MODEL = DEFAULT_VLM_SETTINGS.model
DEFAULT_REASONING_EFFORT = DEFAULT_VLM_SETTINGS.reasoning_effort
VLM_STAGE_MAX_WORKERS = max(1, MANIM_SETTINGS.eval_vlm_stage_workers)


# =====================================================================
# CLI
# =====================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval_pipeline",
        description="Three-layer evaluation pipeline for Manim-rendered videos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    p.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="One or more input MP4 video paths",
    )
    p.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("eval_output"),
        help="Root output directory (default: eval_output)",
    )

    # --- VLM options ---
    vlm = p.add_argument_group("VLM options")
    vlm.add_argument("--skip-vlm", action="store_true", help="Run CV-only mode")
    vlm.add_argument("--api-key", type=str, default=DEFAULT_API_KEY, help="OpenAI-compatible API key (default: settings.toml [manim.llm.eval].api_key)")
    vlm.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL, help="Custom API base URL (default: settings.toml [manim.llm.eval].base_url)")
    vlm.add_argument("--model", type=str, default=DEFAULT_MODEL, help="VLM model name (default: settings.toml [manim.llm.eval].model)")
    vlm.add_argument(
        "--reasoning-effort",
        type=str,
        default=DEFAULT_REASONING_EFFORT,
        help="Responses API reasoning.effort for VLM review (default: settings.toml [manim.llm.eval].reasoning_effort)",
    )
    vlm.add_argument("--max-vlm-segments", type=int, default=0, help="Max segments to send to VLM (0=all)")
    vlm.add_argument(
        "--enable-direct-video-vlm",
        action="store_true",
        help="Allow whole-video VLM stages to send input_video instead of using keyframes only",
    )
    vlm.add_argument(
        "--vlm-all",
        action="store_true",
        help="Send ALL candidate buckets to VLM, including low-risk static-layout candidates",
    )

    # --- CV tuning ---
    cv = p.add_argument_group("CV tuning")
    cv.add_argument("--no-ocr", action="store_true", help="Disable OCR artifact detection")
    cv.add_argument("--text-max-sat", type=int, default=48)
    cv.add_argument("--text-min-val", type=int, default=145)
    cv.add_argument("--solid-min-sat", type=int, default=20)
    cv.add_argument("--solid-min-val", type=int, default=20)
    cv.add_argument("--frame-step", type=int, default=10, help="Process every N-th frame (default: 10)")
    cv.add_argument("--candidate-min-pixels", type=int, default=90)

    # --- Audio options ---
    au = p.add_argument_group("Audio options")
    au.add_argument("--skip-audio", action="store_true", help="Skip audio quality and alignment analysis")
    au.add_argument("--silence-threshold-db", type=float, default=-40.0, help="RMS threshold for silence detection (dB)")
    au.add_argument("--speech-threshold-db", type=float, default=-35.0, help="RMS threshold for speech VAD (dB)")

    # --- Task Correctness ---
    tc = p.add_argument_group("Task Correctness (VLM)")
    tc.add_argument("--topic", type=str, default="", help="Teaching topic/prompt for Task Correctness VLM review")
    tc.add_argument("--skip-task-correctness", action="store_true", help="Skip Task Correctness VLM evaluation")
    tc.add_argument("--tc-keyframes", type=int, default=8, help="Number of keyframes to sample for task correctness review")

    # --- External metadata ---
    em = p.add_argument_group("External metadata (Executability / Efficiency / Repairability)")
    em.add_argument("--render-at-1", type=lambda x: x.lower() in ("true","1","yes"), default=None, help="render@1 result (true/false)")
    em.add_argument("--render-at-final", type=lambda x: x.lower() in ("true","1","yes"), default=None, help="render@final result (true/false)")
    em.add_argument("--token-usage", type=float, default=None, help="Mean token usage")
    em.add_argument("--token-std", type=float, default=None, help="Token usage std")
    em.add_argument("--token-cost", type=float, default=None, help="Estimated cost in USD")
    em.add_argument("--time-total", type=float, default=None, help="Total generation time (seconds)")
    em.add_argument("--score-delta", type=float, default=None, help="Score improvement delta")
    em.add_argument("--fix-rate", type=float, default=None, help="Fix rate after repair")
    em.add_argument("--repair-rounds", type=int, default=None, help="Number of repair rounds")

    # --- Fusion weights ---
    fw = p.add_argument_group("Fusion weights")
    fw.add_argument("--w-overlap", type=float, default=0.17)
    fw.add_argument("--w-rendering", type=float, default=0.17)
    fw.add_argument("--w-layout", type=float, default=0.13)
    fw.add_argument("--w-animation", type=float, default=0.13)
    fw.add_argument("--w-color", type=float, default=0.08)
    fw.add_argument("--w-vlm", type=float, default=0.17)
    fw.add_argument("--w-audio-quality", type=float, default=0.08)
    fw.add_argument("--w-av-alignment", type=float, default=0.07)

    return p


def build_config(args: argparse.Namespace) -> PipelineConfig:
    cv_cfg = CVConfig(
        text_max_sat=args.text_max_sat,
        text_min_val=args.text_min_val,
        solid_min_sat=args.solid_min_sat,
        solid_min_val=args.solid_min_val,
        candidate_min_pixels=args.candidate_min_pixels,
        ocr_enabled=not args.no_ocr,
        frame_step=args.frame_step,
    )

    vlm_cfg = VLMConfig(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        reasoning_effort=args.reasoning_effort,
        max_segments=args.max_vlm_segments,
        include_cv_fail=True,
        enable_direct_video=args.enable_direct_video_vlm,
    )

    audio_cfg = AudioConfig(
        silence_threshold_db=args.silence_threshold_db,
        speech_energy_threshold_db=args.speech_threshold_db,
    )

    fusion_cfg = FusionConfig(
        w_overlap=args.w_overlap,
        w_rendering=args.w_rendering,
        w_layout=args.w_layout,
        w_animation=args.w_animation,
        w_color_consistency=args.w_color,
        w_vlm_semantic=args.w_vlm,
        w_audio_quality=args.w_audio_quality,
        w_av_alignment=args.w_av_alignment,
    )

    meta = ExternalMeta(
        render_at_1=args.render_at_1,
        render_at_final=args.render_at_final,
        token_usage_mean=args.token_usage,
        token_usage_std=args.token_std,
        token_cost_usd=args.token_cost,
        time_total_sec=args.time_total,
        score_delta=args.score_delta,
        fix_rate=args.fix_rate,
        repair_rounds=args.repair_rounds,
        topic=args.topic,
    )

    cfg = PipelineConfig(
        cv=cv_cfg,
        audio=audio_cfg,
        vlm=vlm_cfg,
        fusion=fusion_cfg,
        meta=meta,
        output_dir=args.output_dir,
        skip_vlm=args.skip_vlm,
        skip_audio=args.skip_audio,
        vlm_all=getattr(args, "vlm_all", False),
    )
    # Extra flags not in dataclass (avoid breaking existing callers)
    cfg._skip_task_correctness = getattr(args, "skip_task_correctness", False)  # type: ignore[attr-defined]
    cfg._tc_keyframes = getattr(args, "tc_keyframes", 8)  # type: ignore[attr-defined]
    return cfg


# =====================================================================
# Single-video pipeline
# =====================================================================

def evaluate_video(video_path: Path, cfg: PipelineConfig) -> dict:
    """Run the full pipeline on a single video.  Returns the report dict."""

    video_name = video_path.name
    stem = video_path.stem
    out_dir = cfg.output_dir / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Evaluating: {video_path}")
    print(f"  Output dir: {out_dir}")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # Layer 1: CV feature extraction
    # ------------------------------------------------------------------
    print("[Layer 1] Extracting CV features ...")
    t0 = time.time()

    audio_metrics = None
    alignment_metrics = None
    audio_future = None
    t_audio = None

    if cfg.skip_audio:
        print("\n[Layer 1b] Audio analysis skipped (--skip-audio)")
    else:
        print("\n[Layer 1b] Extracting audio features in parallel ...")
        t_audio = time.time()

    def cv_progress(cur, total):
        pct = cur / max(total, 1) * 100
        print(f"  frame {cur}/{total} ({pct:.0f}%)", end="\r", flush=True)

    with ThreadPoolExecutor(max_workers=1) as layer1_executor:
        if not cfg.skip_audio:
                audio_future = layer1_executor.submit(
                    extract_audio_metrics_with_pcm,
                    video_path,
                    cfg.audio,
                )

        features, fps, total_frames = extract_all_frames(
            video_path, cfg.cv, progress_callback=cv_progress
        )
        print(f"\n  Done: {total_frames} frames @ {fps:.1f} fps  ({time.time()-t0:.1f}s)")

        # Save per-frame CSV
        save_frame_csv(features, out_dir / "frame_stats.csv")

        # Segment extraction
        raw_segments = merge_candidate_frames(features, fps, cfg.cv)
        print(f"  Raw segments: {len(raw_segments)}")

        segment_features_list: list[SegmentFeatures] = []
        for idx, (s, e) in enumerate(raw_segments, start=1):
            seg_id = f"seg_{idx:04d}"
            sf = compute_segment_features(seg_id, s, e, fps, features, cfg.cv)
            label, score, reason = classify_segment(sf, cfg.cv)
            sf.label = label
            sf.score = score
            sf.reason = reason
            segment_features_list.append(sf)

        save_segment_csv(segment_features_list, out_dir / "segments_all.csv")

        # Classification summary
        n_fail = sum(1 for s in segment_features_list if s.label == "cv_fail")
        n_static = sum(1 for s in segment_features_list if s.label == "likely_intentional")
        n_vlm = sum(1 for s in segment_features_list if s.label == "needs_vlm")
        print(
            "  Candidate buckets: "
            f"high_risk={n_fail}, "
            f"low_risk_static={n_static}, "
            f"needs_review={n_vlm}"
        )

        # Extract keyframes for VLM segments
        if cfg.vlm_all:
            vlm_segments = list(segment_features_list)
        else:
            vlm_segments = [
                s for s in segment_features_list
                if s.label == "needs_vlm" or (cfg.vlm.include_cv_fail and s.label == "cv_fail")
            ]
        frames_dir = out_dir / "vlm_payload" / "frames"
        extract_keyframes(video_path, vlm_segments, frames_dir)

        # Global CV metrics
        global_cv = compute_global_cv_metrics(
            features,
            segment_features_list,
            fps,
            cfg.cv,
            total_video_frames=total_frames,
        )

        # ------------------------------------------------------------------
        # Layer 1b: Audio feature extraction
        # ------------------------------------------------------------------
        if not cfg.skip_audio and audio_future is not None:
            try:
                audio_metrics, pcm_result = audio_future.result()
                if audio_metrics.has_audio:
                    alignment_metrics = extract_alignment_metrics(
                        video_path,
                        features,
                        fps,
                        cfg.audio,
                        pcm_result=pcm_result,
                    )
                    print(
                        f"  Audio: {audio_metrics.audio_duration_sec:.1f}s, "
                        f"SNR={audio_metrics.snr_db:.1f}dB, "
                        f"alignment IoU={alignment_metrics.iou:.3f}"
                    )
                else:
                    print("  No audio stream detected - skipping audio analysis")
            except Exception as exc:
                print(f"  Audio analysis error: {exc}")
            finally:
                if t_audio is not None:
                    print(f"  Done ({time.time()-t_audio:.1f}s)")

    # ------------------------------------------------------------------
    # Layer 2: VLM semantic judgment
    # ------------------------------------------------------------------
    verdicts: list[VLMVerdict] = []

    if cfg.skip_vlm:
        print("\n[Layer 2] VLM skipped (--skip-vlm)")
    elif not vlm_segments:
        print("\n[Layer 2] No segments to review – skipping VLM")
    else:
        print(f"\n[Layer 2] Sending {len(vlm_segments)} segments to VLM ({cfg.vlm.model}) ...")

        def vlm_progress(cur, total):
            print(f"  segment {cur}/{total}", end="\r", flush=True)

        try:
            verdicts = review_segments(
                vlm_segments,
                frames_dir,
                cfg.vlm,
                video_name=video_name,
                progress_callback=vlm_progress,
            )
            print(f"\n  VLM returned {len(verdicts)} verdicts")
            save_verdicts_jsonl(verdicts, out_dir / "vlm_verdicts.jsonl")
        except Exception as exc:
            print(f"\n  VLM error: {exc}")
            print("  Continuing with CV-only scoring ...")

    # ------------------------------------------------------------------
    # Layer 2b: Task Correctness VLM review (whole-video)
    # ------------------------------------------------------------------
    task_correctness: TaskCorrectnessVerdict | None = None
    tc_kf_paths: list[Path] = []   # shared with later layers
    skip_tc = getattr(cfg, "_skip_task_correctness", False) or cfg.skip_vlm
    topic = cfg.meta.topic if cfg.meta else ""
    tc_keyframes_count = getattr(cfg, "_tc_keyframes", 8)

    # Load teaching plan early (used by 2b, 2b-3)
    teaching_plan = None
    for tp_candidate in [
        video_path.parent.parent / "teaching_plan.json",
        video_path.parent / "teaching_plan.json",
    ]:
        if tp_candidate.exists():
            import json as _json
            teaching_plan = _json.loads(tp_candidate.read_text(encoding="utf-8"))
            break

    # Infer topic from teaching plan or video name if not provided
    if not topic and teaching_plan:
        topic = teaching_plan.get("lesson_goal", "") or teaching_plan.get("teaching_promise", "")
    if not topic:
        # Use request.txt if it exists next to the video
        for req_candidate in [
            video_path.parent.parent / "request.txt",
            video_path.parent / "request.txt",
        ]:
            if req_candidate.exists():
                topic = req_candidate.read_text(encoding="utf-8").strip()
                break
    if not topic:
        topic = f"Educational video: {video_name}"

    direct_video_enabled = bool(cfg.vlm.enable_direct_video and video_path.exists())

    if skip_tc:
        print("\n[Layer 2b] Task Correctness skipped (--skip-vlm)")
    else:
        print(f"\n[Layer 2b] Task Correctness VLM review (topic: {topic[:60]}...)")
        print(f"  Mode: direct video → VLM (fallback: {tc_keyframes_count} keyframes)")

        if not direct_video_enabled:
            print(f"  Direct video disabled; using {tc_keyframes_count} sampled keyframes for whole-video VLM stages")

        # Always prepare keyframes as fallback
        import cv2 as _cv2
        tc_frames_dir = out_dir / "tc_keyframes"
        tc_frames_dir.mkdir(parents=True, exist_ok=True)
        tc_kf_paths: list[Path] = []

        cap = _cv2.VideoCapture(str(video_path))
        tc_total = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
        if tc_total > 0:
            step = max(1, tc_total // tc_keyframes_count)
            indices = [min(i * step, tc_total - 1) for i in range(tc_keyframes_count)]
            for idx in indices:
                cap.set(_cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if ret:
                    p = tc_frames_dir / f"frame_{idx:06d}.jpg"
                    _cv2.imwrite(str(p), frame)
                    tc_kf_paths.append(p)
        cap.release()

        # Whole-video VLM stages are launched together below.

    if skip_tc and not cfg.skip_vlm:
        import cv2 as _cv2
        tc_frames_dir = out_dir / "tc_keyframes"
        tc_frames_dir.mkdir(parents=True, exist_ok=True)
        tc_kf_paths = []

        cap = _cv2.VideoCapture(str(video_path))
        tc_total = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
        if tc_total > 0:
            step = max(1, tc_total // tc_keyframes_count)
            indices = [min(i * step, tc_total - 1) for i in range(tc_keyframes_count)]
            for idx in indices:
                cap.set(_cv2.CAP_PROP_POS_FRAMES, idx)
                ret, frame = cap.read()
                if ret:
                    p = tc_frames_dir / f"frame_{idx:06d}.jpg"
                    _cv2.imwrite(str(p), frame)
                    tc_kf_paths.append(p)
        cap.release()

    overlap_review: OverlapReviewVerdict | None = None
    anchor_binding_review: AnchorBindingVerdict | None = None
    whole_video_visual_review_raw_response: str = ""
    visual_coverage: VisualCoverageVerdict | None = None
    semantic_coherence: SemanticCoherenceVerdict | None = None
    av_alignment_verdict: AVAlignmentVerdict | None = None
    has_audio_stream = audio_metrics is not None and audio_metrics.has_audio
    whole_video_media = WholeVideoMediaCache()

    if (not skip_tc or not cfg.skip_vlm) and (direct_video_enabled or tc_kf_paths):
        whole_video_media = prepare_whole_video_media_cache(
            video_path=video_path if direct_video_enabled else None,
            keyframe_paths=tc_kf_paths if tc_kf_paths else None,
        )

    stage_tasks = {}

    if not skip_tc:
        stage_tasks["task_correctness"] = lambda: review_task_correctness(
            topic=topic,
            vlm_cfg=cfg.vlm,
            video_name=video_name,
            video_path=video_path if direct_video_enabled else None,
            keyframe_paths=tc_kf_paths if tc_kf_paths else None,
            teaching_plan=teaching_plan,
            encoded_keyframes=whole_video_media.encoded_keyframes,
            encoded_video_input=whole_video_media.encoded_video_input,
        )

    if not cfg.skip_vlm and tc_kf_paths:
        print(f"\n[Layer 2b-2] Whole-video visual VLM review ({len(tc_kf_paths)} keyframes) ...")
        stage_tasks["whole_video_visual_review"] = lambda: review_whole_video_visual_keyframes(
            keyframe_paths=tc_kf_paths,
            vlm_cfg=cfg.vlm,
            video_name=video_name,
            encoded_keyframes=whole_video_media.encoded_keyframes,
        )

    if not cfg.skip_vlm and tc_kf_paths:
        if teaching_plan and teaching_plan.get("sections"):
            print(f"\n[Layer 2b-4] Visual coverage check ({len(teaching_plan['sections'])} sections) ...")
            stage_tasks["visual_coverage"] = lambda: review_visual_coverage(
                vlm_cfg=cfg.vlm,
                teaching_plan=teaching_plan,
                video_name=video_name,
                video_path=video_path if direct_video_enabled else None,
                keyframe_paths=tc_kf_paths if tc_kf_paths else None,
                encoded_keyframes=whole_video_media.encoded_keyframes,
                encoded_video_input=whole_video_media.encoded_video_input,
            )
        else:
            print("\n[Layer 2b-4] No teaching plan -> running semantic coherence check ...")
            stage_tasks["semantic_coherence"] = lambda: review_semantic_coherence(
                vlm_cfg=cfg.vlm,
                video_name=video_name,
                video_path=video_path if direct_video_enabled else None,
                keyframe_paths=tc_kf_paths if tc_kf_paths else None,
                encoded_keyframes=whole_video_media.encoded_keyframes,
                encoded_video_input=whole_video_media.encoded_video_input,
            )
    elif not cfg.skip_vlm:
        print("\n[Layer 2b-4] Whole-video semantic review skipped (no keyframes available)")

    if not skip_tc and not cfg.skip_vlm and has_audio_stream:
        if direct_video_enabled:
            print("\n[Layer 2c] AV Alignment MLLM review (direct video) ...")
        else:
            print("\n[Layer 2c] AV Alignment MLLM review (keyframes-only mode) ...")
        stage_tasks["av_alignment"] = lambda: review_av_alignment(
            vlm_cfg=cfg.vlm,
            video_name=video_name,
            video_path=video_path if direct_video_enabled else None,
            keyframe_paths=tc_kf_paths if tc_kf_paths else None,
            encoded_keyframes=whole_video_media.encoded_keyframes,
            encoded_video_input=whole_video_media.encoded_video_input,
        )
    elif not cfg.skip_vlm and not skip_tc:
        reason = "no audio" if not has_audio_stream else "VLM skipped"
        print(f"\n[Layer 2c] AV Alignment MLLM skipped ({reason})")

    def _handle_stage_result(stage_name: str, result) -> None:
        nonlocal task_correctness, anchor_binding_review, overlap_review, whole_video_visual_review_raw_response, visual_coverage, semantic_coherence, av_alignment_verdict
        if stage_name == "task_correctness":
            task_correctness = result
            print(f"  Content Accuracy: {'YES' if result.content_accuracy else 'NO'}")
            print(f"  Pedagogical Clarity: {result.pedagogical_clarity:.0%}")
            print(f"  Engagement: {result.engagement:.0%}")
        elif stage_name == "whole_video_visual_review":
            if not isinstance(result, WholeVideoVisualReviewResult):
                raise TypeError("whole_video_visual_review returned an unexpected result type")
            overlap_review = result.overlap_review
            anchor_binding_review = result.anchor_binding_review
            whole_video_visual_review_raw_response = result.raw_response
            overlap_status = "HAS OVERLAP" if overlap_review.has_overlap else "CLEAN"
            anchor_status = "HAS ISSUES" if anchor_binding_review.has_binding_issue else "CLEAN"
            print(
                f"  Overlap: {overlap_status} "
                f"(ratio={overlap_review.overlap_ratio:.3f}, issues={len(overlap_review.issues)})"
            )
            print(
                f"  Anchor Binding: {anchor_status} "
                f"(ratio={anchor_binding_review.binding_issue_ratio:.3f}, issues={len(anchor_binding_review.issues)})"
            )
        elif stage_name == "visual_coverage":
            visual_coverage = result
            print(f"  Coverage: {result.coverage_ratio:.0%}")
            if result.missing_sections:
                print(f"  Missing: {result.missing_sections}")
        elif stage_name == "semantic_coherence":
            semantic_coherence = result
            print(
                f"  Coherence: {result.score:.2f} "
                f"(topic={result.topic_consistency:.2f}, "
                f"progression={result.logical_progression:.2f}, "
                f"relevance={result.visual_relevance:.2f})"
            )
            print(f"  Reason: {result.reason}")
        elif stage_name == "av_alignment":
            av_alignment_verdict = result
            print(f"  Semantic Alignment: {result.semantic_alignment}/5")
            print(f"  Temporal Pacing:    {result.temporal_pacing}/5")
            print(f"  Narration Natural.: {result.narration_naturalness}/5")

    def _handle_stage_error(stage_name: str, exc: Exception) -> None:
        if stage_name == "task_correctness":
            print(f"  Task Correctness VLM error: {exc}")
        elif stage_name == "whole_video_visual_review":
            print(f"  Whole-video visual VLM review error: {exc}")
        elif stage_name == "visual_coverage":
            print(f"  Visual coverage error: {exc}")
        elif stage_name == "semantic_coherence":
            print(f"  Semantic coherence error: {exc}")
        elif stage_name == "av_alignment":
            print(f"  AV Alignment MLLM error: {exc}")

    if stage_tasks:
        max_workers = min(VLM_STAGE_MAX_WORKERS, len(stage_tasks))
        if max_workers <= 1:
            for stage_name, stage_fn in stage_tasks.items():
                try:
                    _handle_stage_result(stage_name, stage_fn())
                except Exception as exc:
                    _handle_stage_error(stage_name, exc)
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(stage_fn): stage_name for stage_name, stage_fn in stage_tasks.items()}
                for future in as_completed(futures):
                    stage_name = futures[future]
                    try:
                        _handle_stage_result(stage_name, future.result())
                    except Exception as exc:
                        _handle_stage_error(stage_name, exc)

    # ------------------------------------------------------------------
    # Layer 3: Fusion scoring
    # ------------------------------------------------------------------
    print("\n[Layer 3] Computing fusion scores ...")

    report = compute_report(
        video_name=video_name,
        global_cv=global_cv,
        segments=segment_features_list,
        verdicts=verdicts,
        fusion_cfg=cfg.fusion,
        audio_metrics=audio_metrics,
        alignment_metrics=alignment_metrics,
        task_correctness=task_correctness,
        av_alignment_verdict=av_alignment_verdict,
        whole_video_visual_review_raw_response=whole_video_visual_review_raw_response,
        anchor_binding_review=anchor_binding_review,
        overlap_review=overlap_review,
        visual_coverage=visual_coverage,
        semantic_coherence=semantic_coherence,
        meta=cfg.meta,
    )

    save_report_json(report, out_dir / "report.json")
    print_report(report)

    return {
        "video": video_name,
        "overall_score": report.overall_score,
        "overall_passed": report.overall_passed,
        "output_dir": str(out_dir),
    }


# =====================================================================
# Batch entry
# =====================================================================

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = build_config(args)

    results: list[dict] = []
    for video_path in args.inputs:
        if not video_path.exists():
            print(f"WARNING: {video_path} not found, skipping.", file=sys.stderr)
            continue
        result = evaluate_video(video_path, cfg)
        results.append(result)

    # Summary for batch mode
    if len(results) > 1:
        print(f"\n{'='*60}")
        print(f"  BATCH SUMMARY ({len(results)} videos)")
        print(f"{'='*60}")
        for r in results:
            status = "PASS" if r["overall_passed"] else "FAIL"
            print(f"  [{status}] {r['overall_score']:.2f}  {r['video']}")
        avg = sum(r["overall_score"] for r in results) / len(results)
        print(f"\n  Average score: {avg:.2f}")

    # Save batch summary
    if results:
        summary_path = cfg.output_dir / "batch_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
